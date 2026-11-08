"""Download a filing PDF and return per-page text (cached forever)."""
from __future__ import annotations

import re

import pymupdf

from finn.net import get_bytes, memo_json


def pages(url: str, max_pages: int | None = None) -> list[str]:
    def run():
        data = get_bytes(url, ttl=None)
        doc = pymupdf.open(stream=data, filetype="pdf")
        out = []
        for i, p in enumerate(doc):
            if max_pages and i >= max_pages:
                break
            t = p.get_text()
            t = re.sub(r"[ \t]+", " ", t)
            t = re.sub(r"\n\s*\n+", "\n", t)
            out.append(t.strip())
        return out
    return memo_json("pdftext", f"{url}|{max_pages}", run)


def with_markers(pgs: list[str], start: int = 0) -> str:
    """Join pages with [p N] markers so the model can cite page numbers."""
    return "\n".join(f"[p{start + i + 1}]\n{t}" for i, t in enumerate(pgs) if t)
