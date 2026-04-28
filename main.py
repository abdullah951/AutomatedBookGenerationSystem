"""
Automated Book Generation System — pipeline runner.

This is the main entry point. It does two things:
  1. Ingests book requests from an Excel/CSV file into Supabase.
  2. Runs the book generation pipeline for all pending requests.

The pipeline is a STATE MACHINE — each book request moves through these states:
  pending → outline_generated → (waiting_outline_notes) → chapters running
          → (waiting_final_notes) → compiling → ready

If a book is paused waiting for human input, it stays in its current state.
The next time the pipeline runs (via polling), it re-checks the state and
continues from where it left off — it never re-generates work already done.

Usage:
  # Ingest Excel then run pipeline once:
  python -m src.main sample_requests.xlsx

  # Ingest only (no pipeline run):
  python -m src.main --ingest sample_requests.xlsx

  # Run pipeline once without ingesting:
  python -m src.main --run-once

  # Ingest then keep polling every POLL_INTERVAL_SECONDS:
  python -m src.main sample_requests.xlsx --poll

  # Poll continuously without ingesting:
  python -m src.main --poll
"""
import os
import sys
import time

from .config import EMAIL_FROM, POLL_INTERVAL_SECONDS
from .compile_docx import compile_book_to_docx
from .generator import generate_chapter, generate_outline, summarize_chapter
from .ingest_excel import ingest_from_csv, ingest_from_xlsx
from .notifications import send_email
from .supabase_client import (
    aggregate_summaries,
    create_chapters_for_book,
    fetch_request_by_id,
    get_chapters,
    get_pending_requests,
    log_audit,
    log_notification,
    update_book_status,
    update_chapter,
    update_outline,
    upload_file_to_storage,
)

# ── Pipeline state reference ───────────────────────────────────────────────────
# pending               → fresh row just inserted from Excel, nothing generated yet
# outline_generated     → outline saved to DB, chapter generation can begin
# waiting_outline_notes → outline exists but editor requested review (status_outline_notes='yes')
#                         pipeline stops here until editor sets status_outline_notes='no_notes_needed'
# waiting_final_notes   → all chapters done, editor requested final review (final_review_notes_status='yes')
#                         pipeline stops here until editor sets final_review_notes_status='no_notes_needed'
# compiling             → .docx is being assembled right now
# ready                 → book fully compiled, nothing left to do
# paused                → missing required input; editor must fix the DB row
# error                 → an unhandled exception occurred; see audit_logs table


# ── Notification helper ────────────────────────────────────────────────────────

def _notify(req: dict, event_type: str, message: str):
    """
    Send an email notification AND write a record to the notifications table.

    Called at every pipeline stage that requires human attention:
      - Outline ready for review
      - Chapter waiting for notes
      - Final draft compiled
      - Pipeline paused due to missing input

    If the book_request row has an 'editor_email' column set, that address
    is used. Otherwise falls back to the global EMAIL_FROM address in .env.
    Email failures are caught silently so they never crash the pipeline.
    """
    req_id = req['id']

    # Use per-book editor email if set, otherwise use the default sender address
    recipient = req.get('editor_email') or EMAIL_FROM

    # Build a readable subject line from the event type (e.g. 'outline_ready' → 'Outline Ready')
    subject = f"[BookBot] {event_type.replace('_', ' ').title()} — {req.get('title', '')}"
    html = f"<p>{message}</p><p><strong>Book ID:</strong> {req_id}</p>"

    # Try sending email — failure is logged but never raises so pipeline continues
    try:
        send_email(recipient, subject, html)
    except Exception as exc:
        print(f"    [email failed] {exc}")

    # Also write notification to Supabase for audit trail / dashboard visibility
    try:
        log_notification(req_id, event_type, {'message': message})
    except Exception:
        pass


# ── Core pipeline function ─────────────────────────────────────────────────────

def process_request(req: dict):
    """
    Run the pipeline for a single book request.

    This function is IDEMPOTENT — it can be called multiple times on the same
    request and it will only do the work that hasn't been done yet.
    It reads the current state from the DB (via req dict) and picks up from
    the correct stage automatically.

    Stages in order:
      1. Outline generation
      2. (Optional) Wait for editor outline notes → regenerate outline
      3. Chapter generation (sequential, one chapter at a time)
      4. (Optional) Wait for final review sign-off
      5. Compile .docx and upload to Supabase Storage

    Parameters:
      req  — a dict row from the book_requests Supabase table
    """
    req_id = req['id']
    title = req.get('title') or f'Book {req_id}'

    # Read the current pipeline state from the DB row
    status = req.get('book_output_status') or 'pending'

    print(f"[{req_id}] '{title}' — status: {status}")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 1: OUTLINE GENERATION
    # ══════════════════════════════════════════════════════════════════════════
    # Only runs if there is no outline yet in the DB.
    # If outline already exists (from a previous run), this whole block is skipped.

    if not req.get('outline'):

        # Guard: outline generation requires editor notes to guide the AI.
        # If notes_on_outline_before is empty, we can't proceed — pause the book.
        if not req.get('notes_on_outline_before'):
            print("  No notes_on_outline_before → paused")
            update_book_status(req_id, 'paused')
            _notify(req, 'paused_missing_input',
                    "Paused: <code>notes_on_outline_before</code> is required to generate the outline. "
                    "Please add it in Supabase and the pipeline will resume on the next poll.")
            return  # Stop processing this request

        # Call Gemini to generate the outline.
        # Returns a dict like: {"chapters": [{"title": "...", "bullets": [...]}, ...]}
        print("  Generating outline…")
        outline = generate_outline(title, req['notes_on_outline_before'])

        # Persist the outline to the book_requests table in Supabase
        update_outline(req_id, outline)

        # Update the in-memory req dict so later stages can use the outline
        # without a second DB fetch
        req['outline'] = outline

        # Write an audit log entry so we have a timestamped record of generation
        log_audit(req_id, 'outline_generated')

        # Check if the editor wants to review the outline before chapters start.
        # status_outline_notes='yes' means "pause here and wait for my feedback".
        if req.get('status_outline_notes') == 'yes':
            update_book_status(req_id, 'waiting_outline_notes')
            _notify(req, 'outline_ready_needs_review',
                    f"Outline generated for <strong>{title}</strong>. "
                    "Please review it in Supabase, add <code>notes_on_outline_after</code> if desired, "
                    "then set <code>status_outline_notes = no_notes_needed</code> to continue.")
            return  # Stop — wait for editor to respond

        # No review needed — proceed directly to chapter generation
        update_book_status(req_id, 'outline_generated')
        status = 'outline_generated'

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 2: RESUME FROM WAITING_OUTLINE_NOTES
    # ══════════════════════════════════════════════════════════════════════════
    # This block only runs if the book was previously paused waiting for the
    # editor's outline feedback. On the next poll we re-check whether the editor
    # has given the go-ahead (status_outline_notes = 'no_notes_needed').

    if status == 'waiting_outline_notes':

        # Re-fetch the latest DB row — the editor may have changed fields
        # since the last time we read this request
        fresh = fetch_request_by_id(req_id)

        # Not yet approved — editor hasn't changed the status, keep waiting
        if fresh.get('status_outline_notes') != 'no_notes_needed':
            print("  Still waiting for outline notes.")
            return

        # Editor approved. If they also added notes_on_outline_after,
        # regenerate the outline incorporating those refinement notes.
        if fresh.get('notes_on_outline_after'):
            print("  Regenerating outline with after-notes…")
            outline = generate_outline(
                title,
                fresh.get('notes_on_outline_before', ''),
                fresh['notes_on_outline_after'],  # passed as second prompt layer
            )
            update_outline(req_id, outline)
            log_audit(req_id, 'outline_regenerated')
        else:
            # No after-notes — use the original outline already in the DB
            outline = fresh.get('outline') or req.get('outline') or {}

        # Replace req with the fresh row so all subsequent reads are up to date
        req = fresh
        req['outline'] = outline

        update_book_status(req_id, 'outline_generated')
        status = 'outline_generated'

    # Extract outline from req for use in chapter generation below
    outline = req.get('outline') or {}

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 3: CHAPTER GENERATION
    # ══════════════════════════════════════════════════════════════════════════
    # Chapters are generated ONE AT A TIME in order (1, 2, 3, ...).
    # Each chapter gets the summaries of all previous chapters as context,
    # so the AI "remembers" what was written before and stays consistent.
    # If a chapter is already status='ready' it is skipped (idempotent).

    # Determine how many chapters to create based on the outline
    # (number of entries in the outline's 'chapters' list).
    # Falls back to 3 if the outline couldn't be parsed.
    chapter_list = outline.get('chapters', []) if isinstance(outline, dict) else []
    chapter_count = len(chapter_list) if chapter_list else 3

    # Create placeholder rows in the chapters table (skips ones that already exist)
    create_chapters_for_book(req_id, count=chapter_count)

    # Fetch all chapter rows ordered by chapter_number ascending
    chapters = get_chapters(req_id)

    # The global default: if no per-chapter override, use the book-level setting
    global_ch_status = req.get('chapter_notes_status') or 'no_notes_needed'

    for ch in chapters:

        # Skip chapters that have already been successfully generated
        if ch.get('status') == 'ready':
            continue

        n = ch['chapter_number']

        # Per-chapter notes_status overrides the global book-level setting.
        # This lets the editor request review on specific chapters only.
        ch_notes_status = ch.get('chapter_notes_status') or global_ch_status

        # Editor wants to review this chapter before we generate it.
        # Pause here until they add chapter_notes and flip the status.
        if ch_notes_status == 'yes' and not ch.get('chapter_notes'):
            print(f"  Chapter {n} — waiting for editor notes")
            update_chapter(req_id, n, {'status': 'needs_review'})
            _notify(req, f'waiting_chapter_{n}_notes',
                    f"Chapter {n} of <strong>{title}</strong> is ready for your notes. "
                    f"Add <code>chapter_notes</code> for chapter {n} in Supabase and set its "
                    "<code>chapter_notes_status = no_notes_needed</code> to continue.")
            return  # Stop — don't generate any further chapters until this one is resolved

        # status='no' means the editor explicitly blocked this chapter — pause the whole book
        if ch_notes_status == 'no':
            print(f"  Chapter {n} — paused (chapter_notes_status=no)")
            update_book_status(req_id, 'paused')
            _notify(req, 'paused_chapter_notes',
                    f"Pipeline paused at chapter {n}: <code>chapter_notes_status</code> is 'no'. "
                    "Set it to 'no_notes_needed' (or 'yes' to add notes) to resume.")
            return

        # Resolve the chapter title:
        # Priority: 1) manually set title in DB row, 2) title from outline, 3) generic fallback
        if len(chapter_list) >= n:
            fallback_title = chapter_list[n - 1].get('title', f'Chapter {n}')
        else:
            fallback_title = f'Chapter {n}'
        chapter_title = ch.get('chapter_title') or fallback_title

        # Context chaining: collect summaries of all previously generated chapters.
        # This text is injected into the prompt so the AI writes consistently.
        # For chapter 1, prev_summaries is empty string (no previous chapters).
        prev_summaries = aggregate_summaries(req_id, upto=n - 1)

        # Optional editor notes specific to this chapter
        chapter_notes = ch.get('chapter_notes') or ''

        # Call Gemini to write the chapter (~1000 words by default)
        print(f"  Generating Chapter {n}: {chapter_title}…")
        content = generate_chapter(title, outline, n, chapter_title, prev_summaries, chapter_notes)

        # Immediately summarize the chapter and store the summary.
        # This summary is what gets passed to future chapters as context — NOT the full text.
        # This keeps token usage manageable as the book grows.
        summary = summarize_chapter(content)

        # Persist chapter content, summary, and mark as ready
        update_chapter(req_id, n, {
            'chapter_title': chapter_title,
            'chapter_content': content,
            'chapter_summary': summary,
            'status': 'ready',
        })
        log_audit(req_id, f'chapter_{n}_generated')
        print(f"  Chapter {n} saved.")

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 4: FINAL REVIEW GATE
    # ══════════════════════════════════════════════════════════════════════════
    # After all chapters are generated, optionally pause for a final human review
    # before compiling the .docx. This is controlled by final_review_notes_status.

    final_status = req.get('final_review_notes_status') or 'no_notes_needed'

    # If previously paused here, re-check whether the editor has signed off
    if status == 'waiting_final_notes':
        fresh = fetch_request_by_id(req_id)
        if fresh.get('final_review_notes_status') != 'no_notes_needed':
            print("  Still waiting for final review notes.")
            return
        # Editor approved — update local variables and fall through to compile
        req = fresh
        final_status = 'no_notes_needed'

    # Editor wants to review before compiling — pause and notify
    if final_status == 'yes':
        update_book_status(req_id, 'waiting_final_notes')
        _notify(req, 'waiting_final_review',
                f"All chapters for <strong>{title}</strong> are complete. "
                "Please review and then set <code>final_review_notes_status = no_notes_needed</code> to compile.")
        return

    # Editor explicitly blocked compilation — pause
    if final_status == 'no':
        update_book_status(req_id, 'paused')
        _notify(req, 'paused_final_review',
                f"Final compilation paused for <strong>{title}</strong>. "
                "Set <code>final_review_notes_status = no_notes_needed</code> to proceed.")
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 5: COMPILE .DOCX
    # ══════════════════════════════════════════════════════════════════════════
    # All chapters are ready and all gates passed — build the final document.

    print("  Compiling .docx…")
    update_book_status(req_id, 'compiling')

    # Re-fetch the full request row fresh from DB so outline and all fields
    # reflect the final state (avoids stale in-memory data from earlier in this run)
    req_full = fetch_request_by_id(req_id)

    # Build the .docx file locally in the outputs/ folder.
    # compile_docx also fetches all chapter rows internally via get_chapters().
    path = compile_book_to_docx(req_full)

    # Optionally upload to Supabase Storage for remote access.
    # Stored at: books/{book_id}/{filename}.docx
    dest = f"books/{req_id}/{os.path.basename(path)}"
    try:
        upload_file_to_storage(path, dest)
        print(f"  Uploaded to storage: {dest}")
    except Exception as exc:
        # Upload failure is non-fatal — the local file is still usable
        print(f"  Storage upload skipped: {exc}")

    # Mark the book as fully done
    update_book_status(req_id, 'ready')

    # Write to notifications table (for audit/dashboard) and send final email
    log_notification(req_id, 'compiled', {'local_path': path, 'storage_path': dest})
    _notify(req, 'book_compiled',
            f"<strong>{title}</strong> has been compiled successfully! "
            f"Local file: <code>{path}</code>. Storage path: <code>{dest}</code>.")
    print(f"  Done — output: {path}")


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_pipeline():
    """
    Fetch all pending book requests from Supabase and process each one.

    A request is considered 'pending' if its book_output_status is anything
    other than 'ready' or 'error'. This includes paused books so they can
    be automatically unpaused when the editor fixes the missing input.

    Errors on individual books are caught here so one broken book doesn't
    stop the pipeline from processing other books.
    """
    requests = get_pending_requests()

    if not requests:
        print("No pending requests.")
        return

    for req in requests:
        try:
            process_request(req)
        except Exception as exc:
            # Catch unexpected errors, mark the book as 'error' and log it
            print(f"  ERROR for request {req.get('id')}: {exc}")
            try:
                update_book_status(req['id'], 'error')
                log_audit(req['id'], 'error', {'message': str(exc)})
            except Exception:
                pass


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    """
    Parse CLI arguments and run the appropriate mode.

    Modes:
      python -m src.main file.xlsx          → ingest + run pipeline once
      python -m src.main file.xlsx --poll   → ingest + poll continuously
      python -m src.main --ingest file.xlsx → ingest only, no pipeline
      python -m src.main --run-once         → pipeline once, no ingest
      python -m src.main --poll             → poll continuously, no ingest
    """
    args = sys.argv[1:]

    xlsx_path = None
    do_poll = False
    run_once = False
    ingest_only = False

    # Parse arguments manually (avoids importing argparse for simplicity)
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--poll':
            do_poll = True
        elif a == '--run-once':
            run_once = True
        elif a == '--ingest':
            ingest_only = True
            i += 1
            if i < len(args):
                xlsx_path = args[i]
        elif not a.startswith('--'):
            # Positional argument — treat as the input file path
            xlsx_path = a
        i += 1

    # Step 1: Ingest from file if a path was provided
    if xlsx_path:
        print(f"Ingesting from {xlsx_path}…")
        if xlsx_path.endswith('.csv'):
            ingest_from_csv(xlsx_path)
        else:
            ingest_from_xlsx(xlsx_path)

    # Step 2: Exit after ingest if --ingest flag was used
    if ingest_only:
        return

    # Step 3: Run pipeline once (used for testing or one-shot execution)
    if run_once or (xlsx_path and not do_poll):
        run_pipeline()
        return

    # Step 4: Default — poll in a loop.
    # Each iteration fetches all pending requests and processes them.
    # Interval is configurable via POLL_INTERVAL_SECONDS in .env (default: 60s).
    # This is the human-in-the-loop mode: the pipeline sleeps between iterations,
    # and when an editor updates a field in Supabase, the next poll picks it up.
    print(f"Starting polling loop (interval: {POLL_INTERVAL_SECONDS}s). Press Ctrl+C to stop.")
    while True:
        run_pipeline()
        print(f"Sleeping {POLL_INTERVAL_SECONDS}s…")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
