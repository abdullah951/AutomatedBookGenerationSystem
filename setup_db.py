"""
Run the SQL migration against Supabase via the Management API.

Usage:
  python setup_db.py --token YOUR_SUPABASE_ACCESS_TOKEN

To get your access token:
  1. Go to https://supabase.com/dashboard/account/tokens
  2. Create a new access token
  3. Paste it here (or set SUPABASE_ACCESS_TOKEN in .env)

Alternatively, copy-paste migrations/001_initial_schema.sql directly
into the Supabase Dashboard -> SQL Editor -> New query -> Run.
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL', '')
# Project ref is the subdomain portion of SUPABASE_URL
PROJECT_REF = SUPABASE_URL.replace('https://', '').split('.')[0]

MIGRATION_FILE = 'migrations/001_initial_schema.sql'


def run_via_management_api(access_token: str):
    with open(MIGRATION_FILE) as f:
        sql = f.read()

    url = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    resp = requests.post(url, json={'query': sql}, headers=headers, timeout=30)
    if resp.status_code in (200, 201):
        print("Migration applied successfully via Management API.")
    else:
        print(f"Management API returned {resp.status_code}: {resp.text}")
        sys.exit(1)


def check_tables_exist() -> bool:
    """Quick check via PostgREST — returns True if book_requests table exists."""
    from src.supabase_client import supabase
    try:
        supabase.table('book_requests').select('id').limit(1).execute()
        return True
    except Exception:
        return False


if __name__ == '__main__':
    if check_tables_exist():
        print("Tables already exist — nothing to do.")
        sys.exit(0)

    # Parse --token from CLI or env
    token = os.getenv('SUPABASE_ACCESS_TOKEN')
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == '--token' and i + 1 < len(args):
            token = args[i + 1]

    if not token:
        print(
            "Tables not found. To create them, either:\n"
            "  A) Run:  python setup_db.py --token <your-supabase-access-token>\n"
            "           (get token at https://supabase.com/dashboard/account/tokens)\n"
            "  B) Paste migrations/001_initial_schema.sql into Supabase Dashboard > SQL Editor > Run\n"
        )
        sys.exit(1)

    run_via_management_api(token)
    print("Done. Re-run your pipeline.")
