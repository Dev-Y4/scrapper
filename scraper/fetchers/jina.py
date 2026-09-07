from __future__ import annotations

import os
from typing import Dict, Optional

import requests

from scraper.fetchers.base import Fetcher

JINA_BASE = "https://r.jina.ai/"


class JinaFetcher(Fetcher):
    """Reader proxy. html mode returns the full page including __NEXT_DATA__.
    Works without a key; a key only raises the rate limit."""

    name = "jina"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 45):
        self.api_key = api_key if api_key is not None else os.getenv("JINA_API_KEY", "")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {"X-Return-Format": "html", "X-Timeout": "40"}
        if self.api_key:
            headers["Authorization"] = "Bearer {0}".format(self.api_key)
        return headers

    def fetch(self, url: str) -> str:
        try:
            response = requests.get(JINA_BASE + url, headers=self._headers(),
                                    timeout=self.timeout)
            response.raise_for_status()
            return response.text
        except Exception:
            # An empty body classifies as TRANSIENT_BLOCK; the pool decides
            # whether to retry or fail over.
            return ""
