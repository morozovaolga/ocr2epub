# -*- coding: utf-8 -*-
"""Механическая обработка: rules + modernize на готовых pages/*.json."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .modernize import modernize_block
from .page_store import load_manifest, load_page, page_path, save_page
from .rules import apply_rules_to_blocks
from .workdir import update_stage


def process_page_data(
    data: Dict[str, Any],
    *,
    apply_rules: bool = True,
    rules_dir: Optional[Path] = None,
    modernize: bool = True,
    use_oldspelling: bool = True,
) -> Dict[str, Any]:
    blocks = list(data.get("blocks") or [])
    for b in blocks:
        b.setdefault("text_raw", b.get("text") or "")

    if apply_rules:
        blocks = apply_rules_to_blocks(
            blocks,
            rules_dir=rules_dir,
            use_oldspelling=use_oldspelling,
        )
    else:
        for b in blocks:
            b["text"] = b.get("text_raw") or ""

    if modernize:
        for b in blocks:
            b["text_modern"] = modernize_block(
                b.get("text") or "",
                role=b.get("role") or "paragraph",
            )
        for fn in data.get("footnotes") or []:
            fn["text_modern"] = modernize_block(fn.get("text") or "")
    else:
        for b in blocks:
            b["text_modern"] = (b.get("text") or "").strip()
        for fn in data.get("footnotes") or []:
            fn["text_modern"] = (fn.get("text") or "").strip()

    data["blocks"] = blocks
    data["status"] = "done"
    data["processed_at"] = datetime.now(timezone.utc).isoformat()
    return data


def apply_to_workdir(
    out_dir: Path,
    *,
    page_from: int = 1,
    page_to: Optional[int] = None,
    rules_dir: Optional[Path] = None,
    modernize: bool = True,
    use_oldspelling: bool = True,
) -> Dict[str, Any]:
    """Переприменить rules + modernize без повторного OCR."""
    manifest = load_manifest(out_dir)
    page_dir = out_dir / (manifest.get("page_dir") or "pages")
    completed = sorted(int(p) for p in (manifest.get("completed_pages") or []))
    if not completed:
        completed = sorted(
            int(p.stem.split("_")[1]) for p in page_dir.glob("page_*.json")
        )
    if page_to:
        completed = [p for p in completed if page_from <= p <= page_to]
    else:
        completed = [p for p in completed if p >= page_from]

    done = 0
    for page in completed:
        data = load_page(page_dir, page)
        if not data:
            continue
        data = process_page_data(
            data,
            apply_rules=True,
            rules_dir=rules_dir,
            modernize=modernize,
            use_oldspelling=use_oldspelling,
        )
        save_page(page_dir, data)
        done += 1

    update_stage(out_dir, "assemble", "pending")
    return {"pages_updated": done, "out_dir": str(out_dir.resolve())}
