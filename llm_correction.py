"""
LLM-коррекция текста: исправление OCR-ошибок через GigaChat (Сбер).

Текст разбивается на чанки по абзацам и отправляется на коррекцию.
LLM исправляет OCR-ошибки, сохраняя стиль и содержание текста.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from html import escape as hesc
from typing import Optional

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

# Настройки GigaChat
GIGACHAT_ENV_KEY = "GIGACHAT_CREDENTIALS"
GIGACHAT_DEFAULT_MODEL = "GigaChat"


def chunks_by_paragraphs(text: str, max_len: int = 3000) -> list[str]:
    """Разбить текст на чанки по абзацам, не превышая max_len символов."""
    paras = text.split("\n\n")
    out: list[str] = []
    buf: list[str] = []
    cur = 0
    for p in paras:
        piece = p + "\n\n"
        if cur + len(piece) > max_len and buf:
            out.append("".join(buf))
            buf, cur = [], 0
        buf.append(piece)
        cur += len(piece)
    if buf:
        out.append("".join(buf))
    return out


# ---------------------------------------------------------------------------
# GigaChat (Сбер)
# ---------------------------------------------------------------------------

def correct_gigachat(
    text: str,
    model: str = "GigaChat",
    credentials: Optional[str] = None,
    chunk_size: int = 3000,
    sleep: float = 0.3,
) -> tuple[str, dict]:
    """Коррекция через GigaChat API (Сбер)."""
    try:
        from gigachat import GigaChat
        from gigachat.models import Chat, Messages, MessagesRole
    except ImportError:
        print("Ошибка: gigachat не установлен. Установите: pip install gigachat")
        return text, {"error": "gigachat not installed"}

    if not credentials:
        print("Ошибка: не указан GIGACHAT_CREDENTIALS (env или --api-key)")
        print("  Получите ключ на https://developers.sber.ru/studio/")
        return text, {"error": "no credentials"}

    chunks = chunks_by_paragraphs(text, max_len=chunk_size)
    fixed_chunks: list[str] = []
    total_in = 0
    total_out = 0

    max_retries = 4
    RETRYABLE_CODES = {"429", "502", "503", "504"}

    with GigaChat(
        credentials=credentials,
        model=model,
        verify_ssl_certs=False,
        timeout=60.0,
    ) as client:
        for i, chunk in enumerate(chunks, 1):
            print(f"  LLM чанк {i}/{len(chunks)} ({len(chunk)} симв.)...",
                  end=" ", flush=True)
            success = False
            for attempt in range(max_retries):
                try:
                    chat = Chat(
                        messages=[
                            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
                            Messages(role=MessagesRole.USER, content=chunk),
                        ],
                        temperature=0.15,
                        max_tokens=len(chunk) + 500,
                    )
                    resp = client.chat(chat)
                    result = resp.choices[0].message.content if resp.choices else chunk
                    in_tok = resp.usage.prompt_tokens if resp.usage else 0
                    out_tok = resp.usage.completion_tokens if resp.usage else 0
                    total_in += in_tok
                    total_out += out_tok
                    print(f"OK ({in_tok}+{out_tok} tok)")
                    fixed_chunks.append(result)
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

    stats = {
        "provider": "gigachat",
        "model": model,
        "chunks": len(chunks),
        "input_tokens": total_in,
        "output_tokens": total_out,
    }
    return "".join(fixed_chunks), stats


# ---------------------------------------------------------------------------
# Общий интерфейс
# ---------------------------------------------------------------------------

def correct_text(
    text: str,
    model: str = "",
    api_key: Optional[str] = None,
    chunk_size: int = 3000,
    sleep: float = 0.3,
) -> tuple[str, dict]:
    """
    Коррекция текста через GigaChat.

    Args:
        text: Исходный текст
        model: Название модели (по умолчанию: GigaChat)
        api_key: GIGACHAT_CREDENTIALS (если пусто — берётся из env)
        chunk_size: Размер чанка в символах
        sleep: Пауза между запросами

    Returns:
        (исправленный текст, статистика)
    """
    creds = api_key or os.environ.get(GIGACHAT_ENV_KEY, "")
    m = model or GIGACHAT_DEFAULT_MODEL
    return correct_gigachat(
        text, model=m, credentials=creds, chunk_size=chunk_size, sleep=sleep,
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
    args = ap.parse_args()

    if not args.inp:
        ap.error("аргумент --in обязателен")
    inp = Path(args.inp)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    text = inp.read_text(encoding="utf-8", errors="replace")
    print(f"Входной текст: {len(text)} символов")
    print(f"Провайдер: GigaChat")

    fixed_text, stats = correct_text(
        text,
        model=args.model,
        api_key=args.api_key,
        chunk_size=args.chunk_size,
        sleep=args.sleep,
    )

    out_txt = outdir / "final_llm.txt"
    out_html = outdir / "final_llm.html"
    out_txt.write_text(fixed_text, encoding="utf-8")
    out_html.write_text(to_html(fixed_text, args.title), encoding="utf-8")

    print(f"Сохранено: {out_txt}, {out_html}")
    if "error" not in stats:
        print(
            f"Статистика: GigaChat/{stats['model']}, "
            f"{stats['chunks']} чанков, "
            f"{stats['input_tokens']} input + {stats['output_tokens']} output токенов"
        )


if __name__ == "__main__":
    main()
