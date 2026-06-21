"""
LLM-коррекция текста: исправление OCR-ошибок через GigaChat (Сбер).

Текст разбивается на чанки по абзацам и отправляется на коррекцию.
LLM исправляет OCR-ошибки, сохраняя стиль и содержание текста.
"""

import argparse
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from html import escape as hesc
from typing import Optional
import json

# Принудительно UTF-8 на Windows (до любых импортов сетевых библиотек)
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Загружаем .env файл (ищем рядом со скриптом)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass


SYSTEM_PROMPT = (
    "Ты — корректор текста. Тебе дан фрагмент русского текста из книги, "
    "распознанный OCR из PDF (возможно, дореволюционного издания). "
    "В тексте могут быть ошибки распознавания: пропущенные или лишние буквы, "
    "замена похожих букв (н↔п, м↔ш, о↔с и т.д.), разорванные слова, "
    "неверная пунктуация.\n\n"
    "Правила:\n"
    "1. Исправляй ТОЛЬКО явные OCR-ошибки.\n"
    "2. НЕ меняй стиль, лексику, порядок слов.\n"
    "3. НЕ добавляй и НЕ удаляй предложения.\n"
    "4. Сохраняй все абзацы (двойные переносы строк).\n"
    "5. Сохраняй регистр букв.\n"
    "6. Если слово выглядит необычно, но корректно (имя, архаизм, термин) — "
    "не трогай его.\n"
    "7. Верни ТОЛЬКО исправленный текст, без комментариев и пояснений."
)

# Промпт для осторожного режима: не менять сомнительные слова, а выносить в список
SYSTEM_PROMPT_CAUTIOUS = (
    "Ты — корректор текста. Тебе дан фрагмент русского текста из книги, "
    "распознанный OCR из PDF (возможно, дореволюционного издания). "
    "В тексте могут быть ошибки распознавания: пропущенные или лишние буквы, "
    "замена похожих букв (н↔п, м↔ш, о↔с и т.д.), разорванные слова, "
    "неверная пунктуация.\n\n"
    "Правила:\n"
    "1. Исправляй ТОЛЬКО те OCR-ошибки, в которых ты уверен на 100%.\n"
    "2. Если слово выглядит подозрительно, но ты НЕ уверен в правильном варианте — "
    "НЕ МЕНЯЙ его в тексте, оставь как есть.\n"
    "3. НЕ меняй стиль, лексику, порядок слов.\n"
    "4. НЕ добавляй и НЕ удаляй предложения.\n"
    "5. Сохраняй все абзацы (двойные переносы строк).\n"
    "6. Сохраняй регистр букв.\n"
    "7. Если слово выглядит необычно, но корректно (имя, архаизм, термин) — "
    "не трогай его.\n\n"
    "Формат ответа:\n"
    "Сначала выведи исправленный текст.\n"
    "Затем, если есть сомнительные слова, добавь строку-разделитель:\n"
    "===СОМНИТЕЛЬНЫЕ===\n"
    "И ниже — список сомнительных слов, по одному на строку, в формате:\n"
    "слово — причина сомнения\n\n"
    "Пример:\n"
    "===СОМНИТЕЛЬНЫЕ===\n"
    "привзтствовал — возможно, «приветствовал» (замена е→з)\n"
    "камнатъ — возможно, «комнат» (о→а, лишний ъ)\n\n"
    "Если сомнительных слов нет — не добавляй разделитель, верни только текст."
)

SYSTEM_PROMPT_OLD_RUSSIAN = (
    "Ты — корректор текста, специализирующийся на древнерусских и старорусских текстах.\n\n"
    "Тебе дан фрагмент из книги, распознанный OCR из скана дореволюционного издания.\n\n"
    "ГЛАВНАЯ ПРОБЛЕМА: OCR вставил лишние пробелы внутри слов. Почти каждое слово "
    "разорвано на слоги или отдельные буквы. Твоя главная задача — склеить "
    "разорванные слова обратно, восстановив нормальный текст.\n\n"
    "Примеры исправлений:\n"
    '- "Аф анас ий Ни к и т и н" → "Афанасий Никитин"\n'
    '- "п у т е ш е с т вия" → "путешествия"\n'
    '- "Ка ра м зи н а" → "Карамзина"\n'
    '- "Бе с е рм е н ьс ко й зе м л е" → "Бесерменьской земле"\n'
    '- "о т п ра ви л с я и з Тве ри" → "отправился из Твери"\n'
    '- "г о ро д о в" → "городов"\n'
    '- "Со ф ий с к о м у вре м е н н и к у" → "Софийскому временнику"\n\n'
    "Правила:\n"
    "1. СКЛЕИВАЙ разорванные слова — это главная задача. Любые последовательности "
    "коротких фрагментов (1-3 буквы), разделённых пробелами, почти наверняка являются "
    "частями одного слова.\n"
    "2. Текст содержит древнерусскую лексику (есмя, поидох, град, велми, инде, зане, "
    "токмо, аще, паки, понеже) — НЕ модернизируй её, сохраняй как есть.\n"
    "3. Тюркские/персидские/арабские вставки (олло, аллах, керим, рагим, секишь, "
    "оллоперводигер и подобные) — склеивай разорванные, но не пытайся «исправить» "
    "их на русский.\n"
    "4. Номера страниц (одиночные числа типа «24», «172», «173»), колонтитулы "
    "(«Т. II. К. 8.», «ПУТЕШЕСТВИЕ», «ТВЕРСКАГО КУПЦА АФАНАСИЯ НИКИТИНА В ИНДИЮ 173») "
    "— удаляй.\n"
    "5. Сноски (числа со скобкой или звёздочкой, например «*9», «18», «23») — сохраняй.\n"
    "6. Также исправляй явные OCR-ошибки: замену похожих букв (н↔п, м↔ш, о↔с, "
    "л↔п, ь↔ъ и т.д.), пропущенные или лишние буквы.\n"
    "7. НЕ добавляй и НЕ удаляй предложения. Сохраняй абзацы.\n"
    "8. НЕ меняй стиль, лексику, порядок слов.\n"
    "9. Верни ТОЛЬКО исправленный текст, без комментариев и пояснений."
)

SYSTEM_PROMPT_OLD_RUSSIAN_CAUTIOUS = (
    "Ты — корректор текста, специализирующийся на древнерусских и старорусских текстах.\n\n"
    "Тебе дан фрагмент из книги, распознанный OCR из скана дореволюционного издания.\n\n"
    "ГЛАВНАЯ ПРОБЛЕМА: OCR вставил лишние пробелы внутри слов. Почти каждое слово "
    "разорвано на слоги или отдельные буквы. Твоя главная задача — склеить "
    "разорванные слова обратно, восстановив нормальный текст.\n\n"
    "Примеры исправлений:\n"
    '- "Аф анас ий Ни к и т и н" → "Афанасий Никитин"\n'
    '- "п у т е ш е с т вия" → "путешествия"\n'
    '- "Ка ра м зи н а" → "Карамзина"\n'
    '- "Бе с е рм е н ьс ко й зе м л е" → "Бесерменьской земле"\n'
    '- "о т п ра ви л с я и з Тве ри" → "отправился из Твери"\n\n'
    "Правила:\n"
    "1. СКЛЕИВАЙ разорванные слова — это главная задача.\n"
    "2. Текст содержит древнерусскую лексику — НЕ модернизируй её.\n"
    "3. Тюркские/персидские вставки — склеивай разорванные, но не «исправляй».\n"
    "4. Номера страниц и колонтитулы — удаляй.\n"
    "5. Исправляй ТОЛЬКО те OCR-ошибки, в которых ты уверен на 100%.\n"
    "6. Если слово выглядит подозрительно, но ты НЕ уверен — оставь как есть.\n"
    "7. НЕ добавляй и НЕ удаляй предложения. Сохраняй абзацы.\n\n"
    "Формат ответа:\n"
    "Сначала выведи исправленный текст.\n"
    "Затем, если есть сомнительные слова, добавь строку-разделитель:\n"
    "===СОМНИТЕЛЬНЫЕ===\n"
    "И ниже — список сомнительных слов, по одному на строку, в формате:\n"
    "слово — причина сомнения\n\n"
    "Если сомнительных слов нет — не добавляй разделитель, верни только текст."
)

DOUBT_SEPARATOR = "===СОМНИТЕЛЬНЫЕ==="

# Настройки GigaChat
GIGACHAT_ENV_KEY = "GIGACHAT_CREDENTIALS"
GIGACHAT_DEFAULT_MODEL = "GigaChat"

_RU_WORD_RE = re.compile(r"[А-Яа-яЁё]{3,}")
_RU_STOPWORDS = {
    "и", "в", "во", "на", "к", "ко", "о", "об", "от", "до", "за", "из", "у", "по",
    "с", "со", "для", "без", "при", "над", "под", "про", "через", "как", "что",
    "это", "то", "не", "ни", "но", "а", "или", "ли", "же", "бы", "б", "вот",
    "там", "тут", "здесь", "где", "когда", "потом", "тогда", "теперь",
    "он", "она", "оно", "они", "мы", "вы", "ты", "я", "его", "ее", "её", "их",
    "меня", "тебя", "себя", "вам", "нам", "ему", "ей", "им",
    "этот", "эта", "эти", "тот", "та", "те", "все", "всё", "вся", "весь",
    "быть", "есть", "был", "была", "были", "будет", "будут",
}


@dataclass(frozen=True)
class Chunk:
    text: str
    para_ids: list[int]


def _split_paragraphs(text: str) -> list[str]:
    return text.split("\n\n")


def build_chunks_by_paragraphs(text: str, max_len: int = 3000) -> list[Chunk]:
    """Разбить текст на чанки по абзацам, не превышая max_len символов.

    Возвращает чанки вместе с индексами абзацев (для retrieval “памяти книги”).
    """
    paras = _split_paragraphs(text)
    chunks: list[Chunk] = []
    buf_ids: list[int] = []
    cur_len = 0

    def flush():
        nonlocal buf_ids, cur_len
        if not buf_ids:
            return
        chunk_text = "\n\n".join(paras[i] for i in buf_ids)
        chunks.append(Chunk(text=chunk_text, para_ids=list(buf_ids)))
        buf_ids = []
        cur_len = 0

    for idx, p in enumerate(paras):
        sep = 2 if buf_ids else 0  # "\n\n" между абзацами
        add_len = sep + len(p)
        if buf_ids and cur_len + add_len > max_len:
            flush()
            sep = 0
            add_len = len(p)
        buf_ids.append(idx)
        cur_len += add_len

        # Если один абзац сам по себе больше max_len — оставляем как отдельный чанк.
        if len(p) > max_len and len(buf_ids) == 1:
            flush()

    flush()
    return chunks


def _tail_context(prev_text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or not prev_text:
        return ""
    tail = prev_text[-overlap_chars:]
    cut = tail.find("\n\n")
    if 0 <= cut < len(tail) - 20:
        tail = tail[cut + 2 :]
    return tail.strip()


def _extract_query_terms(text: str, max_terms: int = 12) -> list[str]:
    words = [w.lower() for w in _RU_WORD_RE.findall(text)]
    freq: dict[str, int] = {}
    for w in words:
        if w in _RU_STOPWORDS:
            continue
        if len(w) < 4:
            continue
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    return [w for w, _ in ranked[:max_terms]]


class _BookMemory:
    """Лёгкий retrieval по всей книге через SQLite FTS5 (без внешних зависимостей)."""

    def __init__(self, paragraphs: list[str]):
        self.paragraphs = paragraphs
        self.conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE paras USING fts5(content, pid UNINDEXED)")
            conn.executemany(
                "INSERT INTO paras(content, pid) VALUES(?, ?)",
                ((p, str(i)) for i, p in enumerate(paragraphs) if p.strip()),
            )
            self.conn = conn
        except Exception:
            self.conn = None

    def is_available(self) -> bool:
        return self.conn is not None

    def search_similar(
        self,
        text: str,
        topk: int = 3,
        exclude_pids: set[int] | None = None,
        exclude_window: int = 20,
        max_chars: int = 1200,
    ) -> str:
        if not self.conn or topk <= 0:
            return ""
        terms = _extract_query_terms(text)
        if not terms:
            return ""

        query = " ".join(terms)
        exclude_pids = exclude_pids or set()

        want = max(topk * 6, 10)
        try:
            rows = self.conn.execute(
                "SELECT pid, content, bm25(paras) AS score "
                "FROM paras WHERE paras MATCH ? "
                "ORDER BY score LIMIT ?",
                (query, want),
            ).fetchall()
        except Exception:
            try:
                rows = self.conn.execute(
                    "SELECT pid, content FROM paras WHERE paras MATCH ? LIMIT ?",
                    (query, want),
                ).fetchall()
            except Exception:
                return ""

        if exclude_pids:
            lo = min(exclude_pids) - exclude_window
            hi = max(exclude_pids) + exclude_window
        else:
            lo, hi = 10**9, -10**9

        snippets: list[str] = []
        used = 0
        for row in rows:
            pid = int(row[0])
            if pid in exclude_pids:
                continue
            if lo <= pid <= hi:
                continue
            p = (row[1] or "").strip()
            if not p:
                continue
            if len(p) > 450:
                p = p[:450].rstrip() + "…"
            piece = f"[{pid}] {p}"
            if used + len(piece) + 1 > max_chars:
                break
            snippets.append(piece)
            used += len(piece) + 1
            if len(snippets) >= topk:
                break

        return "\n".join(snippets).strip()


def chunks_by_paragraphs(text: str, max_len: int = 3000) -> list[str]:
    """Разбить текст на чанки по абзацам, не превышая max_len символов."""
    return [c.text for c in build_chunks_by_paragraphs(text, max_len=max_len)]


# ---------------------------------------------------------------------------
# GigaChat (Сбер)
# ---------------------------------------------------------------------------

def _parse_cautious_response(response: str) -> tuple[str, list[str]]:
    """Разбирает ответ модели в осторожном режиме: текст + список сомнений.
    
    Returns:
        (исправленный_текст, список_сомнительных_слов)
    """
    if DOUBT_SEPARATOR in response:
        parts = response.split(DOUBT_SEPARATOR, 1)
        clean_text = parts[0].rstrip()
        doubt_lines = [
            line.strip() for line in parts[1].strip().splitlines()
            if line.strip()
        ]
        return clean_text, doubt_lines
    return response, []


def correct_gigachat(
    text: str,
    model: str = "GigaChat",
    credentials: Optional[str] = None,
    chunk_size: int = 3000,
    sleep: float = 0.3,
    cautious: bool = False,
    old_russian: bool = False,
    user_context: str = "",
    overlap_chars: int = 0,
    overlap_paragraphs: int = 0,
    use_book_memory: bool = False,
    memory_topk: int = 3,
    memory_exclude_window: int = 20,
    memory_max_chars: int = 1200,
) -> tuple[str, dict, list[str]]:
    """Коррекция через GigaChat API (Сбер).
    
    Args:
        cautious: если True, модель не меняет сомнительные слова,
                  а выносит их в отдельный список.
        old_russian: если True, используется специализированный промпт
                     для старорусских/древнерусских текстов со сканов
                     дореволюционных изданий. Фокус на склейке
                     разорванных пробелами слов.
        user_context: дополнительный текст-контекст, который будет добавлен
                      перед каждым чанком в пользовательском сообщении.
    
    Returns:
        (исправленный_текст, статистика, список_сомнений)
    """
    try:
        from gigachat import GigaChat
        from gigachat.models import Chat, Messages, MessagesRole
    except ImportError:
        print("Ошибка: gigachat не установлен. Установите: pip install gigachat")
        return text, {"error": "gigachat not installed"}, []

    if not credentials:
        print("Ошибка: не указан GIGACHAT_CREDENTIALS (env или --api-key)")
        print("  Получите ключ на https://developers.sber.ru/studio/")
        return text, {"error": "no credentials"}, []

    # Выбор промпта в зависимости от режима
    if old_russian:
        prompt = SYSTEM_PROMPT_OLD_RUSSIAN_CAUTIOUS if cautious else SYSTEM_PROMPT_OLD_RUSSIAN
        temperature = 0.05
        print("  Режим: старорусский текст (агрессивная склейка разорванных слов)")
    else:
        prompt = SYSTEM_PROMPT_CAUTIOUS if cautious else SYSTEM_PROMPT
        temperature = 0.15
    if cautious:
        print("  Режим: осторожный (сомнительные слова не меняются, выносятся в список)")

    chunks = build_chunks_by_paragraphs(text, max_len=chunk_size)
    fixed_chunks: list[str] = []
    all_doubts: list[str] = []
    total_in = 0
    total_out = 0

    max_retries = 4
    RETRYABLE_CODES = {"429", "502", "503", "504"}

    # В осторожном режиме нужно больше токенов для списка сомнений
    extra_tokens_cautious = 300

    paragraphs = _split_paragraphs(text)
    book_mem = _BookMemory(paragraphs) if use_book_memory else None
    if use_book_memory and (not book_mem or not book_mem.is_available()):
        print("  Память книги: недоступна (SQLite без FTS5), продолжаю без retrieval.")
        book_mem = None
    elif book_mem and book_mem.is_available():
        print(
            f"  Память книги: включена (topk={memory_topk}, окно исключения={memory_exclude_window})"
        )

    with GigaChat(
        credentials=credentials,
        model=model,
        verify_ssl_certs=False,
        timeout=60.0,
    ) as client:
        prev_chunk_text = ""
        for i, ch in enumerate(chunks, 1):
            chunk = ch.text
            print(f"  LLM чанк {i}/{len(chunks)} ({len(chunk)} симв.)...",
                  end=" ", flush=True)
            success = False

            ctx_parts: list[str] = []

            # 1) Нахлёст: read-only хвост предыдущего чанка
            tail = ""
            if overlap_paragraphs > 0 and prev_chunk_text:
                prev_paras = _split_paragraphs(prev_chunk_text)
                tail = "\n\n".join(prev_paras[-overlap_paragraphs:]).strip()
            elif overlap_chars > 0 and prev_chunk_text:
                tail = _tail_context(prev_chunk_text, overlap_chars=overlap_chars)
            if tail:
                ctx_parts.append(
                    "КОНТЕКСТ (только для понимания; НЕ исправляй и НЕ возвращай его):\n"
                    + tail
                )

            # 2) Память книги: похожие места для согласованности имён/терминов
            if book_mem and memory_topk > 0:
                similar = book_mem.search_similar(
                    chunk,
                    topk=memory_topk,
                    exclude_pids=set(ch.para_ids),
                    exclude_window=memory_exclude_window,
                    max_chars=memory_max_chars,
                )
                if similar:
                    ctx_parts.append(
                        "ПОХОЖИЕ МЕСТА ИЗ ЭТОЙ ЖЕ КНИГИ (для согласованности имён/терминов; "
                        "не копируй дословно, используй только чтобы выбрать правильное написание):\n"
                        + similar
                    )

            ctx_block = ("\n\n".join(ctx_parts).strip() + "\n\n") if ctx_parts else ""
            prefix = (user_context.rstrip() + "\n\n") if user_context else ""
            user_msg = (
                prefix
                + ctx_block
                + "MAIN (исправь ТОЛЬКО этот фрагмент; верни ТОЛЬКО исправленный MAIN, "
                "без заголовков и пояснений):\n"
                + chunk
            )

            # max_tokens: множитель 1.2 от длины чанка (план рекомендует
            # для old-russian, но применяем ко всем режимам для надёжности).
            # В cautious-режиме добавляем запас для списка сомнений.
            chunk_max_tokens = int(len(chunk) * 1.2)
            if cautious:
                chunk_max_tokens += extra_tokens_cautious

            for attempt in range(max_retries):
                try:
                    chat = Chat(
                        messages=[
                            Messages(role=MessagesRole.SYSTEM, content=prompt),
                            Messages(role=MessagesRole.USER, content=user_msg),
                        ],
                        temperature=temperature,
                        max_tokens=chunk_max_tokens,
                    )
                    resp = client.chat(chat)
                    result = resp.choices[0].message.content if resp.choices else chunk
                    in_tok = resp.usage.prompt_tokens if resp.usage else 0
                    out_tok = resp.usage.completion_tokens if resp.usage else 0
                    total_in += in_tok
                    total_out += out_tok

                    # В осторожном режиме разбираем ответ
                    if cautious:
                        clean_text, doubts = _parse_cautious_response(result)
                        fixed_chunks.append(clean_text)
                        if doubts:
                            all_doubts.extend(
                                f"[чанк {i}] {d}" for d in doubts
                            )
                            print(f"OK ({in_tok}+{out_tok} tok, {len(doubts)} сомнит.)")
                        else:
                            print(f"OK ({in_tok}+{out_tok} tok)")
                    else:
                        fixed_chunks.append(result)
                        print(f"OK ({in_tok}+{out_tok} tok)")

                    success = True
                    break
                except Exception as exc:
                    err_str = str(exc)
                    is_retryable = any(code in err_str for code in RETRYABLE_CODES)
                    if is_retryable and attempt < max_retries - 1:
                        wait = (attempt + 1) * 10
                        print(f"ошибка сервера, жду {wait}с...", end=" ", flush=True)
                        time.sleep(wait)
                    else:
                        print(f"Ошибка: {exc}")
                        fixed_chunks.append(chunk)
                        break

            if i < len(chunks):
                time.sleep(sleep)
            prev_chunk_text = chunk

    stats = {
        "provider": "gigachat",
        "model": model,
        "chunks": len(chunks),
        "input_tokens": total_in,
        "output_tokens": total_out,
    }
    return "".join(fixed_chunks), stats, all_doubts


# ---------------------------------------------------------------------------
# Общий интерфейс
# ---------------------------------------------------------------------------

def correct_text(
    text: str,
    model: str = "",
    api_key: Optional[str] = None,
    chunk_size: int = 3000,
    sleep: float = 0.3,
    cautious: bool = False,
    old_russian: bool = False,
    user_context: str = "",
    overlap_chars: int = 0,
    overlap_paragraphs: int = 0,
    use_book_memory: bool = False,
    memory_topk: int = 3,
    memory_exclude_window: int = 20,
    memory_max_chars: int = 1200,
) -> tuple[str, dict, list[str]]:
    """
    Коррекция текста через GigaChat.

    Args:
        text: Исходный текст
        model: Название модели (по умолчанию: GigaChat)
        api_key: GIGACHAT_CREDENTIALS (если пусто — берётся из env)
        chunk_size: Размер чанка в символах
        sleep: Пауза между запросами
        cautious: осторожный режим — не менять сомнительные слова,
                  а выносить их в отдельный список
        old_russian: режим для старорусских текстов (склейка разорванных слов)
        user_context: дополнительный текст-контекст перед каждым чанком

    Returns:
        (исправленный текст, статистика, список сомнительных слов)
    """
    creds = api_key or os.environ.get(GIGACHAT_ENV_KEY, "")
    m = model or GIGACHAT_DEFAULT_MODEL
    return correct_gigachat(
        text, model=m, credentials=creds, chunk_size=chunk_size, sleep=sleep,
        cautious=cautious, old_russian=old_russian, user_context=user_context,
        overlap_chars=overlap_chars,
        overlap_paragraphs=overlap_paragraphs,
        use_book_memory=use_book_memory,
        memory_topk=memory_topk,
        memory_exclude_window=memory_exclude_window,
        memory_max_chars=memory_max_chars,
    )


def to_html(text: str, title: str) -> str:
    """Простой HTML для просмотра."""
    return (
        '<!doctype html>\n<html lang="ru">\n<head>\n'
        '<meta charset="utf-8"/>\n'
        f"<title>{hesc(title)}</title>\n"
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>\n'
        '<style>body{font:18px/1.6 Georgia,Times,"Times New Roman",serif;'
        "margin:2rem;max-width:48rem;color:#111;background:#fff} "
        "pre{white-space:pre-wrap}</style>\n"
        "</head>\n<body>\n"
        '<pre contenteditable="true" spellcheck="true">'
        + hesc(text)
        + "</pre>\n</body>\n</html>\n"
    )


def main():
    ap = argparse.ArgumentParser(
        description="LLM-коррекция OCR-текста через GigaChat (Сбер)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:

  python llm_correction.py --in out/final.txt
  python llm_correction.py --in out/final.txt --chunk-size 6000
  python llm_correction.py --in out/final.txt --model GigaChat-Pro

Настройка:
  Установите GIGACHAT_CREDENTIALS в файле .env или через переменную окружения.
  Получите ключ на https://developers.sber.ru/studio/
        """,
    )
    ap.add_argument("--in", dest="inp", default="", help="Входной TXT файл")
    ap.add_argument("--outdir", default="out", help="Папка вывода")
    ap.add_argument("--title", default="Документ (LLM)", help="Заголовок HTML")
    ap.add_argument(
        "--model", default="",
        help="Модель GigaChat (по умолчанию: GigaChat)",
    )
    ap.add_argument(
        "--api-key", default="",
        help="GIGACHAT_CREDENTIALS (или env: GIGACHAT_CREDENTIALS)",
    )
    ap.add_argument(
        "--chunk-size", type=int, default=3000,
        help="Размер чанка (символы, по умолчанию: 3000)",
    )
    ap.add_argument(
        "--sleep", type=float, default=0.5,
        help="Пауза между запросами (сек, по умолчанию: 0.5)",
    )
    ap.add_argument(
        "--cautious", action="store_true",
        help="Осторожный режим: не менять сомнительные слова, "
             "а выносить их в отдельный файл doubt_words.txt",
    )
    ap.add_argument(
        "--old-russian", action="store_true",
        help="Режим для старорусских/древнерусских текстов из дореволюционных "
             "сканов. Фокус на склейке разорванных пробелами слов, сохранении "
             "архаичной лексики, обработке тюркских/персидских вставок.",
    )
    ap.add_argument(
        "--user-context", default="",
        help="Дополнительный контекст перед каждым чанком "
             '(напр. "Текст из «Хождения за три моря» Афанасия Никитина")',
    )
    ap.add_argument(
        "--overlap-chars", type=int, default=0,
        help="Нахлёст: сколько символов брать хвостом из предыдущего чанка "
             "как read-only контекст (по умолчанию: 0)",
    )
    ap.add_argument(
        "--overlap-paragraphs", type=int, default=0,
        help="Нахлёст по абзацам (предпочтительнее символов): сколько последних "
             "абзацев предыдущего чанка добавлять как read-only контекст "
             "(по умолчанию: 0)",
    )
    ap.add_argument(
        "--book-memory", action="store_true",
        help="Включить “память книги”: подтягивать похожие места из других частей текста "
             "для согласованности имён/терминов (SQLite FTS5, без внешних зависимостей)",
    )
    ap.add_argument(
        "--memory-topk", type=int, default=3,
        help="Сколько похожих фрагментов добавлять из памяти книги (по умолчанию: 3)",
    )
    ap.add_argument(
        "--memory-exclude-window", type=int, default=20,
        help="Исключать абзацы рядом с текущим чанком (по обе стороны, по умолчанию: 20)",
    )
    ap.add_argument(
        "--memory-max-chars", type=int, default=1200,
        help="Ограничение на размер retrieval-контекста (символы, по умолчанию: 1200)",
    )
    ap.add_argument(
        "--stats-json", default="",
        help="Если указан, сохранить статистику LLM-прогона в JSON",
    )
    args = ap.parse_args()

    if not args.inp:
        ap.error("аргумент --in обязателен")
    inp = Path(args.inp)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    text = inp.read_text(encoding="utf-8", errors="replace")
    print(f"Входной текст: {len(text)} символов")
    print(f"Провайдер: GigaChat")

    fixed_text, stats, doubts = correct_text(
        text,
        model=args.model,
        api_key=args.api_key,
        chunk_size=args.chunk_size,
        sleep=args.sleep,
        cautious=args.cautious,
        old_russian=args.old_russian,
        user_context=args.user_context,
        overlap_chars=args.overlap_chars,
        overlap_paragraphs=args.overlap_paragraphs,
        use_book_memory=args.book_memory,
        memory_topk=args.memory_topk,
        memory_exclude_window=args.memory_exclude_window,
        memory_max_chars=args.memory_max_chars,
    )

    out_txt = outdir / "final_llm.txt"
    out_html = outdir / "final_llm.html"
    out_txt.write_text(fixed_text, encoding="utf-8")
    out_html.write_text(to_html(fixed_text, args.title), encoding="utf-8")

    print(f"Сохранено: {out_txt}, {out_html}")

    # Сохраняем список сомнительных слов
    if doubts:
        doubt_file = outdir / "doubt_words.txt"
        doubt_content = (
            "Сомнительные слова (модель не уверена в правильности)\n"
            "=" * 60 + "\n\n"
            + "\n".join(doubts) + "\n"
        )
        doubt_file.write_text(doubt_content, encoding="utf-8")
        print(f"\nСомнительных слов: {len(doubts)}")
        print(f"Список сохранён: {doubt_file}")
        # Показываем первые несколько
        for d in doubts[:10]:
            print(f"  {d}")
        if len(doubts) > 10:
            print(f"  ... и ещё {len(doubts) - 10}")
    elif args.cautious:
        print("\nСомнительных слов не обнаружено")

    if "error" not in stats:
        print(
            f"Статистика: GigaChat/{stats['model']}, "
            f"{stats['chunks']} чанков, "
            f"{stats['input_tokens']} input + {stats['output_tokens']} output токенов"
        )
    if args.stats_json:
        try:
            payload = dict(stats)
            payload.update(
                {
                    "tool": "llm_correction",
                    "chunk_size_chars": args.chunk_size,
                    "overlap_chars": args.overlap_chars,
                    "overlap_paragraphs": args.overlap_paragraphs,
                    "book_memory": bool(args.book_memory),
                    "memory_topk": args.memory_topk,
                    "memory_exclude_window": args.memory_exclude_window,
                    "memory_max_chars": args.memory_max_chars,
                    "cautious": bool(args.cautious),
                    "old_russian": bool(args.old_russian),
                    "doubts_count": len(doubts),
                    "input_chars": len(text),
                    "output_chars": len(fixed_text),
                }
            )
            Path(args.stats_json).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            print(f"⚠️  Не удалось записать stats-json: {exc}")


if __name__ == "__main__":
    main()
