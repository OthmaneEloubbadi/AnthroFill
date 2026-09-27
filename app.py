import os
import re
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
import pyodbc

import gender_engine

try:
    from rapidfuzz import fuzz, process
    HAS_RAPIDFUZZ = True
except ImportError:
    from difflib import SequenceMatcher
    HAS_RAPIDFUZZ = False


app = Flask(__name__, static_folder='.', static_url_path='')
app.config['JSON_SORT_KEYS'] = False
CORS(app)

NULL_LIKE_VALUES = {
    '',
    'null',
    'none',
    'n/a',
    'na',
    'nan',
    'nd',
    'n.d',
    'unknown',
    'undefined',
    'vide',
    'empty',
    '-',
    '--',
    '?',
    'inconnu',
}
# name_variantes
ALIAS_PRENOM = ['prenom', 'prénom', 'first_name', 'firstname', 'first name']
ALIAS_NOM = ['nom', 'last_name', 'lastname', 'family_name', 'surname','last name']
ALIAS_GENRE = ['genre', 'gender', 'sexe']
ALIAS_NOM_COMPLET = [
    'nom_complet',
    'nomcomplet',
    'nom complet',
    'full_name',
    'fullname',
    'full name',
    'nom_et_prenom',
    'nometprenom',
    'identite',
    'identité',
    'libelle_nom',
]

MIN_FUZZY_GROUP_LEN = 5

# The gender reference dataset now lives as a real table inside the same SQL
# Server database we already connect to for staging/audit/history tables --
# it is NOT a local CSV/Excel file anymore. Columns: NormalizedName, Gender.
REFERENCE_TABLE = 'dbo.[reference_prenom]'

# SQL Server caps a single statement at 2100 parameters. Any UPDATE/DELETE
# that builds a `WHERE pk IN (?, ?, ...)` list from a batch of row ids must
# chunk that list rather than sending it all in one call, or large batches
# will fail with "too many parameters". 1000 leaves headroom for any extra
# bound parameters (e.g. the value being SET) alongside the id list.
def chunked(seq, size=1000):
    """Yield successive slices of seq no longer than size."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def get_db_connection(server, database, user=None, password=None):
    """Dynamic connection helper supporting SQL Server Auth and Windows Auth."""
    if user and password:
        conn_str = (
            f'DRIVER={{SQL Server}};'
            f'SERVER={server};'
            f'DATABASE={database};'
            f'UID={user};'
            f'PWD={password};'
        )
    else:
        conn_str = (
            f'DRIVER={{SQL Server}};'
            f'SERVER={server};'
            f'DATABASE={database};'
            'Trusted_Connection=yes;'
        )
    return pyodbc.connect(conn_str)


def resolve_staging_table(table_name):
    """Cleanly constructs target staging table name."""
    if not table_name:
        return 'dbo.[high_sample_STAGING]'

    clean_name = (
        table_name.replace('dbo.', '').replace('[', '').replace(']', '').strip()
    )
    if clean_name.lower().endswith('_staging'):
        return f'dbo.[{clean_name}]'

    return f'dbo.[{clean_name}_STAGING]'


def resolve_audit_table(table_name):
    """Cleanly constructs target audit table name."""
    if not table_name:
        return 'dbo.[high_sample_AUDIT]'

    clean_name = (
        table_name.replace('dbo.', '').replace('[', '').replace(']', '').strip()
    )
    if clean_name.lower().endswith('_audit'):
        return f'dbo.[{clean_name}]'

    return f'dbo.[{clean_name}_AUDIT]'


def resolve_staging_snapshot_table(table_name):
    """Cleanly constructs the rewind-snapshot table name (built by db_genreFINAL.py
    right after it (re)creates the STAGING table from the source table)."""
    if not table_name:
        return 'dbo.[high_sample_STAGING_SNAPSHOT]'

    clean_name = (
        table_name.replace('dbo.', '').replace('[', '').replace(']', '').strip()
    )
    if clean_name.lower().endswith('_staging'):
        clean_name = clean_name[: -len('_staging')]
    if clean_name.lower().endswith('_staging_snapshot'):
        return f'dbo.[{clean_name}]'

    return f'dbo.[{clean_name}_STAGING_SNAPSHOT]'


def resolve_manual_history_table(table_name):
    """Cleanly constructs target manual history table name."""
    if not table_name:
        return 'dbo.[high_sample_MANUAL_HISTORY]'

    clean_name = (
        table_name.replace('dbo.', '').replace('[', '').replace(']', '').strip()
    )
    if clean_name.lower().endswith('_manual_history'):
        return f'dbo.[{clean_name}]'

    return f'dbo.[{clean_name}_MANUAL_HISTORY]'


def get_column_sql_type(cursor, table_name, column_name):
    """Looks up column_name's real SQL type on table_name (schema.[table] or
    [table]) via INFORMATION_SCHEMA.COLUMNS, so a history table's 'id' column
    can be created with the SAME type as the original table's id -- instead of
    guessing INT. Falls back to NVARCHAR(255) if the column can't be found."""
    clean = table_name.replace('dbo.', '').replace('[', '').replace(']', '').strip()
    schema = 'dbo'
    if '.' in clean:
        schema, clean = clean.rsplit('.', 1)
    cursor.execute(
        """
        SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?
        """,
        (schema, clean, column_name),
    )
    row = cursor.fetchone()
    if not row:
        return 'NVARCHAR(255)'
    data_type, char_len, num_prec, num_scale = row
    data_type = (data_type or '').lower()
    if data_type in ('varchar', 'nvarchar', 'char', 'nchar'):
        length = 'MAX' if char_len == -1 else char_len
        return f'{data_type.upper()}({length})'
    if data_type in ('decimal', 'numeric'):
        return f'{data_type.upper()}({num_prec},{num_scale})'
    return data_type.upper() or 'NVARCHAR(255)'


def ensure_manual_history_table(cursor, manual_table, pk_type='INT'):
    """Creates the manual history table dynamically if it does not exist.

    The 'id' column stores the ORIGINAL source/staging table's row id (passed
    in by the caller) rather than an auto-generated IDENTITY value, so every
    history row can be traced back to the exact record it came from. It is
    intentionally not a PRIMARY KEY/UNIQUE, since the same original row can be
    logged more than once over time.
    """
    create_sql = f"""
        IF OBJECT_ID('{manual_table}', 'U') IS NULL
        BEGIN
            CREATE TABLE {manual_table} (
                id {pk_type},
                PRENOM NVARCHAR(255),
                NOM NVARCHAR(255),
                genre_correct NVARCHAR(100),
                created_at DATETIME DEFAULT GETDATE()
            );
        END
    """
    cursor.execute(create_sql)


def resolve_auto_history_table(table_name):
    """Cleanly constructs target auto-fill history table name."""
    if not table_name:
        return 'dbo.[high_sample_AUTO_HISTORY]'

    clean_name = (
        table_name.replace('dbo.', '').replace('[', '').replace(']', '').strip()
    )
    if clean_name.lower().endswith('_auto_history'):
        return f'dbo.[{clean_name}]'

    return f'dbo.[{clean_name}_AUTO_HISTORY]'


def ensure_auto_history_table(cursor, auto_table, pk_type='INT'):
    """Creates the auto-fill history table dynamically if it does not exist.

    The 'id' column stores the ORIGINAL staging table's row id (the same id
    that was just updated), not an auto-generated IDENTITY value -- see
    ensure_manual_history_table for the same reasoning.
    """
    create_sql = f"""
        IF OBJECT_ID('{auto_table}', 'U') IS NULL
        BEGIN
            CREATE TABLE {auto_table} (
                id {pk_type},
                PRENOM NVARCHAR(255),
                NOM NVARCHAR(255),
                genre_correct NVARCHAR(100),
                created_at DATETIME DEFAULT GETDATE()
            );
        END
    """
    cursor.execute(create_sql)


def normalize_text(value): #removes any type of accents or empty spaces
    if value is None:
        return ''
    value = str(value)
    value = value.replace('\ufeff', '').replace('\u200b', '').replace('\xa0', ' ')
    value = value.strip().lower()
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z\s\-'/\.]", '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def is_null_like(value):
    if value is None:
        return True
    raw = str(value).strip()
    if raw == '':
        return True
    normalized = normalize_text(raw).replace('/', '').replace('.', '')
    return normalized in {item.replace('/', '').replace('.', '') for item in NULL_LIKE_VALUES}


def normalize_gender(value):
    if is_null_like(value):
        return ''

    normalized = normalize_text(value)
    if normalized in {'m', 'male', 'masculin', 'masculine'}:
        return 'MASCULIN'
    if normalized in {'f', 'female', 'feminin', 'feminine'}:
        return 'FEMININ'
    return str(value).strip().upper()


def to_reference_gender_code(value):
    """Converts internal MASCULIN/FEMININ labels to M/F for reference file storage."""
    normalized = normalize_gender(value)
    if normalized == 'MASCULIN':
        return 'M'
    if normalized == 'FEMININ':
        return 'F'
    return normalized


def display_prenom(value):
    if value is None or str(value).strip() == '':
        return '<EMPTY>'
    return str(value).strip()


def similarity_ratio(left, right):
    if not left or not right:
        return 0
    if HAS_RAPIDFUZZ:
        return fuzz.ratio(left, right)
    return SequenceMatcher(None, left, right).ratio() * 100


def choose_display_name(names, fallback):
    filtered = [str(name).strip() for name in names if name and str(name).strip()]
    if not filtered:
        return fallback or '<EMPTY>'

    counter = Counter(filtered)
    return sorted(counter.items(), key=lambda item: (-item[1], len(item[0]), item[0].lower()))[0][0]


def quote_sql_ident(name):
    return '[' + str(name).replace(']', ']]') + ']'


def list_table_columns(cursor, table_name):
    cursor.execute(f'SELECT TOP 0 * FROM {table_name}')
    return [column[0] for column in cursor.description]


def find_named_column(columns, aliases):
    normalized_map = {
        normalize_text(column).replace(' ', '_'): column
        for column in columns
    }
    compact_map = {
        key.replace('_', ''): value
        for key, value in normalized_map.items()
    }
    for alias in aliases:
        alias_norm = normalize_text(alias).replace(' ', '_')
        if alias_norm in normalized_map:
            return normalized_map[alias_norm]
        compact = alias_norm.replace('_', '')
        if compact in compact_map:
            return compact_map[compact]
    return None


def assign_group_key(normalized_name, known_keys, threshold):
    if normalized_name in known_keys:
        return normalized_name

    if not normalized_name:
        return '<empty>'

    # Short / truncated prenoms (ABD, MED, ...) must stay exact so they are not
    # merged with longer unrelated names.
    if len(normalized_name) < MIN_FUZZY_GROUP_LEN:
        return normalized_name

    if not known_keys:
        return normalized_name

    if HAS_RAPIDFUZZ:
        result = process.extractOne(
            normalized_name,
            list(known_keys),
            scorer=fuzz.ratio,
            score_cutoff=threshold,
        )
        return result[0] if result else normalized_name

    best_key = None
    best_score = 0
    for candidate in known_keys:
        score = similarity_ratio(normalized_name, candidate)
        if score > best_score:
            best_score = score
            best_key = candidate
    return best_key if best_key and best_score >= threshold else normalized_name


def build_ungrouped_null_records(records):
    """Replaces the old fuzzy-grouped PRENOM list: every unresolved row is
    kept as its OWN separate entry, even when it shares an identical (or
    similar) PRENOM with other rows. Grouping used to silently lump rows
    together purely because their PRENOM text matched -- which broke badly
    whenever someone had put a LAST name in the PRENOM column, since two
    unrelated people who happen to share that surname would get merged and
    pushed to the same gender in one click. Rows are resolved individually
    now; /api/v1/enrich-reference performs a live cascade at push time
    instead (see cascade_fill_matching_nulls) so pushing one name can still
    resolve other, still-open rows that exactly match it -- but only ones
    that genuinely still match at the moment of the push."""
    response = []
    for row in records:
        row_id = row.get('ROW_ID')
        prenom_value = row.get('PRENOM')
        nom_value = row.get('NOM')

        display_name_value = display_prenom(prenom_value)
        normalized_prenom = normalize_text(prenom_value)
        nom_display = '' if is_null_like(nom_value) else str(nom_value).strip()

        response.append(
            {
                'PRENOM': display_name_value,
                'NORMALIZED_PRENOM': normalized_prenom,
                'NOM': nom_display,
                'ROW_ID': row_id,
                'ROW_IDS': [row_id] if row_id is not None else [],
                'COUNT': 1,
            }
        )

    response.sort(key=lambda item: (normalize_text(item['PRENOM']) or item['PRENOM'].lower(), item['ROW_ID'] or 0))
    return response


def cascade_fill_matching_nulls(server, database, table, user, password, pk, name_normalized, gender, exclude_row_ids=None):
    """Called right after a manual push resolves ONE row's gender for
    `name_normalized`. Sweeps every OTHER row whose GENRE is still
    unresolved and looks for an exact (normalized) match of that same name --
    first in the PRENOM column; only if nothing matches there does it fall
    back to checking the NOM column. Every match found gets the same gender
    applied immediately. This intentionally does an EXACT normalized-text
    match (no fuzzy matching) computed fresh against whatever is still open
    at push time, so pushing "ebrahimi" only ever resolves other rows that
    are actually "ebrahimi" -- never a merge of unrelated people the way the
    old fuzzy grouping could."""
    if not name_normalized or not database or not table:
        return []

    staging_table = resolve_staging_table(table)
    exclude_ids = set(exclude_row_ids or [])

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        # empty_prenom=None -> every still-unresolved row, whether or not its
        # PRENOM is filled in, since a match can come from either column.
        candidates = [
            row for row in fetch_staging_name_rows(cursor, staging_table, pk, empty_prenom=None)
            if row.get('ROW_ID') is not None and row.get('ROW_ID') not in exclude_ids
        ]

        match_ids = [
            row['ROW_ID'] for row in candidates
            if normalize_text(row.get('PRENOM')) == name_normalized
        ]

        if not match_ids:
            match_ids = [
                row['ROW_ID'] for row in candidates
                if not is_null_like(row.get('NOM')) and normalize_text(row.get('NOM')) == name_normalized
            ]

        if not match_ids:
            return []

        columns = list_table_columns(cursor, staging_table)
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'
        for chunk in chunked(match_ids):
            placeholders = ', '.join(['?'] * len(chunk))
            cursor.execute(
                f"""
                    UPDATE {staging_table}
                    SET {quote_sql_ident(genre_col)} = ?
                    WHERE {quote_sql_ident(pk)} IN ({placeholders})
                """,
                [gender, *chunk],
            )
        conn.commit()
        return match_ids
    finally:
        if conn is not None:
            conn.close()


def group_nom_complet_records(records):
    groups = {}

    for row in records:
        row_id = row.get('ROW_ID')
        nom_complet_value = row.get('NOM_COMPLET')
        nom_value = row.get('NOM')
        prenom_value = row.get('PRENOM')
        if not is_null_like(nom_complet_value):
            full_name = str(nom_complet_value).strip()
        else:
            parts = []
            if not is_null_like(nom_value):
                parts.append(str(nom_value).strip())
            if not is_null_like(prenom_value):
                parts.append(str(prenom_value).strip())
            full_name = ' '.join(parts).strip()
        display_value = full_name if full_name else '<EMPTY>'
        group_key = normalize_text(full_name) or '<empty>'

        group = groups.setdefault(
            group_key,
            {
                'group_key': group_key,
                'display_candidates': [],
                'row_ids': [],
            },
        )
        group['display_candidates'].append(display_value)
        if row_id is not None:
            group['row_ids'].append(row_id)

    response = []
    for group in groups.values():
        display_value = choose_display_name(group['display_candidates'], '<EMPTY>')
        response.append({
            'GROUP_KEY': group['group_key'],
            'NOM_COMPLET': display_value,
            'ROW_IDS': group['row_ids'],
            'COUNT': len(group['row_ids']),
        })

    response.sort(key=lambda item: normalize_text(item['NOM_COMPLET']) or item['NOM_COMPLET'].lower())
    return response


def fetch_staging_name_rows(cursor, staging_table, pk, empty_prenom=None):
    """Fetches every row whose GENRE is still unresolved. `empty_prenom` filters
    the result: True keeps only rows with an empty/null PRENOM, False keeps only
    rows with a PRENOM present, and None (the default) returns every unresolved
    row regardless of whether PRENOM is filled in -- used by the push-cascade,
    which needs to check a pushed name against BOTH the PRENOM and NOM columns
    of every remaining null row."""
    columns = list_table_columns(cursor, staging_table)
    prenom_col = find_named_column(columns, ALIAS_PRENOM) or 'PRENOM'
    nom_col = find_named_column(columns, ALIAS_NOM)
    genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'
    nom_complet_col = find_named_column(columns, ALIAS_NOM_COMPLET)

    select_parts = [
        f'{quote_sql_ident(pk)} AS ROW_ID',
        f'{quote_sql_ident(prenom_col)} AS PRENOM',
    ]
    if nom_col:
        select_parts.append(f'{quote_sql_ident(nom_col)} AS NOM')
    else:
        select_parts.append('NULL AS NOM')
    if nom_complet_col:
        select_parts.append(f'{quote_sql_ident(nom_complet_col)} AS NOM_COMPLET')
    else:
        select_parts.append('NULL AS NOM_COMPLET')

    query = f"""
        SELECT {', '.join(select_parts)}
        FROM {staging_table}
        WHERE {quote_sql_ident(genre_col)} IS NULL
           OR {quote_sql_ident(genre_col)} = 'NULL'
           OR {quote_sql_ident(genre_col)} = ''
           OR UPPER({quote_sql_ident(genre_col)}) = 'INCONNU'
    """
    cursor.execute(query)
    column_names = [column[0] for column in cursor.description]
    results = [dict(zip(column_names, row)) for row in cursor.fetchall()]

    filtered = []
    for row in results:
        prenom_empty = is_null_like(row.get('PRENOM'))
        if empty_prenom is None:
            filtered.append(row)
        elif empty_prenom and prenom_empty:
            filtered.append(row)
        elif not empty_prenom and not prenom_empty:
            filtered.append(row)
    return filtered


def classify_original_label(raw_value):
    """Canonical bucket for an original GENRE value: 'NULL', 'MASCULIN', 'FEMININ', or the raw upper-cased text."""
    if is_null_like(raw_value):
        return 'NULL'
    normalized = normalize_gender(raw_value)
    if normalized:
        return normalized
    return str(raw_value).strip().upper()


def load_gender_reference_engine(cursor):
    """Builds the full gender_engine.GenderReference object: majority-vote
    resolved genders + collapsed-letters phonetic index. Use this for
    anything that needs the smarter multi-token / fallback matching
    (auto-fill, nom_complet suggestions). Reads directly from the
    reference_noms2 table on the currently connected database via the given
    cursor -- rebuilding per-request keeps it simple and always in sync with
    the table's live contents."""
    engine = gender_engine.GenderReference()

    try:
        cursor.execute(f'SELECT NormalizedName, Gender FROM {REFERENCE_TABLE}')
        for name_raw, gender_raw in cursor.fetchall():
            engine.add_observation(name_raw, gender_raw)
    except Exception:
        # A missing/misconfigured reference table should not break processing.
        pass

    engine.finalize()
    return engine


def load_reference_gender_map(cursor):
    """Loads the NormalizedName -> gender reference dataset from the
    reference_noms2 SQL table into a simple lookup dict, kept for existing
    callers (e.g. exceptions review). Internally this uses MAJORITY VOTE: if
    the table has the same normalized name recorded with two different
    genders on different rows, the more frequent one is kept."""
    return load_gender_reference_engine(cursor).gender


def process_audit_rows(records, cursor, fuzzy_threshold=90):
    """
    Splits flagged audit rows into three buckets:

    - audit_list: rows whose original GENRE (across the grouped PRENOM) agrees on a single
      non-null value -- the normal "Audit History" review queue.
    - null_list: rows whose original GENRE was NULL/blank -- routed to "Null Correction".
    - exceptions_list: rows that are the minority original-gender value within a PRENOM group
      that otherwise disagrees (e.g. 4 MASCULIN + 1 FEMININ for "Mohamed") -- routed to
      "Exceptions", where the NOM value is checked against the reference dataset for a
      more targeted suggestion.
    """
    reference_map = load_reference_gender_map(cursor)
    groups = {}

    for row in records:
        row_id = row.get('ROW_ID')
        prenom_value = row.get('PRENOM')
        nom_value = row.get('NOM')
        display_name_value = display_prenom(prenom_value)
        target_gender = normalize_gender(row.get('GENRE_CORRECT'))

        normalized_name = normalize_text(prenom_value) or '<empty>'
        name_group = assign_group_key(normalized_name, {key[0] for key in groups.keys()}, fuzzy_threshold)
        group_key = (name_group, target_gender)

        group = groups.setdefault(
            group_key,
            {
                'group_key': f'{name_group}::{target_gender}',
                'normalized_prenom': name_group if name_group != '<empty>' else '',
                'target_gender': target_gender,
                'rows': [],
            },
        )

        group['rows'].append({
            'row_id': row_id,
            'display_prenom': display_name_value,
            'nom_raw': nom_value,
            'original_label': classify_original_label(row.get('GENRE_ORIGINAL')),
        })

    audit_list = []
    null_list = []
    exceptions_raw = []

    for group in groups.values():
        rows = group['rows']
        label_counts = Counter(r['original_label'] for r in rows)
        majority_label, _ = label_counts.most_common(1)[0]

        majority_rows = [r for r in rows if r['original_label'] == majority_label]
        minority_rows = [r for r in rows if r['original_label'] != majority_label]

        display_name = choose_display_name(
            [r['display_prenom'] for r in majority_rows],
            group['normalized_prenom'] or '<EMPTY>',
        )
        row_ids = [r['row_id'] for r in majority_rows if r['row_id'] is not None]

        if majority_label == 'NULL':
            null_list.append({
                'GROUP_KEY': group['group_key'],
                'PRENOM': display_name,
                'GENRE_CORRECT': group['target_gender'],
                'ROW_IDS': row_ids,
                'COUNT': len(row_ids),
            })
        else:
            audit_list.append({
                'GROUP_KEY': group['group_key'],
                'PRENOM': display_name,
                'GENRE_ORIGINAL': majority_label,
                'GENRE_CORRECT': group['target_gender'],
                'ROW_IDS': row_ids,
                'COUNT': len(row_ids),
            })

        for r in minority_rows:
            exceptions_raw.append({
                'name_group': group['normalized_prenom'],
                'display_prenom': r['display_prenom'],
                'nom_raw': r['nom_raw'],
                'original_label': r['original_label'],
                'target_gender': group['target_gender'],
                'row_id': r['row_id'],
            })

    # Group exceptions by (prenom group, normalized NOM, original label) to avoid one row per record.
    exception_groups = {}
    for item in exceptions_raw:
        nom_normalized = normalize_text(item['nom_raw'])
        key = (item['name_group'], nom_normalized, item['original_label'])

        exc_group = exception_groups.setdefault(key, {
            'group_key': f"{item['name_group']}::{nom_normalized or '<empty>'}::{item['original_label']}",
            'prenom_candidates': [],
            'nom_candidates': [],
            'original_label': item['original_label'],
            'target_gender': item['target_gender'],
            'row_ids': [],
        })
        exc_group['prenom_candidates'].append(item['display_prenom'])
        exc_group['nom_candidates'].append(display_prenom(item['nom_raw']))
        if item['row_id'] is not None:
            exc_group['row_ids'].append(item['row_id'])

    exceptions_list = []
    for exc in exception_groups.values():
        nom_display = choose_display_name(exc['nom_candidates'], '<EMPTY>')
        nom_lookup = normalize_text(nom_display)
        suggested_gender = reference_map.get(nom_lookup, '')

        exceptions_list.append({
            'GROUP_KEY': exc['group_key'],
            'PRENOM': choose_display_name(exc['prenom_candidates'], '<EMPTY>'),
            'NOM': nom_display,
            'GENRE_ORIGINAL': exc['original_label'],
            'SUGGESTED_GENDER': suggested_gender,
            'PRENOM_BASED_GENDER': exc['target_gender'],
            'ROW_IDS': exc['row_ids'],
            'COUNT': len(exc['row_ids']),
        })

    audit_list.sort(key=lambda item: (normalize_text(item['PRENOM']) or item['PRENOM'].lower(), item['GENRE_CORRECT']))
    null_list.sort(key=lambda item: normalize_text(item['PRENOM']) or item['PRENOM'].lower())
    exceptions_list.sort(key=lambda item: normalize_text(item['PRENOM']) or item['PRENOM'].lower())

    return audit_list, null_list, exceptions_list


def enrich_reference_table(server, database, user, password, prenom, gender):
    """Adds (or confirms) a PRENOM -> gender entry directly in the
    reference_noms2 SQL table (columns: NormalizedName, Gender). This
    replaces the old CSV/Excel-file enrichment -- the table already lives in
    the connected database, so we just open a connection and INSERT."""
    reference_gender = to_reference_gender_code(gender)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT COUNT(*) FROM {REFERENCE_TABLE}
            WHERE NormalizedName = ? AND Gender = ?
            """,
            (prenom, reference_gender),
        )
        if cursor.fetchone()[0] > 0:
            return 'exists'

        cursor.execute(
            f"""
            INSERT INTO {REFERENCE_TABLE} (NormalizedName, Gender)
            VALUES (?, ?)
            """,
            (prenom, reference_gender),
        )
        conn.commit()
        return 'created'
    finally:
        if conn is not None:
            conn.close()


def update_staging_gender_rows(server, database, table, user, password, pk, row_ids, target_gender):
    """Runs the actual UPDATE against the staging table's GENRE column for the given row ids.
    Shared by /api/v1/enrich-reference and /api/v1/save-staging-gender so both endpoints
    push to the database the same way."""
    if not database or not table or not row_ids:
        return 0

    staging_table = resolve_staging_table(table)
    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        columns = list_table_columns(cursor, staging_table)
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'
        for chunk in chunked(row_ids):
            placeholders = ', '.join(['?'] * len(chunk))
            update_query = f"""
                UPDATE {staging_table}
                SET {quote_sql_ident(genre_col)} = ?
                WHERE {quote_sql_ident(pk)} IN ({placeholders})
            """
            cursor.execute(update_query, [target_gender, *chunk])
        conn.commit()
        return len(row_ids)
    finally:
        if conn is not None:
            conn.close()


def log_manual_reference_push(data, prenom, gender):
    table = data.get('table')
    if not table:
        return

    server = data.get('server', 'localhost')
    database = data.get('database')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')
    row_ids = data.get('row_ids') or []
    if not database:
        return

    manual_table = resolve_manual_history_table(table)
    staging_table = resolve_staging_table(table)
    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()
        pk_type = get_column_sql_type(cursor, staging_table, pk)
        ensure_manual_history_table(cursor, manual_table, pk_type)

        insert_manual_query = f"""
            INSERT INTO {manual_table} (id, PRENOM, NOM, genre_correct)
            VALUES (?, ?, ?, ?)
        """
        # Correcting an existing history row (e.g. FEMININ -> MASCULIN) should
        # replace that row's entry, not add a second one for the same id --
        # so re-pushes update the most recent row for that id instead of
        # inserting a new one. created_at is bumped too, so "latest per id"
        # ordering (ORDER BY created_at DESC) keeps working after an update.
        update_manual_query = f"""
            UPDATE {manual_table}
            SET PRENOM = ?, NOM = ?, genre_correct = ?, created_at = GETDATE()
            WHERE id = ?
        """

        if row_ids:
            # Look up each row's REAL PRENOM and REAL NOM from the staging
            # table -- a single push (e.g. "Push nom") can cover several
            # original records, and one PRENOM/NOM push can supply the
            # confirmed name from either column, so the caller-supplied
            # `prenom` is NOT necessarily this row's real PRENOM value.
            # Blindly writing it into PRENOM here caused rows whose real
            # PRENOM was empty (name only lived in NOM) to show the same
            # name duplicated into both columns in history.
            prenom_by_id = {}
            nom_by_id = {}
            columns = list_table_columns(cursor, staging_table)
            prenom_col = find_named_column(columns, ALIAS_PRENOM)
            nom_col = find_named_column(columns, ALIAS_NOM)
            select_cols = [f'{quote_sql_ident(pk)} AS ID']
            if prenom_col:
                select_cols.append(f'{quote_sql_ident(prenom_col)} AS PRENOM')
            if nom_col:
                select_cols.append(f'{quote_sql_ident(nom_col)} AS NOM')

            if prenom_col or nom_col:
                for chunk in chunked(row_ids):
                    placeholders = ', '.join(['?'] * len(chunk))
                    cursor.execute(
                        f"""
                            SELECT {', '.join(select_cols)}
                            FROM {staging_table}
                            WHERE {quote_sql_ident(pk)} IN ({placeholders})
                        """,
                        chunk,
                    )
                    for row in cursor.fetchall():
                        if prenom_col:
                            prenom_by_id[row.ID] = row.PRENOM
                        if nom_col:
                            nom_by_id[row.ID] = row.NOM

            def resolve_display_names(row_id):
                """Keep each row's real PRENOM/NOM split as it actually is in
                staging -- only fill in the pushed name on whichever side of
                that row was empty, instead of forcing it into PRENOM."""
                real_prenom = prenom_by_id.get(row_id)
                real_nom = nom_by_id.get(row_id)
                prenom_is_empty = not real_prenom or not str(real_prenom).strip()
                nom_is_empty = not real_nom or not str(real_nom).strip()

                if not prenom_is_empty:
                    # Row already had a real PRENOM -- keep it, and keep the
                    # real NOM (even if empty) rather than overwriting either.
                    return real_prenom, real_nom
                if nom_is_empty:
                    # Neither column had a value on this row -- nothing to
                    # split the pushed name against, so just record it as PRENOM.
                    return prenom, real_nom
                # PRENOM was empty but NOM was filled -- this push's name came
                # from NOM, so it belongs in NOM, not duplicated into PRENOM.
                return None, real_nom

            # One history row per original record this push actually applied to,
            # each carrying that record's real id and its real PRENOM/NOM split.
            # If a history row for this id already exists, update it in place
            # (a correction) instead of inserting a duplicate.
            for row_id in row_ids:
                display_prenom, display_nom = resolve_display_names(row_id)
                cursor.execute(update_manual_query, (display_prenom, display_nom, gender, row_id))
                if cursor.rowcount == 0:
                    cursor.execute(insert_manual_query, (row_id, display_prenom, display_nom, gender))
        else:
            # No specific rows were targeted (e.g. reference-only push) -- there is
            # no original id/NOM to attach, so leave both NULL rather than inventing one.
            cursor.execute(insert_manual_query, (None, prenom, None, gender))
        conn.commit()
    finally:
        if conn is not None:
            conn.close()


@app.route('/')
def home():
    return app.send_static_file('index.html')


@app.route('/api/v1/run-script', methods=['POST'])
def run_script():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    try:
        script_dir = Path(__file__).resolve().parent
        cmd = [
            'python',
            '-u',  # unbuffered: forces stdout to reach us line-by-line, not just at exit
            'db_genreFINAL.py',
            '--serveur',
            server,
            '--base-donnees',
            database,
            '--table',
            table,
            '--colonne-pk',
            pk,
        ]

        if user and password:
            cmd.extend(['--utilisateur', user, '--mot-de-passe', password])
        else:
            cmd.append('--trusted-connection')

        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'

        print(f"[run-script] Launching db_genreFINAL.py for table '{table}'...", flush=True)

        # Popen + line-by-line reading (instead of subprocess.run with
        # capture_output=True) is what actually gets the child script's
        # [INFO]/[HEARTBEAT] prints into THIS terminal live, as they happen,
        # rather than all at once after the whole script finishes.
        process = subprocess.Popen(
            cmd,
            cwd=str(script_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )

        output_lines = []
        for line in process.stdout:
            line = line.rstrip('\n')
            print(line, flush=True)  # echoed live into the `python app.py` terminal
            output_lines.append(line)

        process.wait()
        full_output = '\n'.join(output_lines)

        if process.returncode != 0:
            return jsonify({'status': 'error', 'message': full_output}), 500

        return jsonify({
            'status': 'success',
            'message': 'Matching script completed.',
            'output': full_output,
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@app.route('/api/v1/null-records', methods=['POST'])
def get_null_records():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    staging_table = resolve_staging_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()
        results = fetch_staging_name_rows(cursor, staging_table, pk, empty_prenom=False)
        ungrouped = build_ungrouped_null_records(results)

        # --- DEBUG ---
        print(f"[DEBUG null-records] table={staging_table}")
        print(f"[DEBUG null-records] rows returned by fetch_staging_name_rows: {len(results)}")
        print(f"[DEBUG null-records] rows returned (ungrouped, 1:1 with DB rows): {len(ungrouped)}")
        # --- END DEBUG ---

        return jsonify({
            'status': 'success',
            'data': ungrouped,
            'debug': {
                'rows_from_fetch_staging_name_rows': len(results),
                'num_records': len(ungrouped),
            }
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/auto-fill-exact-matches', methods=['POST'])
def auto_fill_exact_matches():
    """
    Auto-fills GENRE directly on the staging table, but ONLY for rows that satisfy
    every one of these conditions:

      1. GENRE is currently NULL/blank/'INCONNU'. Rows that already carry ANY gender
         value are never touched or overwritten, even if that value looks wrong --
         this endpoint performs no correction, only first-time filling of true blanks.
      2. PRENOM is filled (not null/blank). Rows with an empty PRENOM belong to the
         nom_complets.html flow and are intentionally left alone here.
      3. NOM is filled (not null/blank). Rows missing NOM are left alone too.

    Matching uses gender_engine.guess_gender() with allow_fuzzy=False, so this
    endpoint stays exact-match-only (no rapidfuzz guessing) -- but "exact match"
    now covers more than a single literal PRENOM lookup:
      - Honorific titles (Moulay/Sidi -> M, Lalla/Hajja -> F) if present.
      - The "Abd + attribute" rule (Abdelkader, Abd Errahim, ...) which is
        grammatically always masculine.
      - Each individual token of PRENOM itself, e.g. compound first names
        like "Mohammed Amine" (skipping bare connector words like
        ben/el/al/ould/ait so they're never mistaken for a first name).
      - As a LAST resort, if PRENOM still has no hit, the same particle-aware
        exact scan is run over NOM (front AND back of the name), for the
        common case where PRENOM/NOM got swapped or PRENOM was left with
        only a family name. This fallback is only accepted when the front
        and back scans agree (or only one produced a hit) -- if they
        disagree, the row is left untouched rather than guessed.
    Rows with no exact match anywhere are left completely untouched and simply
    remain visible in the normal null-records review list.

    Every row that IS updated is written to staging's GENRE column and logged into the
    per-table *_AUTO_HISTORY table (PRENOM, NOM, genre_correct, created_at) so the
    auto_history.html page can show a full audit trail of what was auto-assigned.
    """
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    if not database or not table:
        return jsonify({'status': 'error', 'message': 'Database and table are required.'}), 400

    staging_table = resolve_staging_table(table)
    auto_table = resolve_auto_history_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        columns = list_table_columns(cursor, staging_table)
        prenom_col = find_named_column(columns, ALIAS_PRENOM) or 'PRENOM'
        nom_col = find_named_column(columns, ALIAS_NOM)
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'

        if not nom_col:
            # No NOM column detected on this table at all -> nothing can qualify as
            # "PRENOM full AND NOM full", so there is nothing safe to touch.
            return jsonify({
                'status': 'success',
                'message': 'No NOM column detected on this table; nothing to auto-fill.',
                'updated_count': 0,
                'candidates_checked': 0,
                'data': []
            })

        select_query = f"""
            SELECT {quote_sql_ident(pk)} AS ROW_ID,
                   {quote_sql_ident(prenom_col)} AS PRENOM,
                   {quote_sql_ident(nom_col)} AS NOM
            FROM {staging_table}
            WHERE ({quote_sql_ident(genre_col)} IS NULL
                   OR {quote_sql_ident(genre_col)} = 'NULL'
                   OR {quote_sql_ident(genre_col)} = ''
                   OR UPPER({quote_sql_ident(genre_col)}) = 'INCONNU')
        """
        cursor.execute(select_query)
        col_names = [c[0] for c in cursor.description]
        rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]

        # Keep ONLY rows where PRENOM and NOM are BOTH actually filled in. This is
        # what excludes empty-PRENOM rows (the nom_complets.html population) and any
        # row missing a NOM -- those are never candidates for this feature.
        candidates = [
            row for row in rows
            if not is_null_like(row.get('PRENOM'))

        ]

        engine = load_gender_reference_engine(cursor)

        matched_updates = []    # (row_id, gender)
        history_entries = []    # (row_id, prenom_display, nom_display, gender)

        for row in candidates:
            prenom_raw = row.get('PRENOM')
            nom_raw = row.get('NOM')

            guess = gender_engine.guess_gender(
                prenom_value=prenom_raw,
                nom_value=nom_raw,
                reference=engine,
                allow_fuzzy=False,  # exact/collapsed/honorific/abd-rule only -- no rapidfuzz
            )
            if guess['ambiguous'] or not guess['gender']:
                continue  # no safe exact-style match -> leave this row exactly as-is
            gender = guess['gender']
            row_id = row.get('ROW_ID')
            matched_updates.append((row_id, gender))
            # Keep the original staging row's id so AUTO_HISTORY can store it
            # instead of inventing its own.
            history_entries.append((row_id, str(prenom_raw).strip(), str(nom_raw).strip(), gender))

        updated_count = 0
        if matched_updates:
            # Batch the UPDATEs by gender value to minimize round trips.
            by_gender = {}
            for row_id, gender in matched_updates:
                by_gender.setdefault(gender, []).append(row_id)

            for gender, row_ids in by_gender.items():
                for chunk in chunked(row_ids):
                    placeholders = ', '.join(['?'] * len(chunk))
                    update_query = f"""
                        UPDATE {staging_table}
                        SET {quote_sql_ident(genre_col)} = ?
                        WHERE {quote_sql_ident(pk)} IN ({placeholders})
                    """
                    cursor.execute(update_query, [gender, *chunk])
                    updated_count += len(chunk)

            conn.commit()

            pk_type = get_column_sql_type(cursor, staging_table, pk)
            ensure_auto_history_table(cursor, auto_table, pk_type)
            insert_query = f"""
                INSERT INTO {auto_table} (id, PRENOM, NOM, genre_correct)
                VALUES (?, ?, ?, ?)
            """
            cursor.executemany(insert_query, history_entries)
            conn.commit()

        return jsonify({
            'status': 'success',
            'message': f'Auto-filled {updated_count} row(s) from exact reference matches.',
            'updated_count': updated_count,
            'candidates_checked': len(candidates),
            'data': [
                {'ID': i, 'PRENOM': p, 'NOM': n, 'GENRE_CORRECT': g} for i, p, n, g in history_entries
            ]
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/rewind', methods=['POST'])
def rewind_to_snapshot():
    """
    Restores STAGING back to the exact state it was in right after db_genreFINAL.py
    last (re)built it from the source table -- i.e. before any auto-fill, manual
    save, or audit-confirm edits were made in this app.

    This is the fast alternative to "delete every generated table by hand and
    re-run the 8-minute script": db_genreFINAL.py already saves an untouched copy
    of STAGING as {table}_STAGING_SNAPSHOT the moment it builds it (see that
    script). Rewinding just:
      1. Drops the current STAGING table and rebuilds it from the snapshot
         (a plain in-database bulk copy -- seconds, not minutes, because no
         fuzzy-matching/Python row processing happens here).
      2. Clears AUTO_HISTORY and MANUAL_HISTORY (the two tables that only ever
         accumulate rows because of edits made *after* the script ran), so the
         history pages go back to empty too.

    AUDIT is intentionally left untouched -- nothing in this app ever writes back
    to it, so it's already identical to what the script produced.

    Requires that db_genreFINAL.py has been run at least once since this snapshot
    mechanism was added; if no snapshot table exists yet, this returns an error
    telling the user to run the script once first.
    """
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    if not database or not table:
        return jsonify({'status': 'error', 'message': 'Database and table are required.'}), 400

    staging_table = resolve_staging_table(table)
    snapshot_table = resolve_staging_snapshot_table(table)
    auto_table = resolve_auto_history_table(table)
    manual_table = resolve_manual_history_table(table)

    staging_object_id = staging_table.replace('dbo.', '').replace('[', '').replace(']', '')
    snapshot_object_id = snapshot_table.replace('dbo.', '').replace('[', '').replace(']', '')
    auto_object_id = auto_table.replace('dbo.', '').replace('[', '').replace(']', '')
    manual_object_id = manual_table.replace('dbo.', '').replace('[', '').replace(']', '')

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        cursor.execute("SELECT OBJECT_ID(?, 'U')", (f'dbo.{snapshot_object_id}',))
        if cursor.fetchone()[0] is None:
            return jsonify({
                'status': 'error',
                'message': (
                    f"No rewind snapshot found ({snapshot_table}). Run the matching "
                    f"script once first -- it creates this snapshot automatically, "
                    f"and every rewind after that will be instant."
                ),
            }), 404

        # Rebuild STAGING from the untouched snapshot.
        cursor.execute(
            f"IF OBJECT_ID(?, 'U') IS NOT NULL DROP TABLE {staging_table};",
            (f'dbo.{staging_object_id}',),
        )
        cursor.execute(f"SELECT * INTO {staging_table} FROM {snapshot_table};")
        conn.commit()

        try:
            idx_name = quote_sql_ident(f"IX_{staging_object_id}_{pk}_rewind")
            cursor.execute(
                f"CREATE INDEX {idx_name} ON {staging_table}({quote_sql_ident(pk)});"
            )
            conn.commit()
        except Exception:
            pass  # index is a nice-to-have; never block a rewind on it

        # Wipe the two history tables -- they only hold rows created by edits made
        # since the snapshot was taken, so on rewind they should go back to empty.
        cursor.execute(
            f"IF OBJECT_ID(?, 'U') IS NOT NULL DROP TABLE {auto_table};",
            (f'dbo.{auto_object_id}',),
        )
        cursor.execute(
            f"IF OBJECT_ID(?, 'U') IS NOT NULL DROP TABLE {manual_table};",
            (f'dbo.{manual_object_id}',),
        )
        conn.commit()

        return jsonify({
            'status': 'success',
            'message': (
                f"Rewound '{staging_table}' to the snapshot taken right after the "
                f"last script run, and cleared auto-fill/manual history."
            ),
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/auto-history-records', methods=['POST'])
def get_auto_history_records():
    """Returns the full auto-fill history log for the given table (newest first)."""
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')

    if not database or not table:
        return jsonify({'status': 'error', 'message': 'Database and table are required.'}), 400

    staging_table = resolve_staging_table(table)
    auto_table = resolve_auto_history_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()
        pk_type = get_column_sql_type(cursor, staging_table, data.get('pk', 'ID'))
        ensure_auto_history_table(cursor, auto_table, pk_type)
        conn.commit()

        # NOTE: ordering by 'id' would no longer reflect recency, since id now
        # holds the ORIGINAL staging row's id (not a sequential identity) --
        # order by created_at instead.
        cursor.execute(f"""
            SELECT id AS ID, PRENOM, NOM, genre_correct AS GENRE_CORRECT, created_at AS CREATED_AT
            FROM {auto_table}
            ORDER BY created_at DESC
        """)
        columns = [c[0] for c in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]

        for row in results:
            if row.get('CREATED_AT') is not None:
                row['CREATED_AT'] = str(row['CREATED_AT'])

        return jsonify({'status': 'success', 'data': results})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


def fetch_processed_audit_lists(data):
    """Fetches audit-table rows for the given DB config and splits them via process_audit_rows."""
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    audit_table = resolve_audit_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        query = f"""
            SELECT
                {pk} AS ROW_ID,
                PRENOM AS PRENOM,
                NOM AS NOM,
                genre_original AS GENRE_ORIGINAL,
                genre_correct AS GENRE_CORRECT
            FROM {audit_table}
            ORDER BY {pk}
        """

        cursor.execute(query)

        columns = [column[0] for column in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        filtered = [
            row for row in results
            if normalize_gender(row.get('GENRE_CORRECT'))
        ]

        return process_audit_rows(filtered, cursor)
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/audit-records', methods=['POST'])
def get_audit_records():
    data = request.json or {}
    try:
        audit_list, _null_list, _exceptions_list = fetch_processed_audit_lists(data)
        return jsonify({
            'status': 'success',
            'data': audit_list
        })
    except Exception as exc:
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 500


@app.route('/api/v1/null-correction-records', methods=['POST'])
def get_null_correction_records():
    data = request.json or {}
    try:
        _audit_list, null_list, _exceptions_list = fetch_processed_audit_lists(data)
        return jsonify({
            'status': 'success',
            'data': null_list
        })
    except Exception as exc:
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 500


@app.route('/api/v1/exception-records', methods=['POST'])
def get_exception_records():
    data = request.json or {}
    try:
        _audit_list, _null_list, exceptions_list = fetch_processed_audit_lists(data)
        return jsonify({
            'status': 'success',
            'data': exceptions_list
        })
    except Exception as exc:
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 500


@app.route('/api/v1/manual-history-records', methods=['POST'])
def get_manual_history_records():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')

    staging_table = resolve_staging_table(table)
    manual_table = resolve_manual_history_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        pk_type = get_column_sql_type(cursor, staging_table, data.get('pk', 'ID'))
        ensure_manual_history_table(cursor, manual_table, pk_type)
        conn.commit()

        query = f"""
            SELECT
                id AS ID,
                PRENOM,
                NOM,
                genre_correct AS GENRE_CORRECT
            FROM {manual_table}
            ORDER BY created_at DESC
        """

        cursor.execute(query)

        columns = [column[0] for column in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return jsonify({
            'status': 'success',
            'data': results
        })

    except Exception as exc:
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/enrich-reference', methods=['POST'])
def enrich_reference():
    data = request.json or {}
    prenom = normalize_text(data.get('prenom'))
    gender = normalize_gender(data.get('gender'))
    row_ids = data.get('row_ids') or []

    if not prenom:
        return jsonify({'status': 'error', 'message': 'A PRENOM value is required.'}), 400
    if gender not in {'MASCULIN', 'FEMININ'}:
        return jsonify({'status': 'error', 'message': 'A valid gender selection is required.'}), 400

    # Multi-word names (e.g. "abdel malek", "mellal berha") are almost always
    # a first+last name pair rather than a single clean given/family name,
    # so they'd pollute the NormalizedName reference lookup if saved there.
    # Skip the reference-table write silently for these -- the staging DB
    # push, history logging, and cascade all still run normally below.
    is_multi_word = len(prenom.split()) > 1

    try:
        if is_multi_word:
            status = 'skipped_multi_word'
        else:
            status = enrich_reference_table(
                server=data.get('server', 'localhost'),
                database=data.get('database'),
                user=data.get('user'),
                password=data.get('password'),
                prenom=prenom,
                gender=gender,
            )
        try:
            log_manual_reference_push(data, prenom, gender)
        except Exception:
            pass

        # Also push the assigned gender to the live staging table rows, so the
        # database record itself is resolved and not just the reference table.
        updated_count = 0
        db_error = None
        if row_ids:
            try:
                updated_count = update_staging_gender_rows(
                    server=data.get('server', 'localhost'),
                    database=data.get('database'),
                    table=data.get('table'),
                    user=data.get('user'),
                    password=data.get('password'),
                    pk=data.get('pk', 'ID'),
                    row_ids=row_ids,
                    target_gender=gender,
                )
            except Exception as db_exc:
                db_error = str(db_exc)

        # Cascade: with grouping removed, sweep every OTHER still-unresolved
        # row for an exact match of this same (normalized) name -- PRENOM
        # column first, NOM column only if nothing matched in PRENOM -- and
        # resolve those too. Runs for both the "Push prenom" and "Push nom"
        # buttons, since both call this same endpoint.
        cascade_ids = []
        cascade_error = None
        try:
            cascade_ids = cascade_fill_matching_nulls(
                server=data.get('server', 'localhost'),
                database=data.get('database'),
                table=data.get('table'),
                user=data.get('user'),
                password=data.get('password'),
                pk=data.get('pk', 'ID'),
                name_normalized=prenom,
                gender=gender,
                exclude_row_ids=row_ids,
            )
        except Exception as cascade_exc:
            cascade_error = str(cascade_exc)

        if cascade_ids:
            try:
                cascade_data = dict(data)
                cascade_data['row_ids'] = cascade_ids
                log_manual_reference_push(cascade_data, prenom, gender)
            except Exception:
                pass

        message = 'Reference table updated.' if status == 'created' else 'Reference already contained this entry.'
        if updated_count:
            message += f' Also updated {updated_count} database row(s).'
        elif db_error:
            message += f' Warning: database update failed ({db_error}).'
        if cascade_ids:
            message += f' Auto-resolved {len(cascade_ids)} other matching row(s).'
        elif cascade_error:
            message += f' Warning: cascade match failed ({cascade_error}).'

        return jsonify({
            'status': 'success',
            'result': status,
            'message': message,
            'prenom': prenom,
            'gender': gender,
            'reference_table': REFERENCE_TABLE,
            'updated_count': updated_count,
            'db_error': db_error,
            'cascade_updated_ids': cascade_ids,
            'cascade_updated_count': len(cascade_ids),
            'cascade_error': cascade_error,
        })
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@app.route('/api/v1/nom-complet-records', methods=['POST'])
def get_nom_complet_records():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    staging_table = resolve_staging_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()
        results = fetch_staging_name_rows(cursor, staging_table, pk, empty_prenom=True)
        engine = load_gender_reference_engine(cursor)

        data_out = []
        for row in results:
            nom_complet_value = row.get('NOM_COMPLET') or row.get('NOM') or ''
            nom_complet_value = str(nom_complet_value).strip()

            # -------------------- ADD THESE LINES HERE --------------------
            # Skip rows where NOM is empty or set to placeholder values like "Inconnu"
            if not nom_complet_value or nom_complet_value.lower() in ('inconnu', 'unknown', 'null', 'none'):
                continue
            # --------------------------------------------------------------

            # Full bidirectional scan (front AND back of the name, particles
            # skipped, honorifics + Abd-rule + fuzzy all enabled) -- this is
            # only ever a SUGGESTION pre-filled in the dropdown; a human still
            # has to click Save, so it's safe to be more liberal here than in
            # the direct-write auto-fill endpoint.
            guess = gender_engine.guess_gender(
                prenom_value=None,
                nom_value=nom_complet_value,
                reference=engine,
                allow_fuzzy=True,
            )

            data_out.append({
                'NOM_COMPLET': nom_complet_value,
                'ROW_IDS': [row.get('ROW_ID')],
                'COUNT': 1,
                'SUGGESTED_GENDER': guess['gender'] if not guess['ambiguous'] else '',
                'SUGGESTED_CONFIDENCE': round((guess.get('confidence') or 0) * 100),
                'SUGGESTED_REASON': guess['reason'],
            })

        return jsonify({'status': 'success', 'data': data_out})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/save-staging-gender', methods=['POST'])
def save_staging_gender():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')
    row_ids = data.get('row_ids') or []
    target_gender = normalize_gender(data.get('gender'))

    if not row_ids:
        return jsonify({'status': 'error', 'message': 'row_ids is required.'}), 400
    if target_gender not in {'MASCULIN', 'FEMININ'}:
        return jsonify({'status': 'error', 'message': 'A valid target gender is required.'}), 400

    staging_table = resolve_staging_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        columns = list_table_columns(cursor, staging_table)
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'
        for chunk in chunked(row_ids):
            placeholders = ', '.join(['?'] * len(chunk))
            update_query = f"""
                UPDATE {staging_table}
                SET {quote_sql_ident(genre_col)} = ?
                WHERE {quote_sql_ident(pk)} IN ({placeholders})
            """
            cursor.execute(update_query, [target_gender, *chunk])
        conn.commit()

        return jsonify({
            'status': 'success',
            'message': f'Updated {len(row_ids)} record(s) in the staging table.',
            'updated_count': len(row_ids),
            'gender': target_gender,
        })
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/confirm-audit-gender', methods=['POST'])
def confirm_audit_gender():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')
    row_ids = data.get('row_ids') or []
    target_gender = normalize_gender(data.get('gender'))

    if not row_ids:
        return jsonify({'status': 'error', 'message': 'row_ids is required.'}), 400
    if target_gender not in {'MASCULIN', 'FEMININ'}:
        return jsonify({'status': 'error', 'message': 'A valid target gender is required.'}), 400

    staging_table = resolve_staging_table(table)

    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        for chunk in chunked(row_ids):
            placeholders = ', '.join(['?'] * len(chunk))
            update_query = f"""
                UPDATE {staging_table}
                SET GENRE = ?
                WHERE {pk} IN ({placeholders})
            """
            cursor.execute(update_query, [target_gender, *chunk])
        conn.commit()

        return jsonify({
            'status': 'success',
            'message': f'Updated {len(row_ids)} record(s) in the staging table.',
            'updated_count': len(row_ids),
            'gender': target_gender,
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/export-unresolved', methods=['POST'])
def export_unresolved_records():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    if not database or not table:
        return jsonify({'status': 'error', 'message': 'Database and table parameters are required.'}), 400

    staging_table = resolve_staging_table(table)
    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        columns = list_table_columns(cursor, staging_table)
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'
        prenom_col = find_named_column(columns, ALIAS_PRENOM) or 'PRENOM'

        # --- DEBUG: sanity-check counts at each stage ---
        cursor.execute(f"SELECT COUNT(*) FROM {staging_table}")
        debug_total_rows = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT COUNT(*) FROM {staging_table}
            WHERE (
                {quote_sql_ident(genre_col)} IS NULL
                OR {quote_sql_ident(genre_col)} = ''
                OR {quote_sql_ident(genre_col)} = 'NULL'
                OR UPPER({quote_sql_ident(genre_col)}) = 'INCONNU'
            )
        """)
        debug_genre_unresolved_count = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT COUNT(*) FROM {staging_table}
            WHERE (
                {quote_sql_ident(genre_col)} IS NULL
                OR {quote_sql_ident(genre_col)} = ''
                OR {quote_sql_ident(genre_col)} = 'NULL'
                OR UPPER({quote_sql_ident(genre_col)}) = 'INCONNU'
            )
            AND {quote_sql_ident(prenom_col)} IS NOT NULL
            AND {quote_sql_ident(prenom_col)} <> ''
        """)
        debug_final_sql_count = cursor.fetchone()[0]

        print(f"[DEBUG export-unresolved] table={staging_table}")
        print(f"[DEBUG export-unresolved] resolved genre_col={genre_col!r} prenom_col={prenom_col!r}")
        print(f"[DEBUG export-unresolved] total rows in table: {debug_total_rows}")
        print(f"[DEBUG export-unresolved] rows with genre unresolved: {debug_genre_unresolved_count}")
        print(f"[DEBUG export-unresolved] rows with genre unresolved AND prenom present (SQL count): {debug_final_sql_count}")
        # --- END DEBUG ---

        # Fetch rows where GENRE is unresolved (NULL, empty, or 'INCONNU')
        # AND PRENOM is actually present (not NULL/empty) — no point exporting
        # a row for manual gender resolution if there's no first name to go on.
        query = f"""
            SELECT *
            FROM {staging_table}
            WHERE (
                {quote_sql_ident(genre_col)} IS NULL
                OR {quote_sql_ident(genre_col)} = ''
                OR {quote_sql_ident(genre_col)} = 'NULL'
                OR UPPER({quote_sql_ident(genre_col)}) = 'INCONNU'
            )
            AND {quote_sql_ident(prenom_col)} IS NOT NULL
            AND {quote_sql_ident(prenom_col)} <> ''
            ORDER BY {quote_sql_ident(pk)}
        """
        cursor.execute(query)

        col_names = [column[0] for column in cursor.description]
        rows = [dict(zip(col_names, row)) for row in cursor.fetchall()]

        print(f"[DEBUG export-unresolved] rows actually fetched via cursor.fetchall(): {len(rows)}")

        return jsonify({
            'status': 'success',
            'count': len(rows),
            'data': rows,
            'debug': {
                'total_rows_in_table': debug_total_rows,
                'genre_unresolved_count': debug_genre_unresolved_count,
                'final_sql_count': debug_final_sql_count,
                'rows_fetched': len(rows),
                'genre_col_used': genre_col,
                'prenom_col_used': prenom_col,
            }
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route('/api/v1/export-empty-prenom', methods=['POST'])
def export_empty_prenom_records():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')
    pk = data.get('pk', 'ID')

    if not database or not table:
        return jsonify({'status': 'error', 'message': 'Database and table parameters are required.'}), 400

    staging_table = resolve_staging_table(table)
    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()

        columns = list_table_columns(cursor, staging_table)
        prenom_col = find_named_column(columns, ALIAS_PRENOM) or 'PRENOM'
        nom_col = find_named_column(columns, ALIAS_NOM) or 'NOM'
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'

        query = f"""
            SELECT {quote_sql_ident(pk)} AS ID,
                {quote_sql_ident(nom_col)} AS NOM,
                {quote_sql_ident(genre_col)} AS GENRE            FROM {staging_table}
            WHERE (
                {quote_sql_ident(prenom_col)} IS NULL
                OR {quote_sql_ident(prenom_col)} = ''
                OR {quote_sql_ident(prenom_col)} = 'NULL'
            )
            AND {quote_sql_ident(nom_col)} IS NOT NULL
            AND {quote_sql_ident(nom_col)} <> ''
            AND (
                {quote_sql_ident(genre_col)} IS NULL
                OR {quote_sql_ident(genre_col)} = ''
                OR {quote_sql_ident(genre_col)} = 'NULL'
            )
            ORDER BY {quote_sql_ident(pk)}
        """

        cursor.execute(query)

        col_names = [column[0] for column in cursor.description]
        rows = [dict(zip(col_names, row)) for row in cursor.fetchall()]

        return jsonify({
            'status': 'success',
            'count': len(rows),
            'data': rows
        })

    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
# One-off diagnostic route — safe to delete after debugging.
@app.route('/api/v1/debug-prenom-breakdown', methods=['POST'])
def debug_prenom_breakdown():
    data = request.json or {}
    server = data.get('server', 'localhost')
    database = data.get('database')
    table = data.get('table')
    user = data.get('user')
    password = data.get('password')

    staging_table = resolve_staging_table(table)
    conn = None
    try:
        conn = get_db_connection(server, database, user, password)
        cursor = conn.cursor()
        columns = list_table_columns(cursor, staging_table)
        genre_col = find_named_column(columns, ALIAS_GENRE) or 'GENRE'
        prenom_col = find_named_column(columns, ALIAS_PRENOM) or 'PRENOM'

        genre_filter = f"""(
            {quote_sql_ident(genre_col)} IS NULL
            OR {quote_sql_ident(genre_col)} = ''
            OR {quote_sql_ident(genre_col)} = 'NULL'
            OR UPPER({quote_sql_ident(genre_col)}) = 'INCONNU'
        )"""

        breakdown = {}
        cursor.execute(f"SELECT COUNT(*) FROM {staging_table} WHERE {genre_filter} AND {quote_sql_ident(prenom_col)} IS NULL")
        breakdown['prenom_is_true_null'] = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {staging_table} WHERE {genre_filter} AND {quote_sql_ident(prenom_col)} = ''")
        breakdown['prenom_is_empty_string'] = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {staging_table} WHERE {genre_filter} AND {quote_sql_ident(prenom_col)} = 'NULL'")
        breakdown['prenom_is_literal_NULL_text'] = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {staging_table} WHERE {genre_filter} AND LTRIM(RTRIM({quote_sql_ident(prenom_col)})) = '' AND {quote_sql_ident(prenom_col)} IS NOT NULL")
        breakdown['prenom_is_whitespace_only'] = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM {staging_table} WHERE {genre_filter} AND {quote_sql_ident(prenom_col)} IS NOT NULL AND {quote_sql_ident(prenom_col)} <> ''")
        breakdown['prenom_present_current_check'] = cursor.fetchone()[0]

        cursor.execute(f"SELECT TOP 10 {quote_sql_ident(prenom_col)} FROM {staging_table} WHERE {genre_filter} AND ({quote_sql_ident(prenom_col)} IS NULL OR {quote_sql_ident(prenom_col)} = '')")
        sample_empty = [r[0] for r in cursor.fetchall()]
        breakdown['sample_of_empty_prenom_values'] = [repr(v) for v in sample_empty]

        print("[DEBUG breakdown]", breakdown)
        return jsonify({'status': 'success', 'breakdown': breakdown})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()