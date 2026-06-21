# -*- coding: utf-8 -*-
"""Детекция сносок (порт логики ocr2epub)."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Tuple

_HYPH_FIX = re.compile(r"(\w)[\-‑–—]\n(?=\w)")


def is_footnote_body_block(raw_block: dict, page_height: float, med_size: float) -> bool:
    bbox = raw_block.get("bbox", [0, 0, 0, 0])
    if bbox[1] < page_height * 0.65:
        return False
    lines = raw_block.get("lines", [])
    total_chars = 0
    wsum = 0.0
    for ln in lines:
        for sp in ln.get("spans", []):
            s = sp.get("size", 0)
            t = sp.get("text", "") or ""
            n = len(t)
            if n and s:
                wsum += s * n
                total_chars += n
    if total_chars == 0:
        return False
    if (wsum / total_chars) >= med_size * 0.92:
        return False
    full = "".join(
        sp.get("text", "") for ln in lines for sp in ln.get("spans", [])
    ).strip()
    return bool(full and re.match(r"^\d", full))


def parse_footnote_block(raw_block: dict) -> List[Tuple[str, str]]:
    full_text = ""
    for ln in raw_block.get("lines", []):
        full_text += "".join(sp.get("text", "") for sp in ln.get("spans", [])) + "\n"
    footnotes: List[Tuple[str, str]] = []
    cur_marker = None
    cur_lines: List[str] = []
    for line in full_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s*[.)]*\s+(.*)", line)
        if m:
            if cur_marker is not None:
                footnotes.append((cur_marker, " ".join(cur_lines).strip()))
            cur_marker = m.group(1)
            cur_lines = [m.group(2)] if m.group(2) else []
        elif cur_marker is not None:
            cur_lines.append(line)
    if cur_marker is not None:
        footnotes.append((cur_marker, " ".join(cur_lines).strip()))
    return footnotes


def block_median_size(block: dict) -> float:
    sizes: List[float] = []
    for ln in block.get("lines", []):
        for sp in ln.get("spans", []):
            s = sp.get("size", 0)
            n = len(sp.get("text", ""))
            if s and n:
                sizes.extend([s] * n)
    if not sizes:
        return 0.0
    sizes.sort()
    return sizes[len(sizes) // 2]


def is_superscript_digit(span: dict, block_med_size: float = 0) -> bool:
    text = span.get("text", "").strip()
    if not text or not re.fullmatch(r"\d{1,3}", text):
        return False
    if span.get("flags", 0) & 1:
        return True
    sz = span.get("size", 0)
    if block_med_size > 0 and sz > 0 and sz < block_med_size * 0.85:
        return True
    return False


def collect_block_text(
    block: dict,
    fn_id_map: Dict[str, int] | None = None,
    *,
    poetry: bool = False,
) -> str:
    lines = block.get("lines") or []
    block_med = block_median_size(block)
    line_texts: List[str] = []
    for ln in lines:
        parts: List[str] = []
        for sp in ln.get("spans", []):
            text = sp.get("text", "")
            marker = text.strip()
            if fn_id_map and is_superscript_digit(sp, block_med) and marker in fn_id_map:
                parts.append(f"{{{{fn:{fn_id_map[marker]}}}}}")
            else:
                parts.append(text)
        line_texts.append("".join(parts))

    text = "\n".join(line_texts)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if poetry:
        out: List[str] = []
        for ln in line_texts:
            ln = re.sub(r"[ \t]{2,}", " ", ln).strip()
            if ln:
                out.append(ln)
        return "\n".join(out)
    text = _HYPH_FIX.sub(r"\1", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_page_footnotes(
    block_infos: List[dict],
    page_height: float,
    med_size: float,
    fn_counter: Iterator[int],
) -> Tuple[Dict[str, int], List[Dict[str, Any]], set[int]]:
    fn_body_indices: set[int] = set()
    per_page_fn: Dict[str, str] = {}
    fn_bbox: List[float] = [0, 0, 0, 0]
    fn_id_map: Dict[str, int] = {}
    page_footnotes: List[Dict[str, Any]] = []

    for idx, bi in enumerate(block_infos):
        if is_footnote_body_block(bi["raw"], page_height, med_size):
            fn_bbox = list(bi["raw"].get("bbox", [0, 0, 0, 0]))
            for marker, text in parse_footnote_block(bi["raw"]):
                per_page_fn[marker] = text
            fn_body_indices.add(idx)

    for marker in sorted(per_page_fn, key=lambda m: int(m) if m.isdigit() else 0):
        gid = next(fn_counter)
        fn_id_map[marker] = gid
        page_footnotes.append({
            "id": gid,
            "marker": str(gid),
            "text": per_page_fn[marker],
            "bbox": fn_bbox,
        })

    return fn_id_map, page_footnotes, fn_body_indices
