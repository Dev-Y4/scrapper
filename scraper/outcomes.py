from __future__ import annotations

from enum import Enum

from scraper.platforms.trustpilot.parser import page_props

MIN_REAL_PAGE_BYTES = 5000
GATE_MARKERS = ("__N_REDIRECT", "/users/connect")
CHALLENGE_MARKERS = ("Verifying Connection", "Verifying your connection")


class Outcome(Enum):
    OK = "ok"
    TRANSIENT_BLOCK = "transient_block"
    GATED = "gated"
    EMPTY = "empty"

    @property
    def retryable(self) -> bool:
        """Only a transient block is worth trying again. GATED is a product
        gate: no proxy, fetcher, IP or credential moves it."""
        return self is Outcome.TRANSIENT_BLOCK


def classify(html: str) -> Outcome:
    text = html or ""

    if all(marker in text for marker in GATE_MARKERS):
        return Outcome.GATED

    props = page_props(text)
    if props is not None:
        if props.get("__N_REDIRECT"):
            return Outcome.GATED
        return Outcome.OK if props.get("reviews") else Outcome.EMPTY

    # No usable page: a challenge, a truncated body, or an empty response.
    # All three are worth another try from another path.
    return Outcome.TRANSIENT_BLOCK
