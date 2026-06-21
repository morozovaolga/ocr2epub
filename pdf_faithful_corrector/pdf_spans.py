# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

import fitz

_WS = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    s = s.replace("\u00AD", "").replace("\u200B", "")
    s = _WS.sub(" ", s.strip())
    return s


def normalize_for_match(s: str) -> str:
    s = normalize_text(s)
    # Latin OCR confusions → Cyrillic (subset)
    trans = str.maketrans({
        "A": "А", "a": "а", "B": "В", "C": "С", "c": "с",
        "E": "Е", "e": "е", "H": "Н", "K": "К", "k": "к",
        "M": "М", "O": "О", "o": "о", "P": "Р", "p": "р",
        "T": "Т", "X": "Х", "x": "х", "Y": "У", "y": "у",
    })
    s = s.translate(trans)
    s = s.replace("Ё", "Е").replace("ё", "е")
    return s.lower()


@lru_cache(maxsize=4)
def _open_doc(pdf_path: str) -> fitz.Document:
    return fitz.open(pdf_path)


def _is_valid_bbox(bbox: List[float]) -> bool:
    if not bbox or len(bbox) < 4:
        return False
    x0, y0, x1, y1 = bbox[:4]
    if x1 <= x0 or y1 <= y0:
        return False
    if x0 == y0 == x1 == y1 == 0:
        return False
    return True


def extract_page_text(pdf_path: Path, page_1based: int) -> str:
    doc = _open_doc(str(pdf_path.resolve()))
    idx = max(0, min(page_1based - 1, len(doc) - 1))
    return doc[idx].get_text("text") or ""


def extract_bbox_text(
    pdf_path: Path,
    page_1based: int,
    bbox: List[float],
    *,
    pad: float = 2.0,
) -> str:
    """Return PDF text-layer content clipped to block bbox (PDF points)."""
    if not _is_valid_bbox(bbox):
        return extract_page_text(pdf_path, page_1based)

    doc = _open_doc(str(pdf_path.resolve()))
    idx = max(0, min(page_1based - 1, len(doc) - 1))
    page = doc[idx]
    x0, y0, x1, y1 = bbox[:4]
    rect = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
    if rect.is_empty:
        return extract_page_text(pdf_path, page_1based)
    return page.get_text("text", clip=rect) or ""


def merge_pdf_spans(
    pdf_path: Path,
    spans: List[Tuple[int, List[float]]],
    *,
    pad: float = 2.0,
) -> str:
    parts: List[str] = []
    full_pages_done: set[int] = set()
    for page, bbox in spans:
        if _is_valid_bbox(bbox):
            t = extract_bbox_text(pdf_path, page, bbox, pad=pad)
        else:
            if page in full_pages_done:
                continue
            t = extract_page_text(pdf_path, page)
            full_pages_done.add(page)
        t = normalize_text(t)
        if t:
            parts.append(t)
    return normalize_text(" ".join(parts))
