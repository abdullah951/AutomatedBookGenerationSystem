"""
ingest_excel.py
----------------
Helpers to ingest book requests from CSV or XLSX files and insert them into
Supabase via `insert_book_request`.

This module performs minimal cleaning and normalization of input values and
expects the input sheet to contain columns (case-sensitive):
 - title (required)
 - notes_on_outline_before (optional, but required to proceed for outline generation)
 - status_outline_notes
 - chapter_notes_status
 - notes_on_outline_after
 - final_review_notes_status

Two public functions are provided:
 - `ingest_from_csv(path)`
 - `ingest_from_xlsx(path, sheet_name=0)`

Both return a list of inserted Supabase responses for the rows that were
successfully inserted.
"""

import pandas as pd
from .supabase_client import insert_book_request


def _clean(val) -> str | None:
    """Normalize a raw cell value to a clean string or None.

    Rules:
    - If the value is None or an empty string (after strip), return None.
    - Treat literal strings 'nan' or 'none' (case-insensitive) as empty.
    - Otherwise return the stripped string.
    """
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ('nan', 'none', '') else None


def _ingest_df(df: pd.DataFrame):
    """Insert rows from a DataFrame into Supabase.

    The DataFrame is iterated row-by-row; rows missing a `title` are skipped
    and logged to stdout. Each inserted row produces a call to
    `insert_book_request` from the Supabase helper module.
    """
    inserted = []

    # Iterate rows to allow per-row validation and reporting
    for idx, row in df.iterrows():
        title = _clean(row.get('title'))
        if not title:
            # Skip rows without a valid title — title is mandatory
            print(f"Row {idx}: skipped — missing title")
            continue

        # Build the payload dict expected by `insert_book_request`.
        # We normalize values with _clean() and provide a default for
        # `chapter_notes_status` when not supplied.
        data = {
            'title': title,
            'notes_on_outline_before':   _clean(row.get('notes_on_outline_before')),
            'notes_on_outline_after':    _clean(row.get('notes_on_outline_after')),
            'status_outline_notes':      _clean(row.get('status_outline_notes')),
            'chapter_notes_status':      _clean(row.get('chapter_notes_status')) or 'no_notes_needed',
            'final_review_notes_status': _clean(row.get('final_review_notes_status')),
        }

        # Insert into Supabase and keep the response for callers/tests
        resp = insert_book_request(data)
        inserted.append(resp)
        print(f"Inserted: {title}")

    return inserted


def ingest_from_csv(path='sample_requests.csv'):
    """Read a CSV file into a DataFrame and ingest rows."""
    return _ingest_df(pd.read_csv(path))


def ingest_from_xlsx(path='sample_requests.xlsx', sheet_name=0):
    """Read an Excel file (first sheet by default) and ingest rows."""
    return _ingest_df(pd.read_excel(path, sheet_name=sheet_name))
