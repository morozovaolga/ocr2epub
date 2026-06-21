# -*- coding: utf-8 -*-
from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import fitz

from .colontitles import ColontitleRegistry, scan_pdf_colontitles
from .extract_pymupdf import extract_page, open_pdf
from .mech import process_page_data
from .page_store import (
    init_manifest,
    load_page,
    mark_page_done,
    page_path,
    save_page,
)
from .workdir import load_book, update_stage, consolidate_workdir

MIN_CHARS_PER_PAGE = 50


def _load_extract_flags(out_dir: Path, overrides: Dict[str, Any]) -> Dict[str, Any]:
    book = load_book(out_dir) or {}
    flags = dict(book.get("flags") or {})
    flags.update({k: v for k, v in overrides.items() if v is not None})
    return {
        "two_columns": bool(flags.get("two_columns")),
        "poetry": bool(flags.get("poetry")),
        "engine": str(flags.get("engine") or "pymupdf"),
    }


def run_pages(
    pdf_path: Path,
    out_dir: Path,
    *,
    page_from: int = 1,
    page_to: Optional[int] = None,
    resume: bool = True,
    force: bool = False,
    apply_rules: bool = True,
    rules_dir: Optional[Path] = None,
    modernize: bool = True,
    use_oldspelling: bool = True,
    two_columns: Optional[bool] = None,
    poetry: Optional[bool] = None,
    engine: Optional[str] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    consolidate_workdir(out_dir)
    page_dir = out_dir / "pages"
    page_dir.mkdir(exist_ok=True)

    xf = _load_extract_flags(
        out_dir,
        {"two_columns": two_columns, "poetry": poetry, "engine": engine},
    )

    doc = open_pdf(str(pdf_path))
    try:
        total = len(doc)
        last = page_to if page_to is not None else total
        last = min(last, total)
        init_manifest(out_dir, pdf=pdf_path, total_pages=total)

        book = load_book(out_dir)
        colontitles = ColontitleRegistry.from_book(book)
        scan_pdf_colontitles(doc, range(total), colontitles)
        colontitles.save_to_book(out_dir)

        fn_counter: Iterator[int] = itertools.count(1)

        done = 0
        skipped = 0
        for page in range(max(1, page_from), last + 1):
            if resume and not force and page_path(page_dir, page).is_file():
                existing = load_page(page_dir, page)
                if existing and existing.get("status") == "done":
                    skipped += 1
                    mark_page_done(out_dir, page)
                    continue

            raw = extract_page(
                doc,
                page - 1,
                two_columns=xf["two_columns"],
                poetry=xf["poetry"],
                fn_counter=fn_counter,
                colontitles=colontitles,
            )
            if xf["engine"] == "auto":
                chars = sum(len(b.get("text") or "") for b in raw.get("blocks") or [])
                if chars < MIN_CHARS_PER_PAGE:
                    raw["engine"] = "pymupdf_sparse"
                    raw.setdefault("warnings", []).append(
                        f"мало текста ({chars} симв.) — нужен tesseract fallback (пока не подключён)"
                    )

            data = process_page_data(
                raw,
                apply_rules=apply_rules,
                rules_dir=rules_dir,
                modernize=modernize,
                use_oldspelling=use_oldspelling,
            )
            save_page(page_dir, data)
            mark_page_done(out_dir, page)
            done += 1
    finally:
        doc.close()

    if done > 0:
        update_stage(out_dir, "ocr", "partial" if skipped else "done")
    elif skipped > 0:
        update_stage(out_dir, "ocr", "done")

    return {
        "out_dir": str(out_dir.resolve()),
        "pages_done": done,
        "pages_skipped": skipped,
        "page_from": page_from,
        "page_to": last,
        "two_columns": xf["two_columns"],
    }
