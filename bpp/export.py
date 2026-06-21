# -*- coding: utf-8 -*-
"""Export: финальный TXT и footnotes.json из book_structured.json."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .assemble import assemble
from .workdir import consolidate_workdir, load_book, save_book, update_stage


def _pick_block_text(b: Dict[str, Any]) -> str:
    for key in ("text_corrected", "text_modern", "text"):
        t = (b.get(key) or "").strip()
        if t:
            return t
    return ""


def _pick_footnote_text(fn: Dict[str, Any]) -> str:
    for key in ("text_corrected", "text_modern", "text"):
        t = (fn.get(key) or "").strip()
        if t:
            return t
    return ""


def load_export_source(out_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Блоки и сноски из book_structured.json."""
    book_path = out_dir / "book_structured.json"
    if not book_path.is_file():
        raise FileNotFoundError(
            f"Нет {book_path}. Сначала: bpp assemble --out {out_dir}"
        )
    data = json.loads(book_path.read_text(encoding="utf-8"))

    blocks: List[Dict[str, Any]] = []
    for b in data.get("blocks") or []:
        text = _pick_block_text(b)
        if not text:
            continue
        blocks.append({
            "id": b.get("id"),
            "page": b.get("page"),
            "role": (b.get("role") or "paragraph").lower(),
            "text": text,
        })

    footnotes: List[Dict[str, Any]] = []
    for fn in data.get("footnotes") or []:
        text = _pick_footnote_text(fn)
        if not text:
            continue
        fid = fn.get("id")
        if fid is None:
            continue
        footnotes.append({
            "id": int(fid),
            "marker": str(fn.get("marker") or fid),
            "text": text,
            "page": fn.get("page"),
        })

    if not blocks:
        raise ValueError(f"В {book_path} нет блоков с текстом для export")
    return blocks, footnotes


def export_workdir(
    out_dir: Path,
    *,
    refresh: bool = True,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Dict[str, Any]:
    if refresh:
        consolidate_workdir(out_dir)
        assemble(out_dir, build_queue=False)

    book = load_book(out_dir) or {}
    book_title = title or book.get("title") or out_dir.name
    book_author = author if author is not None else (book.get("author") or "")

    blocks, footnotes = load_export_source(out_dir)

    corrected = out_dir / "final_corrected.txt"
    export_txt = out_dir / "book_export.txt"
    paths: Dict[str, Any] = {
        "blocks": len(blocks),
        "footnotes": len(footnotes),
        "title": book_title,
    }

    src = corrected if corrected.is_file() else out_dir / "final_better.txt"
    if src.is_file():
        export_txt.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        body = "\n\n".join(b["text"] for b in blocks) + "\n"
        export_txt.write_text(body, encoding="utf-8")
    paths["book_export"] = str(export_txt.resolve())

    if footnotes:
        fn_path = out_dir / "footnotes.json"
        fn_path.write_text(
            json.dumps(footnotes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths["footnotes_json"] = str(fn_path.resolve())

    book.setdefault("stages", {})["export"] = "done"
    book["export_at"] = datetime.now(timezone.utc).isoformat()
    if book_author:
        book["author"] = book_author
    if title:
        book["title"] = book_title
    save_book(out_dir, book)
    update_stage(out_dir, "export", "done")
    paths["out_dir"] = str(out_dir.resolve())
    return paths
