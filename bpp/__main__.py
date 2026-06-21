# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from .assemble import assemble
from .correct import correct_workdir
from .export import export_workdir
from .mech import apply_to_workdir
from .pipeline import run_pages
from .paths import default_corrector_root, package_root
from .workdir import (
    init_workdir,
    list_books,
    load_book,
    migrate_workdir,
    normalize_spelling,
    resolve_mechanics,
    resolve_target_orthography,
    resolve_pdf,
    save_book,
    consolidate_workdir,
    spelling_label,
)


def _default_out_root() -> Path:
    return Path(__file__).resolve().parent.parent / "out"


def _cmd_init(args: argparse.Namespace) -> int:
    if not args.pdf.is_file():
        print(f"PDF не найден: {args.pdf}", file=sys.stderr)
        return 1
    book = init_workdir(
        args.out,
        args.pdf,
        copy_pdf=not args.no_copy_pdf,
        flags={
            "two_columns": args.two_columns,
            "engine": args.engine,
            "poetry": args.poetry,
            "spelling": normalize_spelling(args.spelling),
        },
    )
    print(f"Workdir: {args.out.resolve()}")
    print(f"  book.json — title={book['title']!r}, pdf={book['pdf_path']!r}")
    print(f"  орфография PDF: {spelling_label(book)}")
    if args.no_copy_pdf:
        print(
            "  ⚠ --no-copy-pdf: PDF вне workdir. Для одной папки используйте consolidate или init без этого флага.",
            file=sys.stderr,
        )
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    try:
        book = migrate_workdir(args.out, copy_pdf=args.copy_pdf)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Миграция: {args.out.resolve()}")
    print(f"  pdf={book.get('pdf_path')!r}, stages={book.get('stages')}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    root = args.out_root or _default_out_root()
    rows = list_books(root)
    if not rows:
        print(f"Нет книг с book.json в {root}")
        return 0
    for row in rows:
        st = row.get("stages") or {}
        ocr = st.get("ocr", "?")
        asm = st.get("assemble", "?")
        exp = st.get("export", "?")
        print(f"{row['slug']}\tocr={ocr}\tassemble={asm}\texport={exp}\t{row['path']}")
    return 0


def _apply_run_flags(out_dir: Path, args: argparse.Namespace) -> None:
    book = load_book(out_dir)
    if not book:
        return
    flags = book.setdefault("flags", {})
    if getattr(args, "two_columns", False):
        flags["two_columns"] = True
    if getattr(args, "poetry", False):
        flags["poetry"] = True
    if getattr(args, "engine", None):
        flags["engine"] = args.engine
    save_book(out_dir, book)


def _cmd_run(args: argparse.Namespace) -> int:
    out_dir = args.out
    consolidate_workdir(out_dir)
    _apply_run_flags(out_dir, args)
    if args.pdf:
        pdf_path = args.pdf
        if not pdf_path.is_file():
            print(f"PDF не найден: {pdf_path}", file=sys.stderr)
            return 1
    else:
        try:
            pdf_path = resolve_pdf(out_dir)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1

    book = load_book(out_dir)
    modernize, use_oldspelling = resolve_mechanics(
        book,
        no_modernize=args.no_modernize,
        no_oldspelling=getattr(args, "no_oldspelling", False),
    )

    result = run_pages(
        pdf_path,
        out_dir,
        page_from=args.page_from,
        page_to=args.page_to or None,
        resume=not args.no_resume,
        force=args.force,
        apply_rules=not args.no_rules,
        rules_dir=Path(args.rules_dir) if args.rules_dir else None,
        modernize=modernize,
        use_oldspelling=use_oldspelling,
        two_columns=True if args.two_columns else None,
        poetry=True if args.poetry else None,
        engine=args.engine or None,
    )
    print(f"Готово: обработано {result['pages_done']}, пропущено {result['pages_skipped']}")
    print(f"Папка: {result['out_dir']}")
    if not args.no_assemble:
        asm = assemble(out_dir)
        print(f"Сборка: {asm['blocks']} блоков, bbox: {asm['blocks_with_bbox']}")
        print(f"  {asm['book_structured']}")
        print(f"  {asm['final_better']}")
        if asm.get("corrector_queue"):
            print(f"  {asm['corrector_queue']}")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    if args.page_to and args.page_to > 0:
        page_to = args.page_to
    else:
        page_to = None
    book = load_book(args.out)
    modernize, use_oldspelling = resolve_mechanics(
        book,
        no_modernize=args.no_modernize,
        no_oldspelling=args.no_oldspelling,
    )
    result = apply_to_workdir(
        args.out,
        page_from=args.page_from,
        page_to=page_to,
        rules_dir=Path(args.rules_dir) if args.rules_dir else None,
        modernize=modernize,
        use_oldspelling=use_oldspelling,
    )
    print(f"Обновлено страниц: {result['pages_updated']}")
    if not args.no_assemble:
        asm = assemble(args.out, build_queue=not args.no_queue)
        print(f"Сборка: {asm['blocks']} блоков")
        print(asm["final_better"])
    return 0


def _cmd_correct(args: argparse.Namespace) -> int:
    page_to = args.page_to if args.page_to > 0 else None
    book = load_book(args.out)
    target_orth = resolve_target_orthography(
        book,
        getattr(args, "target_orthography", None) or None,
    )
    try:
        result = correct_workdir(
            args.out,
            page_from=args.page_from,
            page_to=page_to,
            review_only=not args.all_blocks,
            review_threshold=args.review_threshold,
            force=args.force,
            ollama_url=args.ollama_url,
            ollama_model=args.ollama_model,
            ollama_timeout=args.ollama_timeout,
            use_cloud=args.cloud,
            verbose=not args.quiet,
            target_orthography=target_orth,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        f"LLM: страниц {result['pages']}, блоков {result['blocks_processed']}, "
        f"изменено {result['blocks_changed']}, на review {result['blocks_review']}"
    )
    if result.get("blocks_errors"):
        print(f"  ошибок LLM: {result['blocks_errors']}")
    if result.get("blocks_skipped"):
        print(f"  пропущено (ok): {result['blocks_skipped']}")
    print(f"  модель: {result['ollama_model']}, лог: {result['log']}")
    if not args.no_assemble:
        asm = assemble(args.out, build_queue=not args.no_queue)
        print(f"Сборка: {asm['blocks']} блоков")
        print(asm.get("final_corrected") or asm["final_better"])
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    asm = assemble(args.out, build_queue=not args.no_queue)
    print(f"Страниц: {asm['pages']}, блоков: {asm['blocks']}, с bbox: {asm['blocks_with_bbox']}")
    print(asm["book_structured"])
    print(asm["structured_rules"])
    print(asm["final_better"])
    if asm.get("final_corrected"):
        print(asm["final_corrected"])
    if asm.get("corrector_queue"):
        print(asm["corrector_queue"])
    return 0


def _cmd_consolidate(args: argparse.Namespace) -> int:
    result = consolidate_workdir(args.out, copy_pdf=not args.no_copy_pdf)
    if not result.get("ok"):
        print(result.get("reason") or "не удалось", file=sys.stderr)
        return 1
    msg = f"Workdir: {result['workdir']}\n  PDF: {result['pdf']}"
    if result.get("copied"):
        msg += " (скопирован в source.pdf)"
    print(msg)
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    result = consolidate_workdir(args.out)
    if not result.get("ok"):
        print(result.get("reason") or "нет source.pdf", file=sys.stderr)
        return 1
    corrector_root = default_corrector_root()
    cmd = [
        sys.executable,
        "-m",
        "pdf_faithful_corrector.web_app",
        "--workdir",
        str(args.out.resolve()),
    ]
    if args.port and args.port > 0:
        cmd.extend(["--port", str(args.port)])
    if args.no_browser:
        cmd.append("--no-browser")
    if args.no_ollama:
        cmd.append("--no-ollama")
    book = load_book(args.out)
    target_orth = resolve_target_orthography(book, args.target_orthography or None)
    cmd.extend(["--target-orthography", target_orth])
    print(f"Review UI → {args.out.resolve()}")
    print(f"  source.pdf: {result['pdf']}")
    print(f"  орфография review/LLM: {target_orth}")
    return subprocess.call(cmd, cwd=str(package_root()))


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        result = export_workdir(
            args.out,
            refresh=not args.no_refresh,
            title=args.title or None,
            author=args.author if args.author else None,
        )
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Export: {result.get('title')!r}, блоков {result['blocks']}, сносок {result['footnotes']}")
    if result.get("book_export"):
        print(f"  TXT: {result['book_export']}")
    if result.get("footnotes_json"):
        print(f"  Сноски: {result['footnotes_json']}")
    return 0


def _cmd_sync_global_rules(args: argparse.Namespace) -> int:
    """Перенести ручные правки из corrector_training.jsonl в общую базу."""
    root = default_corrector_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pdf_faithful_corrector.learned_rules import global_learned_path, merge_training_log_to_global

    log_path = args.out / "corrector_training.jsonl"
    if not log_path.is_file():
        log_path = args.out / "corrector_training_latest.jsonl"
    if not log_path.is_file():
        print(f"Нет training log в {args.out}", file=sys.stderr)
        return 1
    added = merge_training_log_to_global(log_path, book=args.out.name)
    print(f"Общая база: {global_learned_path()}")
    print(f"Добавлено правил: {added}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bpp",
        description="Book Page Pipeline v3 — постраничное извлечение PDF с bbox, единый out/<slug>/",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Создать out/<slug>/ + book.json + source.pdf")
    init.add_argument("--pdf", type=Path, required=True)
    init.add_argument("--out", type=Path, required=True, help="out/<slug>")
    init.add_argument("--no-copy-pdf", action="store_true", help="Не копировать PDF, только путь в book.json")
    init.add_argument("--two-columns", action="store_true")
    init.add_argument("--engine", default="pymupdf", choices=("pymupdf", "auto", "tesseract"))
    init.add_argument("--poetry", action="store_true")
    init.add_argument(
        "--spelling",
        default="pre_reform",
        choices=("pre_reform", "modern", "old"),
        help="Орфография в PDF: pre_reform/old (дореформенная, по умол.) или modern (уже современная)",
    )
    init.set_defaults(func=_cmd_init)

    mig = sub.add_parser("migrate", help="book.json для существующей папки (manifest + pages)")
    mig.add_argument("--out", type=Path, required=True)
    mig.add_argument("--copy-pdf", action="store_true", help="Скопировать PDF из manifest в source.pdf")
    mig.set_defaults(func=_cmd_migrate)

    lst = sub.add_parser("list", help="Список книг в out/")
    lst.add_argument("--out-root", type=Path, default=None, help="Корень out/ (по умолчанию bpp/../out)")
    lst.set_defaults(func=_cmd_list)

    run = sub.add_parser("run", help="Распознать/извлечь страницы PDF")
    run.add_argument("--out", type=Path, required=True, help="out/<slug>")
    run.add_argument("--pdf", type=Path, default=None, help="PDF (иначе из book.json)")
    run.add_argument("--page-from", type=int, default=1)
    run.add_argument("--page-to", type=int, default=0, help="0 = до конца")
    run.add_argument("--no-resume", action="store_true")
    run.add_argument("--force", action="store_true")
    run.add_argument("--no-rules", action="store_true")
    run.add_argument("--no-modernize", action="store_true")
    run.add_argument("--no-oldspelling", action="store_true", help="Перекрывает spelling из book.json")
    run.add_argument("--no-assemble", action="store_true", help="Не собирать book_structured после run")
    run.add_argument("--two-columns", action="store_true")
    run.add_argument("--poetry", action="store_true")
    run.add_argument(
        "--engine",
        default="",
        choices=("", "auto", "pymupdf", "tesseract"),
        help="OCR-движок (пусто = из book.json)",
    )
    run.add_argument(
        "--rules-dir",
        default="",
        help="Папка rules/ocr (по умолчанию assets/rules/ocr в репозитории)",
    )
    run.set_defaults(func=_cmd_run)

    asm = sub.add_parser("assemble", help="Собрать book_structured + final_better + очередь")
    asm.add_argument("--out", type=Path, required=True)
    asm.add_argument("--no-queue", action="store_true", help="Не строить corrector_queue.json")
    asm.set_defaults(func=_cmd_assemble)

    apply_cmd = sub.add_parser(
        "apply",
        help="Rules + oldspelling + modernize на готовых pages/ (без OCR)",
    )
    apply_cmd.add_argument("--out", type=Path, required=True)
    apply_cmd.add_argument("--page-from", type=int, default=1)
    apply_cmd.add_argument("--page-to", type=int, default=0, help="0 = до конца")
    apply_cmd.add_argument("--no-modernize", action="store_true")
    apply_cmd.add_argument("--no-oldspelling", action="store_true")
    apply_cmd.add_argument("--no-assemble", action="store_true")
    apply_cmd.add_argument("--no-queue", action="store_true")
    apply_cmd.add_argument("--rules-dir", default="")
    apply_cmd.set_defaults(func=_cmd_apply)

    corr = sub.add_parser(
        "correct",
        help="LLM find_apply по блокам (Ollama qwen2.5:3b, опционально GigaChat)",
    )
    corr.add_argument("--out", type=Path, required=True)
    corr.add_argument("--page-from", type=int, default=1)
    corr.add_argument("--page-to", type=int, default=0, help="0 = до конца")
    corr.add_argument(
        "--all-blocks",
        action="store_true",
        help="Корректировать все блоки, не только review",
    )
    corr.add_argument("--review-threshold", type=float, default=0.88)
    corr.add_argument("--force", action="store_true", help="Перекорректировать даже готовые блоки")
    corr.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    corr.add_argument("--ollama-model", default="qwen2.5:3b")
    corr.add_argument("--ollama-timeout", type=float, default=300)
    corr.add_argument(
        "--cloud",
        action="store_true",
        help="После Ollama — GigaChat для блоков review (нужен GIGACHAT_CREDENTIALS)",
    )
    corr.add_argument("--no-assemble", action="store_true")
    corr.add_argument("--no-queue", action="store_true")
    corr.add_argument("--quiet", action="store_true", help="Без построчного прогресса")
    corr.add_argument(
        "--target-orthography",
        choices=("modern", "faithful"),
        default="",
        help="Целевая орфография LLM (по умолчанию из book.json, иначе modern)",
    )
    corr.set_defaults(func=_cmd_correct)

    exp = sub.add_parser("export", help="Финальный TXT из book_structured.json")
    exp.add_argument("--out", type=Path, required=True)
    exp.add_argument("--no-refresh", action="store_true", help="Не вызывать assemble перед export")
    exp.add_argument("--title", default="", help="Заголовок книги (иначе из book.json)")
    exp.add_argument("--author", default="", help="Автор (записывается в book.json)")
    exp.set_defaults(func=_cmd_export)

    con = sub.add_parser(
        "consolidate",
        help="Скопировать PDF в workdir/source.pdf и исправить book.json/manifest",
    )
    con.add_argument("--out", type=Path, required=True)
    con.add_argument(
        "--no-copy-pdf",
        action="store_true",
        help="Только проверить, не копировать",
    )
    con.set_defaults(func=_cmd_consolidate)

    rev = sub.add_parser(
        "review",
        help="Веб-корректор (pdf_faithful_corrector) для этого workdir",
    )
    rev.add_argument("--out", type=Path, required=True)
    rev.add_argument("--port", type=int, default=0, help="Порт (0 = 8765)")
    rev.add_argument("--no-browser", action="store_true")
    rev.add_argument("--no-ollama", action="store_true")
    rev.add_argument(
        "--target-orthography",
        choices=("modern", "faithful"),
        default="",
        help="modern — современная орфография; faithful — сохранять дореформенное написание",
    )
    rev.set_defaults(func=_cmd_review)

    sync = sub.add_parser(
        "sync-global-rules",
        help="Дописать ручные правки книги в assets/rules/ocr/dictionaries/human_learned.jsonl",
    )
    sync.add_argument("--out", type=Path, required=True)
    sync.set_defaults(func=_cmd_sync_global_rules)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
