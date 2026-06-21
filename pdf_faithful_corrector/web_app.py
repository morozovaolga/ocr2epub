# -*- coding: utf-8 -*-
"""Локальный веб-интерфейс для ручной корректуры по corrector_queue.json."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .align import load_paragraphs
from .learned_rules import (
    apply_word_rules,
    global_learned_path,
    harvest_and_merge_training_log,
    load_rules_jsonl,
    merge_record_to_global,
    merge_training_log_to_global,
)
from .ollama_judge import ollama_status, suggest_correction
from .queue import apply_all_decisions, merge_paragraph_from_decisions, run_from_workdir, sync_v3_decisions_to_pages
from .workdir import resolve_pdf as resolve_workdir_pdf
from .page_snapshots import render_page_clip
from .spellcheck import load_learned_bad_pairs, spellcheck_text
from .training_log import append_jsonl, build_training_record, rebuild_training_latest

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class CorrectorState:
    def __init__(
        self,
        *,
        pdf_path: Optional[Path] = None,
        workdir: Optional[Path] = None,
        queue_path: Optional[Path] = None,
        review_threshold: float = 0.88,
        target_orthography: str = "modern",
        ollama_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "qwen2.5:3b",
        ollama_enabled: bool = True,
        ollama_mode: str = "find_apply",
    ) -> None:
        self.pdf_path = pdf_path
        self.workdir = workdir
        self.queue_path = queue_path
        self.review_threshold = review_threshold
        self.target_orthography = target_orthography
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.ollama_enabled = ollama_enabled
        self.ollama_mode = ollama_mode
        self.queue: Dict[str, Any] = {"meta": {}, "items": []}
        self.decisions: Dict[str, Any] = {"decisions": {}, "meta": {}}
        self._lock = threading.Lock()
        self._spell_cache: Dict[str, Dict[str, Any]] = {}
        self._learned_pairs: Optional[List[Dict[str, str]]] = None

        if queue_path and queue_path.is_file():
            self.load_queue(queue_path)
        elif workdir:
            if not pdf_path:
                try:
                    pdf_path = resolve_workdir_pdf(workdir)
                except FileNotFoundError:
                    pass
            if pdf_path:
                self.pdf_path = pdf_path
                self.rebuild_queue()

        if workdir:
            self.load_decisions()

    @property
    def decisions_path(self) -> Optional[Path]:
        if self.workdir:
            return self.workdir / "corrector_decisions.json"
        if self.queue_path:
            return self.queue_path.parent / "corrector_decisions.json"
        return None

    @property
    def training_log_path(self) -> Optional[Path]:
        base = self.workdir or (self.queue_path.parent if self.queue_path else None)
        return (base / "corrector_training.jsonl") if base else None

    @property
    def training_latest_path(self) -> Optional[Path]:
        base = self.workdir or (self.queue_path.parent if self.queue_path else None)
        return (base / "corrector_training_latest.jsonl") if base else None

    @property
    def learned_rules_path(self) -> Optional[Path]:
        base = self.workdir or (self.queue_path.parent if self.queue_path else None)
        return (base / "rules_learned.jsonl") if base else None

    def learned_bad_pairs(self) -> List[Dict[str, str]]:
        if self._learned_pairs is None:
            self._learned_pairs = load_learned_bad_pairs(self.workdir)
        return self._learned_pairs

    def reload_learned_pairs(self) -> None:
        self._learned_pairs = None
        self._spell_cache.clear()

    def spellcheck(self, text: str) -> Dict[str, Any]:
        key = text or ""
        cached = self._spell_cache.get(key)
        if cached is not None:
            return cached
        pairs = self.learned_bad_pairs()
        result = spellcheck_text(text, workdir=self.workdir, learned_pairs=pairs)
        if result.get("ok") or result.get("learned_errors"):
            self._spell_cache[key] = result
        return result

    def load_queue(self, path: Path) -> None:
        with self._lock:
            self.queue = json.loads(path.read_text(encoding="utf-8"))
            self.queue_path = path
            meta = self.queue.get("meta") or {}
            if meta.get("pdf"):
                self.pdf_path = Path(meta["pdf"])
            if not self.workdir and path.parent.is_dir():
                self.workdir = path.parent

    def load_decisions(self) -> None:
        path = self.decisions_path
        if not path or not path.is_file():
            self.decisions = {"decisions": {}, "meta": {}}
            return
        with self._lock:
            self.decisions = json.loads(path.read_text(encoding="utf-8"))

    def _write_decisions_file(self) -> Path:
        path = self.decisions_path
        if not path:
            raise ValueError("Нет пути для сохранения решений (укажите --workdir или --queue)")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.decisions.setdefault("meta", {})
        self.decisions["meta"].update({
            "queue": str(self.queue_path) if self.queue_path else "",
            "pdf": str(self.pdf_path) if self.pdf_path else "",
        })
        path.write_text(
            json.dumps(self.decisions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def save_decisions(self) -> Path:
        with self._lock:
            return self._write_decisions_file()

    def record_decision_updates(self, incoming: Dict[str, Any]) -> tuple[List[Path], int]:
        """Сохранить decisions, training log и дописать ручные правки в общую базу."""
        written: List[Path] = []
        global_added = 0
        log_path = self.training_log_path
        latest_path = self.training_latest_path
        book_slug = self.workdir.name if self.workdir else ""

        with self._lock:
            self.decisions.setdefault("decisions", {})
            queue_meta = dict(self.queue.get("meta") or {})
            if self.queue_path:
                queue_meta.setdefault("queue", str(self.queue_path))

            for key, decision in incoming.items():
                self.decisions["decisions"][key] = decision
                if not log_path:
                    continue
                try:
                    item = self._item_for_decision_key(key)
                except KeyError:
                    continue
                record = build_training_record(item, decision, queue_meta=queue_meta)
                append_jsonl(log_path, record)
                written.append(log_path)
                if book_slug:
                    global_added += merge_record_to_global(record, book=book_slug)

            saved = self._write_decisions_file()
            if log_path and latest_path and log_path.is_file():
                rebuild_training_latest(log_path, latest_path)
                if latest_path not in written:
                    written.append(latest_path)

        if saved not in written:
            written.insert(0, saved)
        return written, global_added

    def rebuild_queue(self) -> Dict[str, Any]:
        if not self.workdir:
            raise ValueError("Для пересборки нужен --workdir")
        if not self.pdf_path:
            self.pdf_path = resolve_workdir_pdf(self.workdir)
        out = self.workdir / "corrector_queue.json"
        with self._lock:
            self.queue = run_from_workdir(
                self.pdf_path,
                self.workdir,
                out,
                review_threshold=self.review_threshold,
                target_orthography=self.target_orthography,
            )
            self.queue_path = out
        return self.queue

    def render_item_image(
        self,
        paragraph_index: int,
        *,
        page: Optional[int] = None,
        zoom: float = 2.0,
    ) -> bytes:
        if not self.pdf_path or not self.pdf_path.is_file():
            raise FileNotFoundError("PDF не задан")

        item = self._item_for_review(paragraph_index, page)
        pages = item.get("pages") or []
        bboxes = item.get("bboxes") or []
        if not pages:
            raise ValueError("У абзаца нет привязки к странице PDF")

        page_no = int(page) if page is not None else int(item.get("page") or pages[0])
        return render_page_clip(
            self.pdf_path,
            page_no=page_no,
            pages=pages,
            bboxes=bboxes,
            zoom=zoom,
        )

    def _item_by_index(self, paragraph_index: int) -> Dict[str, Any]:
        for item in self.queue.get("items") or []:
            if int(item.get("paragraph_index", -1)) == paragraph_index:
                return item
        raise KeyError(f"Абзац {paragraph_index} не найден")

    def _item_by_page(self, page: int) -> Dict[str, Any]:
        for item in self.queue.get("items") or []:
            if int(item.get("page", 0)) == int(page):
                return item
        raise KeyError(f"Лист PDF {page} не найден в очереди")

    def _item_for_review(self, paragraph_index: int, page: Optional[int] = None) -> Dict[str, Any]:
        layout = (self.queue.get("meta") or {}).get("queue_layout")
        if layout == "one_item_per_block_v3":
            return self._item_by_index(paragraph_index)
        if page is not None and layout == "one_item_per_pdf_page_v2":
            return self._item_by_page(page)

        matches = [
            i for i in (self.queue.get("items") or [])
            if int(i.get("paragraph_index", -1)) == int(paragraph_index)
        ]
        if not matches:
            raise KeyError(f"Абзац {paragraph_index} не найден")
        if page is not None:
            for item in matches:
                if int(item.get("page", 0)) == int(page):
                    return item
        if len(matches) == 1:
            return matches[0]
        return self._item_by_index(paragraph_index)

    def _item_for_decision_key(self, key: str) -> Dict[str, Any]:
        """Элемент очереди для training log (ключ «pi:page» или legacy «pi»)."""
        for item in self.queue.get("items") or []:
            sk = item.get("slice_key") or f"{item.get('paragraph_index')}:{item.get('page', '')}"
            if sk == key:
                return item
        if ":" in key:
            pi_s, page_s = key.split(":", 1)
            try:
                return self._item_for_review(int(pi_s), int(page_s))
            except KeyError:
                pass
            item = self._item_by_index(int(pi_s))
            page = int(page_s)
            for sl in item.get("page_slices") or []:
                if int(sl.get("page", 0)) == page:
                    return {
                        **item,
                        "our_text": sl.get("our_text") or "",
                        "pdf_text": sl.get("pdf_text") or "",
                        "pages": [page],
                        "page": page,
                        "similarity": sl.get("similarity", item.get("similarity")),
                        "status": sl.get("status", item.get("status")),
                        "diff": sl.get("diff") or [],
                        "slice_key": key,
                    }
            raise KeyError(f"Страница {page} не найдена в абзаце {pi_s}")
        return self._item_by_index(int(key))

    def apply_decisions(self) -> Path:
        if not self.workdir:
            raise ValueError("Нет workdir для записи final_corrected.txt")
        text_path = self.queue.get("meta", {}).get("text")
        if not text_path:
            raise ValueError("В очереди нет meta.text")
        paragraphs = load_paragraphs(text_path)
        decisions = self.decisions.get("decisions") or {}
        items = self.queue.get("items") or []
        layout = (self.queue.get("meta") or {}).get("queue_layout") or ""
        if layout == "one_item_per_block_v3" and len(paragraphs) != len(items):
            paragraphs = [str(it.get("our_text") or "") for it in items]
        apply_all_decisions(items, decisions, paragraphs, queue_layout=layout)

        out = self.workdir / "final_corrected.txt"
        body = "\n\n".join(paragraphs) + "\n"
        out.write_text(body, encoding="utf-8")

        if layout == "one_item_per_block_v3" and self.workdir:
            sync_v3_decisions_to_pages(self.workdir, items, decisions, paragraphs)
        return out

    def apply_and_refresh(self, *, update_source: bool = True) -> Dict[str, Any]:
        """Применить правки, выучить правила, обновить final_better и пересобрать очередь."""
        if not self.workdir:
            raise ValueError("Нет workdir")

        out = self.apply_decisions()
        rules_path = self.learned_rules_path
        rules_added = 0
        global_rules_added = 0
        book_slug = self.workdir.name if self.workdir else ""
        if rules_path and self.training_latest_path:
            rules_added = harvest_and_merge_training_log(
                self.training_latest_path,
                rules_path,
            )
        if self.training_log_path and book_slug:
            global_rules_added = merge_training_log_to_global(
                self.training_log_path,
                book=book_slug,
            )
        self.reload_learned_pairs()

        text = out.read_text(encoding="utf-8")
        merged_rules = load_rules_jsonl(rules_path) if rules_path and rules_path.is_file() else []
        global_path = global_learned_path()
        if global_path.is_file():
            seen = {pair for pair in merged_rules}
            for pair in load_rules_jsonl(global_path):
                if pair not in seen:
                    merged_rules.append(pair)
                    seen.add(pair)
        if merged_rules:
            text = apply_word_rules(text, merged_rules)
            out.write_text(text, encoding="utf-8")

        final_better = self.workdir / "final_better.txt"
        if update_source and final_better.is_file():
            bak = self.workdir / "final_better.bak"
            shutil.copy2(final_better, bak)
        if update_source:
            final_better.write_text(text, encoding="utf-8")

        queue_meta = {}
        if self.pdf_path and self.pdf_path.is_file():
            self.queue = run_from_workdir(
                self.pdf_path,
                self.workdir,
                self.workdir / "corrector_queue.json",
                review_threshold=self.review_threshold,
                target_orthography=self.target_orthography,
            )
            self.queue_path = self.workdir / "corrector_queue.json"
            queue_meta = self.queue.get("meta") or {}

        return {
            "corrected": str(out),
            "final_better": str(final_better) if update_source else "",
            "rules_learned": str(rules_path) if rules_path else "",
            "rules_added": rules_added,
            "global_rules_learned": str(global_learned_path()),
            "global_rules_added": global_rules_added,
            "queue_meta": queue_meta,
        }

    def ollama_suggest(
        self,
        paragraph_index: int,
        *,
        our_text: Optional[str] = None,
        page: Optional[int] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.ollama_enabled:
            raise ValueError("Ollama отключён (запуск с --no-ollama)")
        item = self._item_for_review(paragraph_index, page)
        return suggest_correction(
            item,
            our_text=our_text,
            page=page,
            target_orthography=self.target_orthography,
            model=self.ollama_model,
            base_url=self.ollama_url,
            mode=mode or self.ollama_mode,
        )


def make_handler(state: CorrectorState, web_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PDFFaithfulCorrector/0.1"

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, data: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def _static_path(self, url_path: str) -> Optional[Path]:
            rel = url_path.lstrip("/")
            if not rel or rel == "/":
                rel = "index.html"
            candidate = (web_dir / rel).resolve()
            if not str(candidate).startswith(str(web_dir.resolve())):
                return None
            return candidate if candidate.is_file() else None

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/api/queue":
                self._send_json(state.queue)
                return
            if path == "/api/decisions":
                self._send_json(state.decisions)
                return
            if path == "/api/config":
                ollama = ollama_status(state.ollama_url, state.ollama_model) if state.ollama_enabled else {
                    "ok": False,
                    "error": "disabled",
                }
                self._send_json({
                    "pdf": str(state.pdf_path) if state.pdf_path else "",
                    "workdir": str(state.workdir) if state.workdir else "",
                    "queue": str(state.queue_path) if state.queue_path else "",
                    "target_orthography": state.target_orthography,
                    "review_threshold": state.review_threshold,
                    "ollama": {
                        "enabled": state.ollama_enabled,
                        "url": state.ollama_url,
                        "model": state.ollama_model,
                        "mode": state.ollama_mode,
                        **ollama,
                    },
                })
                return
            if path == "/api/ollama/status":
                if not state.ollama_enabled:
                    self._send_json({"ok": False, "error": "disabled"})
                    return
                self._send_json(ollama_status(state.ollama_url, state.ollama_model))
                return
            if path == "/api/learned-bad-forms":
                pairs = state.learned_bad_pairs()
                self._send_json({"patterns": pairs, "count": len(pairs)})
                return
            if path == "/api/page-image":
                qs = parse_qs(parsed.query)
                try:
                    idx = int((qs.get("paragraph_index") or ["0"])[0])
                    page_q = (qs.get("page") or [""])[0]
                    page = int(page_q) if page_q else None
                    zoom = float((qs.get("zoom") or ["2"])[0])
                    png = state.render_item_image(idx, page=page, zoom=zoom)
                except (KeyError, ValueError, FileNotFoundError) as e:
                    self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                    return
                self._send_bytes(png, "image/png")
                return

            static = self._static_path(path)
            if static:
                ctype = mimetypes.guess_type(str(static))[0] or "application/octet-stream"
                self._send_bytes(static.read_bytes(), ctype)
                return

            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            body = self._read_json_body()

            try:
                if path == "/api/decisions":
                    incoming = body.get("decisions") or {}
                    paths, global_added = state.record_decision_updates(incoming)
                    self._send_json({
                        "ok": True,
                        "path": str(paths[0]) if paths else "",
                        "training_log": str(state.training_log_path or ""),
                        "training_latest": str(state.training_latest_path or ""),
                        "global_rules_learned": str(global_learned_path()),
                        "global_rules_added": global_added,
                    })
                    return
                if path == "/api/decisions/clear":
                    state.decisions = {"decisions": {}, "meta": {}}
                    saved = state.save_decisions()
                    self._send_json({"ok": True, "path": str(saved)})
                    return
                if path == "/api/rebuild":
                    queue = state.rebuild_queue()
                    meta = queue.get("meta") or {}
                    self._send_json({
                        "ok": True,
                        "meta": meta,
                        "items_count": len(queue.get("items") or []),
                        "empty_our_slices": meta.get("empty_our_slices", 0),
                        "queue_layout": meta.get("queue_layout"),
                    })
                    return
                if path == "/api/apply":
                    result = state.apply_and_refresh()
                    self._send_json({"ok": True, **result})
                    return
                if path == "/api/spellcheck":
                    text = body.get("text") or ""
                    result = state.spellcheck(str(text))
                    self._send_json(result)
                    return
                if path == "/api/load-queue":
                    qpath = Path(body.get("path", ""))
                    if not qpath.is_file():
                        raise FileNotFoundError(f"Нет файла: {qpath}")
                    state.load_queue(qpath)
                    state.load_decisions()
                    self._send_json({"ok": True, "meta": state.queue.get("meta", {})})
                    return
                if path == "/api/ollama/suggest":
                    idx = int(body.get("paragraph_index", -1))
                    page = body.get("page")
                    page_i = int(page) if page is not None and page != "" else None
                    our_text = body.get("our_text")
                    mode = body.get("mode")
                    try:
                        result = state.ollama_suggest(
                            idx,
                            our_text=our_text if isinstance(our_text, str) else None,
                            page=page_i,
                            mode=mode if isinstance(mode, str) else None,
                        )
                    except RuntimeError as e:
                        self._send_json({"error": str(e)}, HTTPStatus.BAD_GATEWAY)
                        return
                    except Exception as e:
                        self._send_json(
                            {"error": f"Ollama: {type(e).__name__}: {e}"},
                            HTTPStatus.BAD_GATEWAY,
                        )
                        return
                    self._send_json({"ok": True, **result})
                    return
            except (ValueError, FileNotFoundError, KeyError, json.JSONDecodeError) as e:
                self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
                return
            except RuntimeError as e:
                self._send_json({"error": str(e)}, HTTPStatus.BAD_GATEWAY)
                return

            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

    return Handler


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    pdf: Optional[Path] = None,
    workdir: Optional[Path] = None,
    queue: Optional[Path] = None,
    review_threshold: float = 0.88,
    target_orthography: str = "modern",
    open_browser: bool = True,
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_model: str = "qwen2.5:3b",
    ollama_enabled: bool = True,
    ollama_mode: str = "find_apply",
) -> None:
    if not WEB_DIR.is_dir():
        raise FileNotFoundError(f"Нет папки web: {WEB_DIR}")

    state = CorrectorState(
        pdf_path=pdf,
        workdir=workdir,
        queue_path=queue,
        review_threshold=review_threshold,
        target_orthography=target_orthography,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        ollama_enabled=ollama_enabled,
        ollama_mode=ollama_mode,
    )

    handler = make_handler(state, WEB_DIR)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"

    print(f"Корректор: {url}")
    if state.queue_path:
        print(f"Очередь: {state.queue_path}")
    if state.pdf_path:
        print(f"PDF: {state.pdf_path}")
    meta = state.queue.get("meta") or {}
    if meta:
        layout = meta.get("queue_layout") or ""
        if layout == "one_item_per_block_v3":
            print(
                f"Блоков: {meta.get('blocks_total', '?')} | "
                f"review: {meta.get('status_review', '?')} | "
                f"ok: {meta.get('status_ok', '?')} | v3 bbox-crop"
            )
        else:
            print(
                f"Абзацев: {meta.get('paragraphs_total', '?')} | "
                f"review: {meta.get('status_review', '?')} | "
                f"ok: {meta.get('status_ok', '?')}"
            )

    if state.ollama_enabled:
        st = ollama_status(state.ollama_url, state.ollama_model)
        if st.get("ok"):
            avail = "да" if st.get("model_available") else "НЕТ — ollama pull " + state.ollama_model
            print(f"Ollama: {state.ollama_url} · модель {state.ollama_model} · доступна: {avail}")
        else:
            print(f"Ollama: недоступен ({st.get('error', '?')})")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка.")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    ap = argparse.ArgumentParser(description="Веб-интерфейс корректора PDF Faithful Corrector")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--pdf", default="", help="PDF (как в CLI)")
    ap.add_argument("--workdir", default="", help="Папка out/<slug>/ (book.json + pages/)")
    ap.add_argument("--queue", default="", help="Готовый corrector_queue.json")
    ap.add_argument("--review-threshold", type=float, default=0.88)
    ap.add_argument(
        "--target-orthography",
        choices=("modern", "faithful"),
        default="modern",
    )
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--ollama-model", default="qwen2.5:3b", help="Модель Ollama (например qwen2.5:3b)")
    ap.add_argument(
        "--ollama-mode",
        choices=("find_apply", "rewrite"),
        default="find_apply",
        help="find_apply: JSON-правки (по умолчанию); rewrite: переписать абзац целиком",
    )
    ap.add_argument("--no-ollama", action="store_true", help="Отключить предложения Ollama")
    args = ap.parse_args(argv)

    pdf = Path(args.pdf) if args.pdf else None
    workdir = Path(args.workdir) if args.workdir else None
    queue = Path(args.queue) if args.queue else None

    if not queue and not workdir:
        print(
            "Укажите --workdir out/<slug> или --queue <corrector_queue.json>",
            file=sys.stderr,
        )
        return 1

    if workdir and not pdf:
        try:
            pdf = resolve_workdir_pdf(workdir)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1

    run_server(
        host=args.host,
        port=args.port,
        pdf=pdf,
        workdir=workdir,
        queue=queue,
        review_threshold=args.review_threshold,
        target_orthography=args.target_orthography,
        open_browser=not args.no_browser,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
        ollama_enabled=not args.no_ollama,
        ollama_mode=args.ollama_mode,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
