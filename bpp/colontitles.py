# -*- coding: utf-8 -*-
"""Колонтитулы: повторяющийся текст в верхнем/нижнем поле — удалять, не heading."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .bbox import is_valid_bbox

MARGIN_FRAC = 0.10


def normalize_colontitle(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t.upper()


def is_in_margin(bbox: Sequence[float], page_height: float, frac: float = MARGIN_FRAC) -> bool:
    if not is_valid_bbox(bbox):
        return False
    y0, y1 = bbox[1], bbox[3]
    margin = page_height * frac
    return y1 < margin or y0 > page_height - margin


def is_page_number_block(
    text: str,
    bbox: Sequence[float],
    page_height: float,
    *,
    line_count: int = 1,
    chars: int | None = None,
) -> bool:
    t = (text or "").strip()
    clen = chars if chars is not None else len(t)
    if not t or line_count > 1 or clen > 20:
        return False
    if not is_in_margin(bbox, page_height, frac=0.08):
        return False
    cleaned = re.sub(r"^[\s\-—–.*·•]+|[\s\-—–.*·•]+$", "", t)
    if not cleaned:
        return False
    if re.fullmatch(r"\d{1,4}", cleaned):
        return True
    if re.fullmatch(r"[IVXLCDMivxlcdm]+", cleaned):
        roman = r"^[Mm]{0,3}([Cc][Mm]|[Cc][Dd]|[Dd]?[Cc]{0,3})([Xx][Cc]|[Xx][Ll]|[Ll]?[Xx]{0,3})([Ii][Xx]|[Ii][Vv]|[Vv]?[Ii]{0,3})$"
        return bool(re.fullmatch(roman, cleaned, re.IGNORECASE))
    return False


class ColontitleRegistry:
    """Cross-page детекция колонтитулов."""

    def __init__(
        self,
        known: Optional[Set[str]] = None,
        book_title: str = "",
        book_author: str = "",
    ) -> None:
        self.known: Set[str] = {normalize_colontitle(x) for x in (known or set()) if x}
        for meta in (book_title, book_author):
            n = normalize_colontitle(meta)
            if n:
                self.known.add(n)
        self._counts: Counter[str] = Counter()
        self._pages_seen = 0

    @classmethod
    def from_book(cls, book: Optional[Dict[str, Any]]) -> ColontitleRegistry:
        if not book:
            return cls()
        detected = book.get("colontitles_detected") or []
        return cls(
            known=set(detected),
            book_title=str(book.get("title") or ""),
            book_author=str(book.get("author") or ""),
        )

    def observe_margin_text(self, text: str) -> None:
        n = normalize_colontitle(text)
        if not n or len(n) < 3:
            return
        self._counts[n] += 1

    def observe_page(self) -> None:
        self._pages_seen += 1

    def finalize(self, *, min_count: int = 3, min_frac: float = 0.04) -> Set[str]:
        threshold = max(min_count, int(self._pages_seen * min_frac))
        for text, cnt in self._counts.items():
            if cnt >= threshold:
                self.known.add(text)
        return set(self.known)

    def is_colontitle(self, text: str, bbox: Sequence[float], page_height: float) -> bool:
        if not is_in_margin(bbox, page_height):
            return False
        return self._text_is_colontitle(text)

    def _text_is_colontitle(self, text: str) -> bool:
        n = normalize_colontitle(text)
        if not n:
            return False
        if n in self.known:
            return True
        if self._counts.get(n, 0) >= 3:
            return True
        if len(n) <= 40:
            for k in self.known:
                if k in n or n in k:
                    return True
        return False

    def strip_line_prefix(self, text: str) -> str:
        """Убрать обрывок колонтитула в начале строки основного текста."""
        t = (text or "").strip()
        if not t:
            return t
        n = normalize_colontitle(t)
        for k in sorted(self.known, key=len, reverse=True):
            if n.startswith(k):
                # снять префикс той же длины из исходной строки (грубо — по словам)
                rest = t[len(k) :].strip(" .-—–")
                if rest:
                    return rest
        m = re.search(r"[а-яё]", t, re.IGNORECASE)
        if m and 0 < m.start() <= 28:
            prefix = t[: m.start()]
            if re.fullmatch(r"[\sA-ZА-ЯЁ\.IVXLCDMivxlcdm0-9\-—–]+", prefix):
                return t[m.start() :].strip()
        return t

    def to_list(self) -> List[str]:
        return sorted(self.known)

    def save_to_book(self, out_dir: Path) -> None:
        from .workdir import load_book, save_book

        book = load_book(out_dir) or {}
        book["colontitles_detected"] = self.to_list()
        save_book(out_dir, book)


def scan_pdf_colontitles(doc: Any, page_indices: range | List[int], reg: ColontitleRegistry) -> Set[str]:
    """Быстрый проход PDF: собрать повторяющиеся строки в margin."""
    import re

    _HYPH = re.compile(r"(\w)[\-\u2010\u2013\u2014]\n(?=\w)")
    for page_idx in page_indices:
        page = doc[page_idx]
        ph = page.rect.height
        d = page.get_text("dict")
        for block in d.get("blocks") or []:
            if block.get("type", 0) != 0:
                continue
            for ln in block.get("lines") or []:
                parts = [sp.get("text", "") for sp in ln.get("spans", [])]
                text = "".join(parts)
                text = _HYPH.sub(r"\1", text)
                text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
                text = re.sub(r"[ \t]{2,}", " ", text).strip()
                if not text:
                    continue
                bbox = list(ln.get("bbox") or block.get("bbox", [0, 0, 0, 0]))
                if is_in_margin(bbox, ph):
                    reg.observe_margin_text(text)
        reg.observe_page()
    return reg.finalize()
