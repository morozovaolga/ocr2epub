# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .bbox import is_valid_bbox, union_bbox

_END_SENTENCE_RE = re.compile(r"[.!?…][\"'\»\)]*\s*$")
_JUNK_RE = re.compile(r"^[\s\-–—&*•·#§†‡©®™°¬]+$")


def is_junk(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 2:
        return True
    if _JUNK_RE.fullmatch(t) and len(t) < 8:
        return True
    return False


def join_lines(prev: str, nxt: str) -> str:
    if not prev:
        return nxt
    if not nxt:
        return prev
    if re.search(r"[-‑–—]\s*$", prev) and re.match(r"^[A-Za-zА-Яа-яЁё]", nxt):
        return re.sub(r"[-‑–—]\s*$", "", prev) + nxt
    sep = "" if prev.endswith(" ") or nxt.startswith((" ", ",", ".", ";", ":", "!", "?", "…", "»", ")", "]")) else " "
    return prev + sep + nxt


def merge_lines_to_paragraphs(
    lines: List[Dict[str, Any]],
    *,
    page: int,
    page_height: float,
    line_gap_pt: float = 14.0,
    poetry: bool = False,
) -> List[Dict[str, Any]]:
    """Склеить строки в абзацы, сохраняя union bbox и wsize."""
    if poetry:
        merged: List[Dict[str, Any]] = []
        for ln in lines:
            text = (ln.get("text") or "").strip()
            if not text:
                continue
            merged.append({
                "role": "verse",
                "text": text,
                "page": page,
                "bbox": ln.get("bbox") or [0, 0, 0, 0],
                "wsize": ln.get("wsize") or 0,
                "line_count": 1,
                "chars": len(text),
            })
        return merged

    merged: List[Dict[str, Any]] = []
    buf_text: Optional[str] = None
    buf_bbox: Optional[List[float]] = None
    buf_wsize = 0.0
    buf_lines = 0
    prev_bbox: Optional[List[float]] = None

    for ln in lines:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        bbox = list(ln.get("bbox") or [0, 0, 0, 0])
        gap = (bbox[1] - prev_bbox[3]) if prev_bbox and is_valid_bbox(bbox) else 999.0
        new_para = (
            buf_text is None
            or gap > line_gap_pt * 1.8
            or (_END_SENTENCE_RE.search(buf_text or "") and gap > line_gap_pt * 0.6)
        )
        if new_para:
            if buf_text:
                merged.append({
                    "role": "paragraph",
                    "text": buf_text,
                    "page": page,
                    "bbox": buf_bbox or [0, 0, 0, 0],
                    "wsize": round(buf_wsize, 2) if buf_wsize else 0,
                    "line_count": buf_lines,
                    "chars": len(buf_text),
                })
            buf_text = text
            buf_bbox = bbox if is_valid_bbox(bbox) else None
            buf_wsize = float(ln.get("wsize") or 0)
            buf_lines = 1
        else:
            buf_text = join_lines(buf_text or "", text)
            if is_valid_bbox(bbox):
                buf_bbox = union_bbox(buf_bbox or bbox, bbox) if buf_bbox else bbox
            buf_wsize = max(buf_wsize, float(ln.get("wsize") or 0))
            buf_lines += 1
        prev_bbox = bbox if is_valid_bbox(bbox) else prev_bbox

    if buf_text:
        merged.append({
            "role": "paragraph",
            "text": buf_text,
            "page": page,
            "bbox": buf_bbox or [0, 0, 0, 0],
            "wsize": round(buf_wsize, 2) if buf_wsize else 0,
            "line_count": buf_lines,
            "chars": len(buf_text),
        })
    return merged
