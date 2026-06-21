# -*- coding: utf-8 -*-
"""Единая рабочая папка книги: out/<slug>/ + book.json."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BOOK_JSON = "book.json"
SOURCE_PDF = "source.pdf"
PIPELINE_VERSION = 3

SPELLING_PRE_REFORM = "pre_reform"
SPELLING_MODERN = "modern"
TARGET_ORTHOGRAPHY_MODERN = "modern"
TARGET_ORTHOGRAPHY_FAITHFUL = "faithful"


def _inside_workdir(path: Path, out_dir: Path) -> bool:
    try:
        path.resolve().relative_to(out_dir.resolve())
        return True
    except ValueError:
        return False


def manifest_pdf_ref(out_dir: Path, pdf: Path) -> str:
    """Путь PDF для manifest/book_structured — относительный внутри workdir."""
    out_dir = out_dir.resolve()
    pdf = pdf.resolve()
    local = out_dir / SOURCE_PDF
    if pdf == local or _inside_workdir(pdf, out_dir):
        return SOURCE_PDF
    return str(pdf)


def consolidate_workdir(out_dir: Path, *, copy_pdf: bool = True) -> Dict[str, Any]:
    """PDF и ссылки book.json/manifest — только внутри out/<slug>/ (source.pdf)."""
    from .page_store import load_manifest, save_manifest

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / SOURCE_PDF
    book = load_book(out_dir) or default_book(out_dir.name)

    current: Optional[Path] = None
    if local.is_file():
        current = local
    else:
        try:
            current = resolve_pdf(out_dir)
        except FileNotFoundError:
            current = None

    copied = False
    if current and not _inside_workdir(current, out_dir):
        if not copy_pdf:
            return {
                "ok": False,
                "workdir": str(out_dir),
                "pdf": str(current),
                "reason": "PDF вне workdir; запустите с copy_pdf или bpp consolidate",
            }
        shutil.copy2(current, local)
        book.setdefault("source_pdf_original", str(current.resolve()))
        copied = True
        current = local
    elif current and current.resolve() != local.resolve() and _inside_workdir(current, out_dir):
        if copy_pdf and not local.is_file():
            shutil.copy2(current, local)
            copied = True
            current = local

    if local.is_file():
        book["pdf_path"] = SOURCE_PDF
        book.pop("pdf_path_resolved", None)
        save_book(out_dir, book)

        manifest = load_manifest(out_dir)
        if manifest:
            manifest["pdf"] = SOURCE_PDF
            save_manifest(out_dir, manifest)

        return {
            "ok": True,
            "workdir": str(out_dir),
            "pdf": str(local),
            "copied": copied,
        }

    return {
        "ok": False,
        "workdir": str(out_dir),
        "reason": "source.pdf не найден; bpp init --pdf … --out …",
    }


def book_json_path(out_dir: Path) -> Path:
    return out_dir / BOOK_JSON


def load_book(out_dir: Path) -> Optional[Dict[str, Any]]:
    p = book_json_path(out_dir)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_book(out_dir: Path, book: Dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    book["updated_at"] = datetime.now(timezone.utc).isoformat()
    p = book_json_path(out_dir)
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def default_book(title: str, pdf_path: str = SOURCE_PDF) -> Dict[str, Any]:
    return {
        "title": title,
        "pdf_path": pdf_path,
        "pipeline_version": PIPELINE_VERSION,
        "flags": {
            "two_columns": False,
            "engine": "pymupdf",
            "poetry": False,
            "spelling": SPELLING_PRE_REFORM,
            "target_orthography": TARGET_ORTHOGRAPHY_MODERN,
        },
        "stages": {
            "init": "done",
            "ocr": "pending",
            "assemble": "pending",
            "correct": "pending",
            "review": "pending",
            "export": "pending",
        },
        "colontitles_detected": [],
    }


def init_workdir(
    out_dir: Path,
    pdf_source: Path,
    *,
    copy_pdf: bool = True,
    flags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not pdf_source.is_file():
        raise FileNotFoundError(f"PDF не найден: {pdf_source}")

    out_dir.mkdir(parents=True, exist_ok=True)
    title = out_dir.name

    if copy_pdf:
        dest = out_dir / SOURCE_PDF
        if not dest.is_file() or dest.stat().st_size != pdf_source.stat().st_size:
            shutil.copy2(pdf_source, dest)
        pdf_rel = SOURCE_PDF
    else:
        pdf_rel = str(pdf_source.resolve())

    existing = load_book(out_dir)
    if existing:
        book = existing
        book.setdefault("flags", {})
        book.setdefault("stages", default_book(title)["stages"])
        book["title"] = title
        book["pdf_path"] = pdf_rel
        book["pipeline_version"] = PIPELINE_VERSION
    else:
        book = default_book(title, pdf_rel)

    if flags:
        book.setdefault("flags", {}).update(flags)
    book.setdefault("stages", {})["init"] = "done"

    save_book(out_dir, book)
    return book


def migrate_workdir(out_dir: Path, *, copy_pdf: bool = False) -> Dict[str, Any]:
    """Создать book.json для существующей папки (manifest.json, pages/)."""
    from .page_store import load_manifest

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out_dir)
    if not manifest and not (out_dir / "pages").is_dir():
        raise FileNotFoundError(f"Нет manifest.json и pages/ в {out_dir}")

    title = out_dir.name
    book = load_book(out_dir) or default_book(title)

    local_pdf = out_dir / SOURCE_PDF
    if local_pdf.is_file():
        book["pdf_path"] = SOURCE_PDF
    elif manifest.get("pdf"):
        ext = Path(str(manifest["pdf"]))
        if ext.is_file():
            if copy_pdf:
                shutil.copy2(ext, local_pdf)
                book["pdf_path"] = SOURCE_PDF
            else:
                book["pdf_path"] = str(ext.resolve())
        else:
            book["pdf_path"] = str(manifest["pdf"])

    completed = manifest.get("completed_pages") or []
    if completed:
        book.setdefault("stages", {})["ocr"] = "done"
    if (out_dir / "book_structured.json").is_file():
        book.setdefault("stages", {})["assemble"] = "done"

    book["pipeline_version"] = PIPELINE_VERSION
    save_book(out_dir, book)
    consolidate_workdir(out_dir, copy_pdf=copy_pdf)
    return load_book(out_dir) or book


def resolve_pdf(out_dir: Path) -> Path:
    """Абсолютный путь к PDF для OCR / corrector."""
    book = load_book(out_dir)
    if book and book.get("pdf_path"):
        p = Path(book["pdf_path"])
        if not p.is_absolute():
            p = out_dir / p
        if p.is_file():
            return p.resolve()

    from .page_store import load_manifest

    manifest = load_manifest(out_dir)
    if manifest.get("pdf"):
        p = Path(str(manifest["pdf"]))
        if p.is_file():
            return p.resolve()

    local = out_dir / SOURCE_PDF
    if local.is_file():
        return local.resolve()

    raise FileNotFoundError(
        f"PDF не найден в {out_dir}. Запустите: bpp init --pdf … --out {out_dir}"
    )


def update_stage(out_dir: Path, stage: str, status: str) -> None:
    book = load_book(out_dir)
    if not book:
        book = default_book(out_dir.name)
    book.setdefault("stages", {})[stage] = status
    save_book(out_dir, book)


def list_books(out_root: Path) -> List[Dict[str, Any]]:
    if not out_root.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for child in sorted(out_root.iterdir()):
        if not child.is_dir():
            continue
        book = load_book(child)
        if not book:
            continue
        stages = book.get("stages") or {}
        rows.append({
            "slug": child.name,
            "path": str(child.resolve()),
            "title": book.get("title") or child.name,
            "stages": stages,
            "pdf_path": book.get("pdf_path"),
        })
    return rows


def normalize_spelling(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("modern", "m", "new", "современ"):
        return SPELLING_MODERN
    return SPELLING_PRE_REFORM


def resolve_mechanics(
    book: Optional[Dict[str, Any]],
    *,
    no_modernize: bool = False,
    no_oldspelling: bool = False,
) -> tuple[bool, bool]:
    """(modernize, use_oldspelling). Явные CLI-флаги перекрывают book.json."""
    if no_modernize or no_oldspelling:
        return (not no_modernize, not no_oldspelling)
    flags = (book or {}).get("flags") or {}
    if flags.get("spelling") == SPELLING_MODERN:
        return (True, False)
    return (True, True)


def resolve_target_orthography(
    book: Optional[Dict[str, Any]],
    cli: Optional[str] = None,
) -> str:
    if cli:
        return cli
    flags = (book or {}).get("flags") or {}
    val = flags.get("target_orthography") or TARGET_ORTHOGRAPHY_MODERN
    if val not in (TARGET_ORTHOGRAPHY_MODERN, TARGET_ORTHOGRAPHY_FAITHFUL):
        return TARGET_ORTHOGRAPHY_MODERN
    return val


def spelling_label(book: Optional[Dict[str, Any]]) -> str:
    flags = (book or {}).get("flags") or {}
    return "современная" if flags.get("spelling") == SPELLING_MODERN else "дореформенная"
