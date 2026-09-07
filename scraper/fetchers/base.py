from __future__ import annotations


class Fetcher:
    """One way of getting a page's HTML. Fetchers never interpret what they
    fetch — classification is the pool's job, so every fetcher stays swappable."""

    name = "base"

    def fetch(self, url: str) -> str:
        raise NotImplementedError

    def close(self) -> None:
        return None
