from google import genai
from google.genai import types
from .config import GEMINI_API_KEY

# This module provides a thin wrapper around Google's GenAI (Gemini) client.
# It expects `GEMINI_API_KEY` to be present in the environment (loaded via `config.py`).
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY must be set in the environment")

# Create a long-lived client instance for use by the application.
_client = genai.Client(api_key=GEMINI_API_KEY)


def chat_completion(messages, model='gemini-2.0-flash', temperature=0.2, max_tokens=800):
    """
    Generate a conversational completion using Gemini.

    Args:
        messages (list): list of dicts with keys `role` and `content` similar to OpenAI-style messages.
                         Roles supported here: 'system', 'user', and other roles treated as 'model'.
        model (str): Gemini model name to use.
        temperature (float): sampling temperature.
        max_tokens (int): maximum number of output tokens.

    Returns:
        str: the generated text response from Gemini.
    """

    # Gather system instructions into a single string. Gemini's client accepts a
    # dedicated `system_instruction` field in the generation config.
    system_parts = [m['content'] for m in messages if m['role'] == 'system']
    system_instruction = '\n'.join(system_parts) if system_parts else None

    # Build the list of `Content` objects for Gemini. Skip system messages here
    # because they were promoted to `system_instruction` in the config.
    contents = []
    for m in messages:
        if m['role'] == 'system':
            continue
        # Map roles to Gemini-friendly labels. Treat unknown roles as 'model'.
        role = 'user' if m['role'] == 'user' else 'model'
        contents.append(types.Content(role=role, parts=[types.Part(text=m['content'])]))

    # Configure generation behavior. `max_output_tokens` limits the response size.
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )

    # Call Gemini and return the plain text result. The client returns a rich
    # response object; use `response.text` to get the combined generated text.
    response = _client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    return response.text
