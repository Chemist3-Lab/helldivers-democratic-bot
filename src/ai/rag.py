"""RAG engine — semantic retrieval and Gemini synthesis.

Manages pre-embedded wiki chunks, performs cosine similarity retrieval,
and composes RAG prompts for the Ministry of Truth persona.

See ARCHITECTURE.md §3.3 for the query flow and §8.2 for the prompt template.
"""

from __future__ import annotations
