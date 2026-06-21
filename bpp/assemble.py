# -*- coding: utf-8 -*-
"""Сборка book_structured.json + final_better.txt для pdf_faithful_corrector."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .page_store import load_manifest, load_page
from .workdir import load_book, resolve_pdf, save_book, update_stage, consolidate_workdir, SOURCE_PDF


def _export_corrected(blocks: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for b in blocks:
        text = (b.get("text_corrected") or b.get("text_modern") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts) + ("\n" if parts else "")


def _export_paragraphs(blocks: List[Dict[str, Any]], *, field: str = "text_modern") -> str:
    """Один блок = один абзац в export; heading отдельно, {{fn:N}} сохраняются."""
    parts: List[str] = []
    for b in blocks:
        text = (b.get(field) or "").strip()
        if not text:
            continue
        parts.append(text)
    return "\n\n".join(parts) + ("\n" if parts else "")


def _build_corrector_queue(out_dir: Path, pdf: Path) -> str | None:
    try:
        from pdf_faithful_corrector.queue import run_from_workdir
    except ImportError:
        return None
    out_path = out_dir / "corrector_queue.json"
    run_from_workdir(pdf, out_dir, out_path)
    return str(out_path.resolve())


def assemble(out_dir: Path, *, build_queue: bool = True) -> Dict[str, Any]:
    consolidate_workdir(out_dir)
    manifest = load_manifest(out_dir)
    page_dir = out_dir / (manifest.get("page_dir") or "pages")
    if not page_dir.is_dir():
        raise FileNotFoundError(f"Нет папки страниц: {page_dir}")

    completed = sorted(int(p) for p in (manifest.get("completed_pages") or []))
    if not completed:
        completed = sorted(
            int(p.stem.split("_")[1])
            for p in page_dir.glob("page_*.json")
        )

    all_blocks: List[Dict[str, Any]] = []
    all_footnotes: List[Dict[str, Any]] = []

    for page in completed:
        data = load_page(page_dir, page)
        if not data:
            continue
        for fn in data.get("footnotes") or []:
            all_footnotes.append({**fn, "page": page})
        for b in data.get("blocks") or []:
            all_blocks.append({
                "id": b.get("id"),
                "page": page,
                "role": b.get("role") or "paragraph",
                "text_raw": b.get("text_raw") or b.get("text") or "",
                "text": b.get("text") or b.get("text_raw") or "",
                "text_modern": b.get("text_modern") or b.get("text") or "",
                "text_corrected": b.get("text_corrected") or "",
                "status": b.get("status") or "",
                "confidence": b.get("confidence") or "",
                "bbox": b.get("bbox") or [0, 0, 0, 0],
                "wsize": b.get("wsize") or 0,
                "line_count": b.get("line_count") or 1,
            })

    pdf = SOURCE_PDF
    try:
        resolve_pdf(out_dir)
    except FileNotFoundError:
        pdf = manifest.get("pdf") or SOURCE_PDF

    book_meta = load_book(out_dir)
    if book_meta:
        book_meta.setdefault("stages", {})["assemble"] = "done"
        save_book(out_dir, book_meta)

    book = {
        "version": 2,
        "pipeline": "book_page_pipeline",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pdf": pdf,
        "pages_total": len(completed),
        "blocks_total": len(all_blocks),
        "footnotes_total": len(all_footnotes),
        "blocks": all_blocks,
        "footnotes": all_footnotes,
    }

    structured = {
        "version": 2,
        "source": "book_page_pipeline",
        "pdf": pdf,
        "blocks": [
            {
                "page": b["page"],
                "role": b["role"],
                "text": b.get("text_corrected") or b["text_modern"],
                "bbox": b["bbox"],
                "wsize": b["wsize"],
                "id": b.get("id"),
            }
            for b in all_blocks
        ],
    }

    final_better = _export_paragraphs(all_blocks, field="text_modern")
    final_rules = _export_paragraphs(all_blocks, field="text")
    final_corrected = _export_corrected(all_blocks)

    book_path = out_dir / "book_structured.json"
    structured_path = out_dir / "structured_rules.json"
    text_path = out_dir / "final_better.txt"
    rules_text_path = out_dir / "final_after_rules.txt"
    corrected_path = out_dir / "final_corrected.txt"

    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    structured_path.write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8")
    text_path.write_text(final_better, encoding="utf-8")
    rules_text_path.write_text(final_rules, encoding="utf-8")
    corrected_path.write_text(final_corrected, encoding="utf-8")

    with_bbox = sum(1 for b in all_blocks if b.get("bbox") and b["bbox"] != [0, 0, 0, 0])

    corrector_queue: str | None = None
    if build_queue and pdf:
        try:
            corrector_queue = _build_corrector_queue(out_dir, resolve_pdf(out_dir))
            if corrector_queue:
                update_stage(out_dir, "review", "pending")
        except Exception:
            corrector_queue = None

    return {
        "book_structured": str(book_path),
        "structured_rules": str(structured_path),
        "final_better": str(text_path),
        "final_after_rules": str(rules_text_path),
        "final_corrected": str(corrected_path),
        "corrector_queue": corrector_queue,
        "pages": len(completed),
        "blocks": len(all_blocks),
        "blocks_with_bbox": with_bbox,
    }
