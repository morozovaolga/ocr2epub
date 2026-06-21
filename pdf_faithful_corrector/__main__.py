# -*- coding: utf-8 -*-
"""CLI: построить corrector_queue.json — diff нашего текста vs PDF по блокам."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .queue import run_from_workdir
from .workdir import resolve_pdf as resolve_workdir_pdf


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "PDF-faithful corrector (MVP): сравнить final_better.txt с текстовым слоем PDF "
            "по блокам structured.json → corrector_queue.json"
        ),
    )
    ap.add_argument(
        "--pdf",
        default="",
        help="PDF (необязательно, если в workdir есть book.json / source.pdf)",
    )
    ap.add_argument(
        "--workdir",
        required=True,
        help="Папка out/<slug>/ (book.json, pages/, final_better.txt)",
    )
    ap.add_argument(
        "--out",
        default="",
        help="Выходной JSON (по умолчанию: <workdir>/corrector_queue.json)",
    )
    ap.add_argument("--structured", default="", help="Явный путь к structured JSON")
    ap.add_argument("--text", default="", help="Явный путь к TXT (final_better и т.д.)")
    ap.add_argument(
        "--review-threshold",
        type=float,
        default=0.88,
        help="Similarity ниже порога → status=review (по умолчанию 0.88)",
    )
    ap.add_argument(
        "--target-orthography",
        choices=("modern", "faithful"),
        default="modern",
        help="modern: ожидаем отличия от PDF из-за модернизации; faithful: сверка буква в букву",
    )
    args = ap.parse_args(argv)

    workdir = Path(args.workdir)
    if not workdir.is_dir():
        print(f"Нет папки workdir: {workdir}", file=sys.stderr)
        return 1

    if args.pdf:
        pdf = Path(args.pdf)
        if not pdf.is_file():
            print(f"Нет PDF: {pdf}", file=sys.stderr)
            return 1
    else:
        try:
            pdf = resolve_workdir_pdf(workdir)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1

    out = Path(args.out) if args.out else workdir / "corrector_queue.json"
    structured = Path(args.structured) if args.structured else None
    text = Path(args.text) if args.text else None

    try:
        queue = run_from_workdir(
            pdf,
            workdir,
            out,
            structured=structured,
            text=text,
            review_threshold=args.review_threshold,
            target_orthography=args.target_orthography,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    m = queue["meta"]
    print(f"Saved: {out}")
    layout = m.get("queue_layout") or ""
    if layout == "one_item_per_block_v3":
        print(
            f"Блоков: {m.get('blocks_total', m['paragraphs_total'])} | "
            f"bbox: {m.get('blocks_with_bbox', '?')} | "
            f"ok: {m['status_ok']} | review: {m['status_review']} | "
            f"layout: {layout}"
        )
    else:
        print(
            f"Абзацев: {m['paragraphs_total']} | "
            f"ok: {m['status_ok']} | review: {m['status_review']} | "
            f"no_pdf_text: {m['status_no_pdf_text']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
