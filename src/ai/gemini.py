"""Gemini API wrapper — chat, vision, and embeddings.

Thin async wrapper around the google-genai SDK providing:
- Chat completions with the Ministry of Truth persona.
- Vision extraction for scoreboard screenshots.
- Text embeddings for wiki RAG.

See ARCHITECTURE.md §6.1 for async safety requirements and
§8.3 for the vision extraction prompt specification.
"""

from __future__ import annotations
