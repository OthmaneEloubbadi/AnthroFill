#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 GENDER REVIEW PIPELINE FOR SQL SERVER (NO AUTO-WRITES TO STAGING)
==============================================================================
 - Creates a fresh _STAGING snapshot from the source table.
 - Builds a review-oriented _AUDIT table.
 - Reads data, runs fuzzy matching, and writes flagged rows to audit only.
 - Does NOT automatically update or enrich the staging rows with gender values.
==============================================================================
"""

import argparse
import csv
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

# Forces stdout to flush line-by-line even when it's piped (e.g. by Flask's
# subprocess call) instead of connected to a real terminal. Without this,
# prints can sit in a buffer and never reach the parent process's console
# until the script exits.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

# --- HEARTBEAT -------------------------------------------------------------
# Prints "still alive" progress every 3 seconds from a background thread, so
# whoever is watching the console (e.g. the terminal running `python app.py`)
# can tell the script is genuinely progressing rather than stuck, even during
# long single steps (big SELECT INTO, index creation) that have no natural
# place to print per-iteration progress.
_etat_execution = {"etape": "Starting up", "debut": time.time(), "arret": False}


def _definir_etape(texte):
    _etat_execution["etape"] = texte


def _boucle_heartbeat():
    while not _etat_execution["arret"]:
        time.sleep(3)
        if _etat_execution["arret"]:
            break
        ecoule = time.time() - _etat_execution["debut"]
        print(
            f"[HEARTBEAT] Still running - current step: {_etat_execution['etape']} "
            f"({ecoule:.0f}s elapsed total)",
            flush=True,
        )


def _demarrer_heartbeat():
    thread = threading.Thread(target=_boucle_heartbeat, daemon=True)
    thread.start()
    return thread


def _arreter_heartbeat():
    _etat_execution["arret"] = True

try:
    import pyodbc
except ImportError:
    print("ERROR: 'pyodbc' library is missing. Install it using: pip install pyodbc")
    sys.exit(1)

try:
    from rapidfuzz import process, fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    from difflib import get_close_matches, SequenceMatcher
    HAS_RAPIDFUZZ = False

import gender_engine


NULL_LIKE_VALUES = {
    "",
    "null",
    "none",
    "n/a",
    "na",
    "nan",
    "nd",
    "n.d",
    "unknown",
    "undefined",
    "vide",
    "empty",
    "-",
    "--",
    "?",
}

# Set this to False to skip creating/rebuilding the _AUDIT table entirely
# (and skip the row-by-row evaluation + insert that feeds it). Everything
# else -- STAGING snapshot, rewind snapshot, index creation -- still runs
# normally either way.
AUDIT_CREATE = False

# Must match REFERENCE_TABLE in app.py so both stay in sync with the same
# live SQL table instead of drifting apart via a stale local CSV export.
REFERENCE_TABLE = 'dbo.[reference_prenom]'


def normaliser(texte):
    if texte is None:
        return ""
    texte = str(texte)
    texte = texte.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
    texte = texte.strip().lower()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = re.sub(r"[^a-z\s\-'/\.]", "", texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte


def est_valeur_nulle(valeur):
    if valeur is None:
        return True
    brut = str(valeur).strip()
    if brut == "":
        return True
    normalise = normaliser(brut).replace("/", "").replace(".", "")
    return normalise in {v.replace("/", "").replace(".", "") for v in NULL_LIKE_VALUES}


def valeur_affichable(valeur):
    if valeur is None or str(valeur).strip() == "":
        return "<EMPTY>"
    return str(valeur)


def extraire_jetons_nom(texte):
    nom = normaliser(texte)
    if not nom:
        return []
    return [j.strip("'") for j in re.split(r"[\s\-']+", nom) if j.strip("'")]


def diviser_prenom_depuis_nom(nom_brut):
    if nom_brut is None:
        return None, None
    brut = str(nom_brut).strip()
    if not brut:
        return None, None

    jetons = brut.split()
    if not jetons:
        return None, None

    prenom_derive = jetons[-1]
    reste = jetons[:-1]
    nouveau_nom = " ".join(reste) if reste else ""

    return nouveau_nom, prenom_derive


class MoteurDetectionGenre:
    """Thin wrapper around gender_engine.GenderReference, kept so the rest of
    this script (argument parsing, SQL Server plumbing, audit-table writing)
    doesn't need to change. The actual matching logic -- majority-vote
    reference loading, particle/honorific awareness, the Abd-compound rule,
    bidirectional multi-token scanning and the collapsed-letters phonetic
    fallback -- now lives in gender_engine.py so app.py and this script stay
    in sync instead of drifting apart with two separate implementations."""

    def __init__(self, dossier_reference, cursor=None):
        dossier_reference = Path(dossier_reference)
        self.engine = gender_engine.GenderReference()

        print(f"[INFO] Reference Engine RapidFuzz Active: {HAS_RAPIDFUZZ}")

        if cursor is not None:
            self._charger_reference_sql(cursor)
        else:
            # Fallback for standalone use with no live DB cursor available.
            self._charger_reference_csv(dossier_reference / "reference_prenom.csv")
        self._charger_variantes(dossier_reference / "variantes_noms.csv")
        self.engine.finalize()

    def _charger_reference_sql(self, cursor):
        """Loads NormalizedName/Gender straight from the live SQL table,
        mirroring app.py's load_gender_reference_engine() -- so this script
        never drifts out of sync with whatever's currently in
        dbo.reference_prenom (e.g. manual corrections made since the last
        CSV export)."""
        print(f"[INFO] Loading reference data live from {REFERENCE_TABLE}...")
        try:
            cursor.execute(f"SELECT NormalizedName, Gender FROM {REFERENCE_TABLE}")
            lignes = cursor.fetchall()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load reference data from {REFERENCE_TABLE}: {exc}"
            ) from exc

        for nom_brut, genre_brut in lignes:
            self.engine.add_observation(nom_brut, genre_brut)
        print(f"[INFO] Loaded {len(lignes)} reference rows from {REFERENCE_TABLE}.")

    def _charger_reference_csv(self, chemin):
        if not chemin.exists():
            chemin_alt = chemin.parent / "reference_noms.csv"
            if chemin_alt.exists():
                chemin = chemin_alt
            else:
                raise FileNotFoundError(f"Reference file not found: {chemin}")

        try:
            with open(chemin, encoding="utf-8-sig") as fichier:
                self._lire_reference_fichier(fichier)
        except UnicodeDecodeError:
            with open(chemin, encoding="latin-1") as fichier:
                self._lire_reference_fichier(fichier)

    def _lire_reference_fichier(self, fichier):
        for ligne in csv.DictReader(fichier):
            nom_brut = (
                ligne.get("NormalizedName")
                or ligne.get("PRENOM")
                or ligne.get("prenom")
                or ligne.get("name")
            )
            genre_brut = (
                ligne.get("Gender")
                or ligne.get("GENRE")
                or ligne.get("genre")
            )
            self.engine.add_observation(nom_brut, genre_brut)

    def _charger_variantes(self, chemin):
        if not chemin.exists():
            return

        def _lire(fichier):
            for ligne in csv.DictReader(fichier):
                v_brut = (
                    ligne.get("Variante")
                    or ligne.get("variante")
                    or ligne.get("variant")
                )
                c_brut = (
                    ligne.get("NomCanonique")
                    or ligne.get("nom_canonique")
                    or ligne.get("canonical")
                )
                self.engine.add_variant(v_brut, c_brut)

        try:
            with open(chemin, encoding="utf-8-sig") as fichier:
                _lire(fichier)
        except UnicodeDecodeError:
            with open(chemin, encoding="latin-1") as fichier:
                _lire(fichier)

    def deviner(self, prenom_brut, seuil_score=90):
        """Kept for backward compatibility: guesses from a PRENOM value alone
        (no NOM fallback). Still benefits from the new engine's honorific /
        Abd-rule / collapsed-letters / bidirectional compound-token handling."""
        guess = gender_engine.guess_from_tokens(
            gender_engine.tokenize(prenom_brut),
            self.engine,
            allow_fuzzy=True,
            fuzzy_threshold=seuil_score,
        )
        return self._format(guess)

    def deviner_complet(self, prenom_brut, nom_brut, seuil_score=90):
        """Full guess covering every real-world layout in one call:
          - PRENOM filled -> scan its own tokens (compound first names, e.g.
            'Mohammed Amine', both parts tried).
          - PRENOM filled but no hit -> FALL BACK to scanning NOM (front and
            back), instead of silently giving up.
          - PRENOM empty, NOM holds the full name -> bidirectional scan of
            every word in NOM (skipping ben/el/al/ould/ait/... connectors),
            not just "take the last word" like before.
        Returns (genre_texte_or_None, fiabilite_str, correspondance_or_None,
        source_str, ambiguous_bool)."""
        guess = gender_engine.guess_gender(
            prenom_value=prenom_brut,
            nom_value=nom_brut,
            reference=self.engine,
            allow_fuzzy=True,
            fuzzy_threshold=seuil_score,
        )
        genre_texte, fiabilite, correspondance = self._format(guess)
        return genre_texte, fiabilite, correspondance, guess.get('source'), guess['ambiguous']

    @staticmethod
    def _format(guess):
        genre = guess.get('gender')
        reason = guess.get('reason', '')
        confidence_pct = round((guess.get('confidence') or 0) * 100)
        if guess.get('ambiguous'):
            return None, f"AMBIGUOUS - {reason}", None
        if not genre:
            return None, f"0% UNDETERMINED - {reason}", None
        return genre, f"{confidence_pct}% {reason}", reason


ALIAS_PRENOM = ["prenom", "prénom", "first_name", "firstname", "nom_prenom"]
ALIAS_NOM = ["nom", "last_name", "lastname", "family_name", "surname"]
ALIAS_GENRE = ["genre", "gender", "sexe"]


def trouver_colonne(en_tetes, alias_possibles):
    en_tetes_norm = {h: normaliser(h).replace(" ", "_") for h in en_tetes}
    for header, header_norm in en_tetes_norm.items():
        for alias in alias_possibles:
            if header_norm == alias or header_norm == alias.replace("_", ""):
                return header
    return None


def quote_ident(name: str) -> str:
    name = str(name)
    return "[" + name.replace("]", "]]") + "]"


def parse_table_name(table_name: str):
    raw = str(table_name).strip().replace("[", "").replace("]", "")
    parts = [p for p in raw.split(".") if p]
    if len(parts) == 1:
        return "dbo", parts[0]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "dbo", raw


def build_full_table(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def sqlserver_type_from_info_schema_row(data_type, char_max_len, num_prec, num_scale, dt_prec):
    if not data_type:
        return "NVARCHAR(255)"

    dt = str(data_type).strip().lower()

    if dt in ("varchar", "nvarchar", "char", "nchar", "varbinary", "binary"):
        if char_max_len is None:
            return f"{dt}(255)"
        try:
            length = int(char_max_len)
        except Exception:
            return f"{dt}(255)"
        if length == -1:
            return f"{dt}(max)"
        return f"{dt}({length})"

    if dt in ("decimal", "numeric"):
        if num_prec is None or num_scale is None:
            return dt
        return f"{dt}({int(num_prec)},{int(num_scale)})"

    if dt in ("datetime2", "datetimeoffset", "time"):
        if dt_prec is None:
            return dt
        return f"{dt}({int(dt_prec)})"

    return dt


def get_column_type_definition(cursor, table_schema: str, table_name: str, column_name: str) -> str:
    cursor.execute(
        """
        SELECT
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            DATETIME_PRECISION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?;
        """,
        (table_schema, table_name, column_name),
    )
    row = cursor.fetchone()
    if not row:
        return "NVARCHAR(255)"
    return sqlserver_type_from_info_schema_row(row[0], row[1], row[2], row[3], row[4])


def safe_index_name(base: str, max_len: int = 128) -> str:
    base = re.sub(r"[^0-9A-Za-z_]+", "_", str(base))
    if len(base) <= max_len:
        return base
    hachage = abs(hash(base)) % (10**10)
    keep = max_len - (len(str(hachage)) + 2)
    return f"{base[:keep]}_{hachage}"


def Obtenir_Connexion_SQL(args):
    """
    Connects to SQL Server. First attempts connecting using the provided arguments.
    If connecting to a local address (localhost/127.0.0.1) fails because it is a named 
    instance (SQLEXPRESS), it automatically retries targeting .\\SQLEXPRESS.
    """
    driver = f"{{{args.driver}}}"
    
    # 1. Build authentication string based on command line flags
    if args.trusted_connection:
        auth_str = "Trusted_Connection=yes;"
    else:
        auth_str = f"UID={args.utilisateur};PWD={args.mot_de_passe};"

    # 2. Add SSL/Encryption compatibility flags required by modern ODBC drivers
    ssl_flags = "Encrypt=no;TrustServerCertificate=yes;"

    # Primary connection string using the passed CLI arguments
    conn_str = (
        f"DRIVER={driver};"
        f"SERVER={args.serveur};"
        f"DATABASE={args.base_donnees};"
        f"{auth_str}"
        f"{ssl_flags}"
    )

    print(f"[INFO] Connecting to SQL Server: {args.serveur} | Database: {args.base_donnees}...")

    try:
        return pyodbc.connect(conn_str)
    except pyodbc.Error as err:
        # Fallback: If '127.0.0.1' or 'localhost' failed, retry using local Express instance syntax
        if args.serveur in ['127.0.0.1', 'localhost', 'localhost,1433']:
            print(f"[WARNING] Primary connection to '{args.serveur}' failed. Retrying with '.\\SQLEXPRESS'...")
            
            fallback_conn_str = (
                f"DRIVER={driver};"
                f"SERVER=.\\SQLEXPRESS;"
                f"DATABASE={args.base_donnees};"
                f"{auth_str}"
                f"{ssl_flags}"
            )
            try:
                return pyodbc.connect(fallback_conn_str)
            except pyodbc.Error:
                pass  # Re-raise the original error below if fallback also fails
                
        raise err


def main():
    parser = argparse.ArgumentParser(
        description="Process SQL Server gender records with fuzzy lookup and review-oriented audit output."
    )

    parser.add_argument("--serveur", required=True, help="SQL Server Name / Host IP")
    parser.add_argument("--base-donnees", required=True, help="Database Name")
    parser.add_argument(
        "--table",
        required=True,
        help="Target SQL Server table name (e.g., high_sample or dbo.high_sample)",
    )
    parser.add_argument(
        "--colonne-pk",
        required=True,
        help="Primary Key column name (e.g., ID, Client_ID)",
    )

    parser.add_argument(
        "--trusted-connection", action="store_true", help="Use Windows Authentication"
    )
    parser.add_argument(
        "--utilisateur", default="", help="SQL Username (if not using trusted connection)"
    )
    parser.add_argument(
        "--mot-de-passe", default="", help="SQL Password (if not using trusted connection)"
    )
    parser.add_argument(
        "--driver", default="ODBC Driver 17 for SQL Server", help="ODBC Driver Name"
    )

    parser.add_argument(
        "--dossier-reference",
        default=".",
        help="Local folder containing reference CSV files",
    )
    parser.add_argument(
        "--seuil-fuzzy",
        type=int,
        default=90,
        help="Fuzzy matching threshold (default: 90)",
    )

    args = parser.parse_args()

    schema_source, table_source_name = parse_table_name(args.table)
    col_pk = args.colonne_pk

    table_staging_name = f"{table_source_name}_STAGING"
    table_audit_name = f"{table_source_name}_AUDIT"

    table_source_full = build_full_table(schema_source, table_source_name)
    table_staging_full = build_full_table(schema_source, table_staging_name)
    table_audit_full = build_full_table(schema_source, table_audit_name)

    table_source_for_object_id = f"{schema_source}.{table_source_name}"
    table_staging_for_object_id = f"{schema_source}.{table_staging_name}"
    table_audit_for_object_id = f"{schema_source}.{table_audit_name}"

    _demarrer_heartbeat()
    _definir_etape("Connecting to SQL Server")

    conn = Obtenir_Connexion_SQL(args)
    cursor = conn.cursor()

    _definir_etape(f"Creating staging snapshot '{table_staging_for_object_id}'")
    print(f"[INFO] Creating staging snapshot: '{table_staging_for_object_id}'...")
    cursor.execute(
        f"IF OBJECT_ID(?, 'U') IS NOT NULL DROP TABLE {table_staging_full};",
        (table_staging_for_object_id,),
    )
    cursor.execute(f"SELECT * INTO {table_staging_full} FROM {table_source_full};")
    conn.commit()

    # --- REWIND SNAPSHOT ---
    # Everything downstream (auto-fill, manual saves, audit confirmations) only ever
    # writes to table_staging_full's GENRE column -- the pipeline above never touches
    # it. So the instant STAGING is (re)built from the source table is exactly the
    # "clean slate" state a user wants to get back to after testing auto-fill/manual
    # edits, without re-running this whole (slow) script.
    #
    # We capture that clean slate here as a second, untouched copy of STAGING. This
    # is a pure in-database bulk copy (SELECT INTO), so it costs seconds even on a
    # large table -- nothing like the minutes spent below on row-by-row fuzzy
    # matching. The Flask app's /api/v1/rewind endpoint later restores STAGING from
    # this snapshot and wipes the AUTO_HISTORY/MANUAL_HISTORY tables, all in a few
    # seconds, instead of the user manually dropping tables and re-running this
    # script from scratch.
    table_snapshot_name = f"{table_source_name}_STAGING_SNAPSHOT"
    table_snapshot_full = build_full_table(schema_source, table_snapshot_name)
    table_snapshot_for_object_id = f"{schema_source}.{table_snapshot_name}"

    _definir_etape(f"Saving rewind snapshot '{table_snapshot_for_object_id}'")
    print(f"[INFO] Saving rewind snapshot: '{table_snapshot_for_object_id}'...")
    cursor.execute(
        f"IF OBJECT_ID(?, 'U') IS NOT NULL DROP TABLE {table_snapshot_full};",
        (table_snapshot_for_object_id,),
    )
    cursor.execute(f"SELECT * INTO {table_snapshot_full} FROM {table_staging_full};")
    conn.commit()

    _definir_etape(f"Creating index on staging table '{table_staging_for_object_id}'")
    print(f"[INFO] Creating index on staging table '{table_staging_for_object_id}'...")
    try:
        idx_name = safe_index_name(f"IX_{table_staging_name}_{col_pk}")
        cursor.execute(
            f"CREATE INDEX {quote_ident(idx_name)} ON {table_staging_full}({quote_ident(col_pk)});"
        )
        conn.commit()
    except Exception as exc:
        print(f"[INFO] Index creation status: {exc}")

    cursor.execute(f"SELECT TOP 1 * FROM {table_staging_full}")
    colonnes_existantes = [column[0] for column in cursor.description]

    col_nom = trouver_colonne(colonnes_existantes, ALIAS_NOM)
    col_prenom = trouver_colonne(colonnes_existantes, ALIAS_PRENOM)
    col_genre_orig = trouver_colonne(colonnes_existantes, ALIAS_GENRE)

    if not col_prenom or not col_genre_orig:
        print(
            f"ERROR: Could not auto-detect PRENOM or GENRE column names in '{table_staging_for_object_id}'. "
            f"Available columns: {colonnes_existantes}"
        )
        sys.exit(1)

    print(
        f"[INFO] Dynamic Column Mapping: PK='{col_pk}', Last Name='{col_nom}', First Name='{col_prenom}', Gender='{col_genre_orig}'"
    )

    nom_avant_prenom = False
    if col_nom and col_prenom:
        try:
            nom_avant_prenom = colonnes_existantes.index(col_nom) < colonnes_existantes.index(col_prenom)
        except ValueError:
            nom_avant_prenom = False

    if AUDIT_CREATE:
        _definir_etape(f"Creating audit table '{table_audit_for_object_id}'")
        print(f"[INFO] Creating audit table: '{table_audit_for_object_id}'...")
        cursor.execute(
            f"IF OBJECT_ID(?, 'U') IS NOT NULL DROP TABLE {table_audit_full};",
            (table_audit_for_object_id,),
        )

        pk_type = get_column_type_definition(cursor, schema_source, table_source_name, col_pk)

        nom_prenom_cols_sql = (
            "NOM NVARCHAR(255),\n        PRENOM NVARCHAR(255),"
            if nom_avant_prenom
            else "PRENOM NVARCHAR(255),\n        NOM NVARCHAR(255),"
        )

        sql_create_audit = f"""
        CREATE TABLE {table_audit_full} (
            {quote_ident(col_pk)} {pk_type},
            {nom_prenom_cols_sql}
            genre_original NVARCHAR(100),
            genre_correct NVARCHAR(100),
            reason NVARCHAR(2000)
        );
        """
        cursor.execute(sql_create_audit)
        conn.commit()

        _definir_etape("Loading reference data (live SQL) and building matching engine")
        moteur = MoteurDetectionGenre(args.dossier_reference, cursor=cursor)

        select_nom = quote_ident(col_nom) if col_nom else "NULL AS NOM"
        query_fetch = (
            f"SELECT {quote_ident(col_pk)}, {select_nom}, {quote_ident(col_prenom)}, {quote_ident(col_genre_orig)} "
            f"FROM {table_staging_full}"
        )
        _definir_etape("Fetching rows from staging table")
        cursor.execute(query_fetch)
        lignes = cursor.fetchall()

        enregistrements_audit_insert = []

        compteur_nulls_origine = 0
        compteur_nulls_corriges = 0
        compteur_nulls_restants = 0
        compteur_mismatches = 0
        compteur_noms_scindes = 0
        compteur_ambigus = 0

        print(f"[INFO] Evaluating {len(lignes)} rows against live SQL reference data...")

        total_lignes = len(lignes)
        for index_ligne, row in enumerate(lignes, start=1):
            _definir_etape(f"Evaluating rows ({index_ligne}/{total_lignes})")
            pk_val = row[0]
            nom_val = row[1]
            prenom = row[2]
            genre_orig = row[3]

            prenom_etait_vide = est_valeur_nulle(prenom) and not est_valeur_nulle(nom_val)

            # deviner_complet() covers every layout in one call:
            #   - PRENOM filled -> tries PRENOM's own tokens first (compound
            #     first names included), falling back to NOM (front AND back)
            #     if PRENOM alone finds nothing.
            #   - PRENOM empty  -> bidirectional scan across every word of NOM
            #     (particles like ben/el/al/ould/ait skipped), not just the
            #     last word.
            genre_predit, fiabilite, _correspondance, source_utilise, est_ambigu = moteur.deviner_complet(
                prenom, nom_val, seuil_score=args.seuil_fuzzy
            )

            nom_split_effectue = bool(source_utilise) and (
                prenom_etait_vide or (source_utilise or '').startswith('nom')
            )
            if nom_split_effectue:
                compteur_noms_scindes += 1
            if est_ambigu:
                compteur_ambigus += 1

            orig_is_null = est_valeur_nulle(genre_orig)
            norm_orig = "" if orig_is_null else normaliser(genre_orig)
            norm_predit = normaliser(genre_predit) if genre_predit else ""

            if orig_is_null:
                compteur_nulls_origine += 1

            is_mismatch = bool(norm_predit and not orig_is_null and norm_orig != norm_predit)

            if is_mismatch or orig_is_null or nom_split_effectue or est_ambigu:
                genre_correct_val = genre_predit if genre_predit else ""
                reason_parts = []

                if nom_split_effectue:
                    reason_parts.append(
                        f"Gender guessed from NOM instead of PRENOM (source: {source_utilise}) "
                        f"-- PRENOM='{valeur_affichable(prenom)}', NOM='{valeur_affichable(nom_val)}'"
                    )

                if est_ambigu:
                    reason_parts.append(
                        f"AMBIGUOUS: front and back of the name disagree on gender -- needs manual review "
                        f"[{fiabilite}]"
                    )

                if is_mismatch:
                    compteur_mismatches += 1
                    reason_parts.append(
                        f"Original '{valeur_affichable(genre_orig)}' != Suggested '{genre_predit}' "
                        f"[{fiabilite}]"
                    )
                elif orig_is_null and genre_predit:
                    compteur_nulls_corriges += 1
                    reason_parts.append(
                        f"Original was NULL-like ('{valeur_affichable(genre_orig)}') -> "
                        f"Suggested '{genre_predit}' [{fiabilite}]"
                    )
                elif orig_is_null:
                    compteur_nulls_restants += 1
                    reason_parts.append(
                        f"Original was NULL-like ('{valeur_affichable(genre_orig)}') -> "
                        f"Unrecognized name [{fiabilite}]"
                    )

                reason = " | ".join(reason_parts) if reason_parts else "Review required."

                enregistrements_audit_insert.append(
                    (pk_val, prenom, nom_val, genre_orig, genre_correct_val, reason)
                )

        cursor.fast_executemany = True

        if enregistrements_audit_insert:
            _definir_etape(
                f"Inserting {len(enregistrements_audit_insert)} flagged audit records"
            )
            print(
                f"[INFO] Inserting {len(enregistrements_audit_insert)} flagged audit records into "
                f"'{table_audit_for_object_id}'..."
            )

            cleaned_audit_data = []
            for audit_row in enregistrements_audit_insert:
                pk_val, prenom_val, nom_val, genre_orig_val, genre_correct_val, reason_val = audit_row

                prenom_clean = str(prenom_val)[:250] if prenom_val is not None else ""
                nom_clean = str(nom_val)[:250] if nom_val is not None else ""
                genre_orig_clean = str(genre_orig_val)[:95] if genre_orig_val is not None else ""
                genre_correct_clean = str(genre_correct_val)[:95] if genre_correct_val is not None else ""
                reason_clean = str(reason_val)[:1950] if reason_val is not None else ""

                noms_prenoms = (
                    (nom_clean, prenom_clean) if nom_avant_prenom else (prenom_clean, nom_clean)
                )

                cleaned_audit_data.append((
                    pk_val,
                    noms_prenoms[0],
                    noms_prenoms[1],
                    genre_orig_clean,
                    genre_correct_clean,
                    reason_clean,
                ))

            col_order = ["NOM", "PRENOM"] if nom_avant_prenom else ["PRENOM", "NOM"]
            cols_sql = ", ".join(
                [quote_ident(col_pk)] + col_order + ["genre_original", "genre_correct", "reason"]
            )
            sql_insert_audit = (
                f"INSERT INTO {table_audit_full} ({cols_sql}) VALUES (?, ?, ?, ?, ?, ?)"
            )
            cursor.executemany(sql_insert_audit, cleaned_audit_data)
            conn.commit()
    else:
        _definir_etape("Skipping audit table (AUDIT_CREATE is False)")
        print(f"[INFO] AUDIT_CREATE is False -- skipping creation of '{table_audit_for_object_id}' and the row evaluation/insert that feeds it.")
        lignes = []
        compteur_nulls_origine = 0
        compteur_nulls_corriges = 0
        compteur_nulls_restants = 0
        compteur_mismatches = 0
        compteur_noms_scindes = 0
        compteur_ambigus = 0

    cursor.close()
    conn.close()
    _arreter_heartbeat()

    print("\n================ PROCESSING SUMMARY ================")
    print(f"Total Rows Evaluated: {len(lignes)}")
    print(f"Gender Guessed From NOM (empty PRENOM or PRENOM had no match): {compteur_noms_scindes}")
    print(f"Ambiguous Rows (front/back of name disagree, flagged for manual review): {compteur_ambigus}")
    print(f"Original NULL-like Rows Detected: {compteur_nulls_origine}")
    print(f"NULL-like Rows With Suggestions: {compteur_nulls_corriges}")
    print(f"NULL-like Rows Still Unresolved: {compteur_nulls_restants}")
    print(f"Non-Null Mismatched Rows Detected: {compteur_mismatches}")
    print(f"Staging Snapshot Refreshed: '{table_staging_for_object_id}'")
    print(f"Rewind Snapshot Saved: '{table_snapshot_for_object_id}'")
    if AUDIT_CREATE:
        print(f"Audit Table Generated: '{table_audit_for_object_id}'")
    else:
        print(f"Audit Table Skipped (AUDIT_CREATE=False): '{table_audit_for_object_id}' was NOT created/touched")
    print("No automatic gender updates were written to the staging table.")
    print("Use the 'Rewind' button in the portal to reset STAGING/history back to")
    print("this point at any time, instead of re-running this script.")
    print("====================================================")


if __name__ == "__main__":
    main()