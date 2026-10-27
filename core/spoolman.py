"""
Thin client for the Spoolman REST API (v1).

Fetches spool data as JSON. We render labels ourselves from this data -- Spoolman
does not expose a label-image endpoint.
"""

from __future__ import annotations

import httpx


class SpoolmanError(Exception):
    pass


class SpoolmanClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def info(self) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(self._url("/info"))
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise SpoolmanError(f"Could not reach Spoolman at {self.base_url}: {e}") from e

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(self._url("/health"))
                return r.status_code == 200 and r.json().get("status") == "healthy"
        except httpx.HTTPError:
            return False

    def spools(self, include_archived: bool = False) -> list[dict]:
        params = {}
        if not include_archived:
            params["allow_archived"] = "false"
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(self._url("/spool"), params=params)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise SpoolmanError(f"Failed to fetch spools: {e}") from e

    def spool(self, spool_id: int) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(self._url(f"/spool/{spool_id}"))
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise SpoolmanError(f"Failed to fetch spool {spool_id}: {e}") from e


def summarize_spool(spool: dict) -> dict:
    """Flatten a Spoolman spool into the fields a label needs, tolerant of
    missing values (Spoolman data is often partial)."""
    filament = spool.get("filament") or {}
    vendor = filament.get("vendor") or {}
    return {
        "id": spool.get("id"),
        "vendor": vendor.get("name") or "",
        "material": filament.get("material") or "",
        "name": filament.get("name") or "",
        "color_hex": filament.get("color_hex") or "",
        "remaining_weight": spool.get("remaining_weight"),
        "location": spool.get("location") or "",
        "archived": spool.get("archived", False),
    }
