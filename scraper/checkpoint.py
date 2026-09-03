from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List

from scraper.models import Review


class Checkpoint:
    """Mid-run safety net. Not the store — the sheet is. This only stops a
    crash from throwing away pages that were already fetched."""

    def __init__(self, path: str):
        self.path = path
        self._rows: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []

    def reviews(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def extend(self, reviews: List[Review]) -> None:
        self._rows.extend(asdict(review) for review in reviews)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self._rows, handle, ensure_ascii=False, indent=2)

    def clear(self) -> None:
        self._rows = []
        try:
            os.remove(self.path)
        except OSError:
            pass
