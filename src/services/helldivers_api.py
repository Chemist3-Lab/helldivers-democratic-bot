"""Backward compatibility alias for hd2_api."""

from src.services.hd2_api import HD2ApiClient, MajorOrder, Dispatch, Campaign, PlanetInfo

__all__ = ["HD2ApiClient", "MajorOrder", "Dispatch", "Campaign", "PlanetInfo"]
