# -*- coding: utf-8 -*-
"""Снимки фрагментов PDF по страницам (для corrector_pages/)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz

from .pdf_spans import _is_valid_bbox


def render_page_clip(
    pdf_path: Path,
    *,
    page_no: int,
    pages: List[int],
    bboxes: List[List[float]],
    zoom: float = 2.2,
    pad: float = 4.0,
) -> bytes:
    doc = fitz.open(str(pdf_path.resolve()))
    try:
        page = doc[max(0, min(page_no - 1, len(doc) - 1))]
        union: Optional[fitz.Rect] = None
        for pg, bbox in zip(pages, bboxes):
            if int(pg) != int(page_no) or not _is_valid_bbox(bbox):
                continue
            x0, y0, x1, y1 = bbox[:4]
            rect = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
            if rect.is_empty:
                continue
            union = rect if union is None else union | rect
        if union is None or union.is_empty:
            union = page.rect
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=union, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def export_page_snapshots(
    pdf_path: Path,
    items: List[Dict[str, Any]],
    out_dir: Path,
    *,
    zoom: float = 2.2,
) -> Path:
    """Сохранить PNG для каждой страницы в очереди: corrector_pages/p0001_page_0007.png."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: List[Dict[str, Any]] = []
    for item in items:
        pi = int(item["paragraph_index"])
        page = int(item.get("page") or (item.get("pages") or [1])[0])
        slice_key = item.get("slice_key") or f"{pi}:{page}"
        block_id = item.get("block_id")
        if block_id:
            fname = f"{block_id}.png"
        else:
            fname = f"p{pi:04d}_page_{page:04d}.png"
        fpath = out_dir / fname
        png = render_page_clip(
            pdf_path,
            page_no=page,
            pages=list(item.get("pages") or [page]),
            bboxes=list(item.get("bboxes") or []),
            zoom=zoom,
        )
        fpath.write_bytes(png)
        manifest.append({
            "slice_key": slice_key,
            "paragraph_index": pi,
            "page": page,
            "image": fname,
        })
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path
