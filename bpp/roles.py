# -*- coding: utf-8 -*-
"""Роли блоков: heading только в теле страницы, не колонтитул."""
from __future__ import annotations

from typing import Any, Dict

from .colontitles import is_in_margin


def assign_heading_role(
    block: Dict[str, Any],
    *,
    page_height: float,
    page_width: float,
    med_wsize: float,
    heading_thr: float,
) -> str:
    if block.get("role") == "verse":
        return "verse"

    bbox = block.get("bbox") or [0, 0, 0, 0]
    if is_in_margin(bbox, page_height):
        return "paragraph"

    wsize = float(block.get("wsize") or 0)
    line_count = int(block.get("line_count") or 1)
    chars = int(block.get("chars") or len(block.get("text") or ""))
    x0, _, x1, _ = bbox
    cx = (x0 + x1) / 2
    centered = abs(cx - page_width / 2) < page_width * 0.12
    wide = (x1 - x0) > page_width * 0.45
    short = line_count <= 3 and chars <= 200
    big_font = wsize >= heading_thr

    if big_font and short:
        return "heading"
    if centered and short and not wide and wsize >= med_wsize * 1.2:
        return "heading"
    if chars <= 50 and line_count == 1 and wsize >= med_wsize * 1.3:
        return "heading"
    return block.get("role") or "paragraph"
