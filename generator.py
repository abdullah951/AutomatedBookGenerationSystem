"""
generator.py — All AI generation logic lives here.

Three functions, each making one Gemini API call:
  1. generate_outline   → produces the chapter structure for the whole book
  2. generate_chapter   → writes one chapter using outline + previous summaries as context
  3. summarize_chapter  → condenses a chapter into 150-250 words for context chaining

All three call chat_completion() from openai_client.py, which is the single
point of contact with the Gemini API. Swapping the AI provider in the future
only requires changing openai_client.py — this file stays the same.
"""

from .openai_client import chat_completion
import json

# ── Outline generation ─────────────────────────────────────────────────────────

# System prompt tells Gemini its role and the exact JSON structure we expect back.
# Keeping the format instruction in the system prompt (not user prompt) makes
# the AI more reliably return well-formed JSON.
OUTLINE_SYSTEM = {
    "role": "system",
    "content": (
        "You are a helpful assistant that creates detailed book outlines. "
        "Always return valid JSON with a top-level key 'chapters', where each chapter "
        "has 'title' (string) and 'bullets' (list of 3-5 strings)."
    )
}


def generate_outline(title: str, notes_before: str, notes_after: str = '') -> dict:
    """
    Ask Gemini to produce a chapter-by-chapter outline for the book.

    The AI decides the number of chapters based on the topic and editor notes.
    If the editor provides after-notes (notes_after), they are appended to the
    prompt so the regenerated outline incorporates their feedback.

    Parameters:
      title        — the book title (mandatory)
      notes_before — editor guidance written BEFORE the first outline attempt
                     (e.g. "keep it practical, 5 chapters max")
      notes_after  — optional refinement notes written AFTER reviewing the first
                     outline (e.g. "add more detail on chapter 3, merge 4 and 5")
                     Only used when regenerating an existing outline.

    Returns:
      dict with shape: {"chapters": [{"title": "...", "bullets": ["...", ...]}, ...]}
      On parse failure: {"raw_outline": "<raw text>", "chapters": []}
    """
    # Combine both sets of notes into one prompt block.
    # notes_after is clearly labeled so the AI knows it's a refinement request.
    notes = notes_before or ''
    if notes_after:
        notes += f"\n\nAdditional editor refinement notes (please incorporate): {notes_after}"

    # User message: provides the book title, combined notes, and tells the AI
    # exactly what JSON shape to return (no markdown, no explanation — just JSON).
    user = {
        "role": "user",
        "content": (
            f"Title: {title}\n"
            f"Editor notes: {notes}\n\n"
            "Decide on an appropriate number of chapters based on the topic and notes. "
            "Return valid JSON only — no markdown fences — with this shape:\n"
            '{"chapters": [{"title": "...", "bullets": ["...", "..."]}, ...]}'
        )
    }

    # Send [system, user] message list to Gemini.
    # max_tokens=1200 gives enough room for a detailed multi-chapter outline.
    raw = chat_completion([OUTLINE_SYSTEM, user], max_tokens=1200)

    # Parse the JSON response.
    # We search for the first '{' because Gemini sometimes prepends a short
    # explanatory sentence before the JSON even when told not to.
    try:
        start = raw.index('{')
        return json.loads(raw[start:])
    except Exception:
        # If parsing fails, store the raw text so it isn't lost.
        # The pipeline will still work — chapter_count will fall back to 3.
        return {"raw_outline": raw, "chapters": []}


# ── Chapter generation ─────────────────────────────────────────────────────────

# System prompt establishes the AI as a consistent book-writing assistant.
# Same system prompt is reused for both generate_chapter and summarize_chapter
# so the AI maintains the same "voice" when writing and summarizing.
CHAPTER_SYSTEM = {
    "role": "system",
    "content": (
        "You are a professional book-writing assistant. "
        "Write clear, well-structured chapters that follow the provided outline "
        "and maintain narrative consistency with previous chapters."
    )
}


def generate_chapter(
    book_title: str,
    outline: dict,
    chapter_number: int,
    chapter_title: str,
    prev_summaries: str,
    chapter_notes: str = '',
    word_target: int = 1000,
) -> str:
    """
    Ask Gemini to write one chapter of the book.

    Context chaining:
      prev_summaries contains the 150-250 word summaries of all chapters
      written before this one. This is the key mechanism that gives the AI
      "memory" of the book so far — keeping chapters consistent in tone,
      facts, and narrative without sending the full text of every chapter
      (which would be too many tokens).

    Parameters:
      book_title      — used in the prompt so the AI keeps the title in mind
      outline         — the full book outline dict (all chapter titles + bullets)
                        so the AI knows where this chapter fits in the whole book
      chapter_number  — e.g. 3 (used to say "write Chapter 3")
      chapter_title   — the title for this specific chapter
      prev_summaries  — newline-joined summaries of chapters 1..N-1
                        empty string for chapter 1 (no prior chapters)
      chapter_notes   — optional editor instructions for this specific chapter
                        (e.g. "focus more on real-world examples")
      word_target     — approximate word count for the chapter (default 1000)

    Returns:
      str — the raw chapter text as written by Gemini
    """
    # Build the user prompt with all context packed in.
    # Providing the full outline (not just this chapter's bullets) lets the AI
    # avoid repeating content from other chapters and set up for future ones.
    user_content = (
        f"Book title: {book_title}\n"
        f"Chapter {chapter_number} title: {chapter_title}\n"
        f"Full outline:\n{json.dumps(outline, indent=2)}\n\n"
        # prev_summaries is the context chain — empty for chapter 1
        f"Previous chapter summaries:\n{prev_summaries or 'None — this is the first chapter.'}\n\n"
        f"Editor notes for this chapter: {chapter_notes or 'None.'}\n\n"
        f"Write approximately {word_target} words. "
        "Structure: short intro paragraph, 3-4 subsections with ### subheadings, brief conclusion paragraph."
    )
    user = {"role": "user", "content": user_content}

    # max_tokens=2000 allows ~1000-1500 words with some headroom
    return chat_completion([CHAPTER_SYSTEM, user], max_tokens=2000)


def summarize_chapter(chapter_text: str) -> str:
    """
    Ask Gemini to condense a chapter into a short summary (150-250 words).

    WHY this exists:
      When generating chapter N, we pass summaries of chapters 1..N-1 as
      context (not the full text). This prevents the prompt from growing
      too large as the book progresses. A 1000-word chapter becomes a
      ~200-word summary — 5x compression while keeping the key facts.

    The summary is stored in chapters.chapter_summary in Supabase and
    re-used on every subsequent chapter generation call.

    Parameters:
      chapter_text — the full text of the chapter just written

    Returns:
      str — a 150-250 word summary of the chapter
    """
    user = {
        "role": "user",
        "content": (
            "Summarize this chapter in 150-250 words, focusing on main points, "
            f"key facts, and narrative flow:\n\n{chapter_text}"
        )
    }
    # max_tokens=400 is enough for a 250-word summary with some margin
    return chat_completion([CHAPTER_SYSTEM, user], max_tokens=400)
