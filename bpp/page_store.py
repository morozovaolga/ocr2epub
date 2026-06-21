# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def page_path(page_dir: Path, page: int) -> Path:
    return page_dir / f"page_{page:04d}.json"


def manifest_path(out_dir: Path) -> Path:
    return out_dir / "manifest.json"


def load_manifest(out_dir: Path) -> Dict[str, Any]:
    p = manifest_path(out_dir)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(out_dir: Path, manifest: Dict[str, Any]) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path(out_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def init_manifest(out_dir: Path, *, pdf: Path, total_pages: int) -> Dict[str, Any]:
    from .workdir import manifest_pdf_ref

    m = load_manifest(out_dir)
    m.update({
        "version": 2,
        "pipeline": "book_page_pipeline",
        "pdf": manifest_pdf_ref(out_dir, pdf),
        "total_pages": total_pages,
        "page_dir": "pages",
        "completed_pages": sorted(set(m.get("completed_pages") or [])),
    })
    save_manifest(out_dir, m)
    return m


def load_page(page_dir: Path, page: int) -> Optional[Dict[str, Any]]:
    p = page_path(page_dir, page)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_page(page_dir: Path, data: Dict[str, Any]) -> Path:
    page_dir.mkdir(parents=True, exist_ok=True)
    page = int(data["page"])
    p = page_path(page_dir, page)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def mark_page_done(out_dir: Path, page: int) -> None:
    m = load_manifest(out_dir)
    done: Set[int] = set(int(x) for x in (m.get("completed_pages") or []))
    done.add(int(page))
    m["completed_pages"] = sorted(done)
    save_manifest(out_dir, m)
