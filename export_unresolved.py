#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 EXPORT UNRESOLVED RECORDS (GENRE IS NULL)
==============================================================================
 - Connects to SQL Server staging table.
 - Fetches all rows where GENRE IS NULL / blank.
 - Saves unresolved records to a local CSV file.
==============================================================================
"""

import argparse
import csv
import sys
from pathlib import Path

try:
    import pyodbc
except ImportError:
    print("ERROR: 'pyodbc' library is missing. Install it using: pip install pyodbc")
    sys.exit(1)


def quote_ident(name: str) -> str:
    return "[" + str(name).replace("]", "]]") + "]"


def parse_table_name(table_name: str):
    raw = str(table_name).strip().replace("[", "").replace("]", "")
    parts = [p for p in raw.split(".") if p]
    if len(parts) == 1:
        return "dbo", parts[0]
    return parts[-2], parts[-1]


def build_full_table(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def main():
    parser = argparse.ArgumentParser(description="Export records where GENRE is NULL.")
    parser.add_argument("--serveur", required=True, help="SQL Server Name / Host")
    parser.add_argument("--base-donnees", required=True, help="Database Name")
    parser.add_argument("--table", required=True, help="Target SQL Server table name")
    parser.add_argument("--colonne-pk", default="ID", help="Primary Key column name")
    parser.add_argument("--trusted-connection", action="store_true", help="Use Windows Auth")
    parser.add_argument("--utilisateur", default="", help="SQL Username")
    parser.add_argument("--mot-de-passe", default="", help="SQL Password")
    parser.add_argument("--driver", default="ODBC Driver 17 for SQL Server", help="ODBC Driver Name")
    parser.add_argument("--colonne-genre", default="GENRE", help="Gender column name")
    parser.add_argument("--colonne-prenom", default="PRENOM", help="First name (prenom) column name")
    parser.add_argument("--output", default="unresolved_records.csv", help="Output CSV path")

    args = parser.parse_args()

    schema, clean_base = parse_table_name(args.table)
    if clean_base.lower().endswith("_staging"):
        staging_table = clean_base
    else:
        staging_table = f"{clean_base}_STAGING"

    staging_full = build_full_table(schema, staging_table)

    # Database connection
    driver = f"{{{args.driver}}}"
    if args.trusted_connection:
        conn_str = f"DRIVER={driver};SERVER={args.serveur};DATABASE={args.base_donnees};Trusted_Connection=yes;"
    else:
        conn_str = (
            f"DRIVER={driver};SERVER={args.serveur};DATABASE={args.base_donnees};"
            f"UID={args.utilisateur};PWD={args.mot_de_passe};"
        )

    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()

    genre_col = quote_ident(args.colonne_genre)
    prenom_col = quote_ident(args.colonne_prenom)
    pk_col = quote_ident(args.colonne_pk)

    query = f"""
        SELECT *
        FROM {staging_full}
        WHERE (
            {genre_col} IS NULL
            OR {genre_col} = ''
            OR {genre_col} = 'NULL'
            OR UPPER({genre_col}) = 'INCONNU'
        )
        AND {prenom_col} IS NOT NULL
        AND {prenom_col} <> ''
        ORDER BY {pk_col}
    """

    print(f"[INFO] Fetching unresolved records from {staging_full}...")
    cursor.execute(query)

    columns = [column[0] for column in cursor.description]
    rows = cursor.fetchall()

    # Write output to CSV
    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(list(row))

    cursor.close()
    conn.close()

    print(f"[SUCCESS] Exported {len(rows)} unresolved record(s) to '{output_path.resolve()}'")


if __name__ == "__main__":
    main()