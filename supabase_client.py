from supabase import create_client
from .config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_BUCKET

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in the environment")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── book_requests ─────────────────────────────────────────────────────────────

def insert_book_request(data: dict):
    resp = supabase.table('book_requests').insert(data).execute()
    return resp.data


def update_outline(book_id: int, outline):
    resp = supabase.table('book_requests').update({'outline': outline}).eq('id', book_id).execute()
    return resp.data


def update_book_status(book_id: int, status: str):
    resp = supabase.table('book_requests').update({'book_output_status': status}).eq('id', book_id).execute()
    return resp.data


def fetch_request_by_id(book_id: int):
    resp = supabase.table('book_requests').select('*').eq('id', book_id).single().execute()
    return resp.data


def fetch_all_requests():
    resp = supabase.table('book_requests').select('*').execute()
    return resp.data or []


def get_pending_requests():
    """Return all requests that are not yet complete or in permanent error."""
    all_reqs = supabase.table('book_requests').select('*').execute().data or []
    return [r for r in all_reqs if r.get('book_output_status') not in ('ready', 'error')]


# ── chapters ──────────────────────────────────────────────────────────────────

def create_chapters_for_book(book_id: int, count: int = 3):
    existing = supabase.table('chapters').select('chapter_number').eq('book_request_id', book_id).execute().data or []
    existing_nums = {r['chapter_number'] for r in existing}
    to_insert = [
        {'book_request_id': book_id, 'chapter_number': n, 'status': 'pending'}
        for n in range(1, count + 1)
        if n not in existing_nums
    ]
    if to_insert:
        supabase.table('chapters').insert(to_insert).execute()


def get_chapters(book_id: int):
    resp = (supabase.table('chapters')
            .select('*')
            .eq('book_request_id', book_id)
            .order('chapter_number', desc=False)
            .execute())
    return resp.data or []


def update_chapter(book_id: int, chapter_number: int, fields: dict):
    resp = (supabase.table('chapters')
            .update(fields)
            .eq('book_request_id', book_id)
            .eq('chapter_number', chapter_number)
            .execute())
    return resp.data


def aggregate_summaries(book_id: int, upto: int) -> str:
    """Return newline-joined summaries for chapters 1..upto."""
    if upto <= 0:
        return ''
    resp = (supabase.table('chapters')
            .select('chapter_number,chapter_summary')
            .eq('book_request_id', book_id)
            .lte('chapter_number', upto)
            .order('chapter_number', desc=False)
            .execute())
    rows = resp.data or []
    summaries = [r.get('chapter_summary') or '' for r in rows]
    return "\n\n".join(s for s in summaries if s)


# ── storage ───────────────────────────────────────────────────────────────────

def upload_file_to_storage(file_path: str, dest_path: str, bucket: str = None):
    bucket = bucket or SUPABASE_BUCKET
    with open(file_path, 'rb') as f:
        data = f.read()
    resp = supabase.storage.from_(bucket).upload(dest_path, data)
    return resp


# ── logging ───────────────────────────────────────────────────────────────────

def log_notification(book_id: int, event_type: str, details: dict):
    payload = {'book_request_id': book_id, 'event_type': event_type, 'details': details}
    return supabase.table('notifications').insert(payload).execute()


def log_audit(book_id: int, action: str, payload: dict = None):
    data = {'book_request_id': book_id, 'action': action, 'payload': payload or {}}
    return supabase.table('audit_logs').insert(data).execute()
