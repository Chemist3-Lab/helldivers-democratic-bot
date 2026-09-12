"""Backward compatibility alias for wiki_client."""

from src.services.wiki_client import WikiClient, WikiArticle, WikiSearchResult

__all__ = ["WikiClient", "WikiArticle", "WikiSearchResult"]
