# -*- coding: utf-8 -*-
"""Разрешение путей в единой рабочей папке out/<slug>/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

BOOK_JSON = "book.json"
SOURCE_PDF = "source.pdf"


def load_book(workdir: Path) -> Optional[Dict[str, Any]]:
    p = workdir / BOOK_JSON
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_pdf(workdir: Path) -> Path:
    """PDF из book.json, source.pdf или manifest.json."""
    book = load_book(workdir)
    if book and book.get("pdf_path"):
        p = Path(book["pdf_path"])
        if not p.is_absolute():
            p = workdir / p
        if p.is_file():
            return p.resolve()

    local = workdir / SOURCE_PDF
    if local.is_file():
        return local.resolve()

    manifest = workdir / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("pdf"):
            p = Path(str(data["pdf"]))
            if p.is_file():
                return p.resolve()

    raise FileNotFoundError(
        f"PDF не найден в {workdir}. Нужен book.json + source.pdf или manifest.json"
    )
