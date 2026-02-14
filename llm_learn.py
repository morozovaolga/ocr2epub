"""
Обучение LLM-коррекции на ручных исправлениях.

Сравнивает вывод LLM (или пайплайна) с вручную исправленным текстом,
извлекает пары «ошибка → исправление» и сохраняет в JSON.

При следующем запуске llm_correction.py эти пары используются:
  1. Как few-shot примеры в промпте (LLM учится на ваших правках)
  2. Как словарь автозамен (точные совпадения применяются автоматически)

Использование:
  # 1. Обработайте PDF пайплайном → получите out/final_llm.txt
  # 2. Вручную исправьте ошибки → сохраните как corrected.txt
  # 3. Извлеките коррекции:
  python llm_learn.py --auto out/final_llm.txt --corrected corrected.txt

  # Повторяйте для каждой книги — база коррекций растёт.
  # При следующем запуске пайплайна коррекции применяются автоматически.
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# По умолчанию файл коррекций лежит рядом со скриптом
DEFAULT_CORRECTIONS_FILE = Path(__file__).parent / "llm_corrections.json"


def load_corrections(path: Path) -> dict:
    """Загрузить файл коррекций или создать пустой."""
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"pairs": [], "stats": {"total_added": 0, "sources": []}}
    # Миграция: гарантируем наличие всех полей
    if "pairs" not in data:
        data["pairs"] = []
    if "stats" not in data:
        data["stats"] = {"total_added": 0, "sources": []}
    return data


def save_corrections(data: dict, path: Path):
    """Сохранить файл коррекций."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def tokenize(text: str) -> list[str]:
    """Разбить текст на слова, сохраняя разделители как отдельные токены."""
    return re.findall(r"\S+|\s+", text)


def extract_corrections(auto_text: str, corrected_text: str) -> list[dict]:
    """
    Извлечь пары «ошибка → исправление» из двух текстов.

    Сравнивает на уровне слов. Возвращает список словарей:
    {
      "wrong": "ошибочное слово или фраза",
      "right": "правильное слово или фраза",
      "context": "...контекст до [wrong → right] контекст после..."
    }
    """
    auto_tokens = tokenize(auto_text)
    corr_tokens = tokenize(corrected_text)

    matcher = difflib.SequenceMatcher(None, auto_tokens, corr_tokens)
    corrections = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        wrong = "".join(auto_tokens[i1:i2]).strip()
        right = "".join(corr_tokens[j1:j2]).strip()

        # Пропускаем пустые и чисто пробельные изменения
        if not wrong and not right:
            continue
        if wrong == right:
            continue

        # Контекст: 5 токенов до и после
        ctx_before = "".join(auto_tokens[max(0, i1 - 10):i1]).strip()
        ctx_after = "".join(auto_tokens[i2:i2 + 10]).strip()
        context = f"...{ctx_before} [{wrong} → {right}] {ctx_after}..."

        corrections.append({
            "wrong": wrong,
            "right": right,
            "context": context,
        })

    return corrections


def merge_corrections(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """
    Объединить существующие и новые коррекции. Дубликаты не добавляются.
    Возвращает (объединённый список, количество добавленных).
    """
    # Индекс существующих пар для быстрой проверки дубликатов
    existing_set = set()
    for pair in existing:
        key = (pair.get("wrong", "").lower(), pair.get("right", "").lower())
        existing_set.add(key)

    added = 0
    for pair in new:
        key = (pair.get("wrong", "").lower(), pair.get("right", "").lower())
        if key not in existing_set:
            existing.append(pair)
            existing_set.add(key)
            added += 1

    return existing, added


def show_corrections(corrections: list[dict], limit: int = 30):
    """Вывести коррекции в консоль."""
    for i, c in enumerate(corrections[:limit], 1):
        print(f"  {i:3d}. «{c['wrong']}» → «{c['right']}»")
        if c.get("context"):
            print(f"       {c['context'][:120]}")
    if len(corrections) > limit:
        print(f"  ... и ещё {len(corrections) - limit}")


def main():
    ap = argparse.ArgumentParser(
        description="Извлечь коррекции из ручных исправлений для обучения LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:

  # Извлечь коррекции:
  python llm_learn.py --auto out/final_llm.txt --corrected my_fixes.txt

  # Указать свой файл коррекций:
  python llm_learn.py --auto out/final_llm.txt --corrected my_fixes.txt \\
    --corrections my_corrections.json

  # Показать текущую базу коррекций:
  python llm_learn.py --show

  # Очистить базу:
  python llm_learn.py --clear
        """,
    )
    ap.add_argument("--auto", help="Текст до ручных исправлений (вывод LLM/пайплайна)")
    ap.add_argument("--corrected", help="Текст после ручных исправлений")
    ap.add_argument(
        "--corrections",
        default=str(DEFAULT_CORRECTIONS_FILE),
        help=f"Файл коррекций JSON (по умолчанию: {DEFAULT_CORRECTIONS_FILE.name})",
    )
    ap.add_argument("--show", action="store_true", help="Показать текущую базу коррекций")
    ap.add_argument("--clear", action="store_true", help="Очистить базу коррекций")
    args = ap.parse_args()

    corrections_path = Path(args.corrections)

    # Показать базу
    if args.show:
        data = load_corrections(corrections_path)
        pairs = data["pairs"]
        print(f"Файл: {corrections_path}")
        print(f"Всего коррекций: {len(pairs)}")
        if pairs:
            print()
            show_corrections(pairs, limit=50)
        return

    # Очистить базу
    if args.clear:
        save_corrections({"pairs": [], "stats": {"total_added": 0, "sources": []}}, corrections_path)
        print(f"База коррекций очищена: {corrections_path}")
        return

    # Извлечение коррекций
    if not args.auto or not args.corrected:
        ap.error("Укажите --auto и --corrected для извлечения коррекций")

    auto_path = Path(args.auto)
    corr_path = Path(args.corrected)

    if not auto_path.exists():
        print(f"Ошибка: файл не найден: {auto_path}")
        return 1
    if not corr_path.exists():
        print(f"Ошибка: файл не найден: {corr_path}")
        return 1

    auto_text = auto_path.read_text(encoding="utf-8", errors="replace")
    corr_text = corr_path.read_text(encoding="utf-8", errors="replace")

    print(f"Автоматический текст: {len(auto_text)} символов ({auto_path.name})")
    print(f"Исправленный текст:   {len(corr_text)} символов ({corr_path.name})")
    print()

    new_corrections = extract_corrections(auto_text, corr_text)
    print(f"Найдено различий: {len(new_corrections)}")

    if not new_corrections:
        print("Тексты идентичны — коррекций нет.")
        return

    # Показываем найденные коррекции
    print()
    show_corrections(new_corrections, limit=20)

    # Загружаем и мержим
    data = load_corrections(corrections_path)
    data["pairs"], added = merge_corrections(data["pairs"], new_corrections)
    data["stats"]["total_added"] = data["stats"].get("total_added", 0) + added

    source_name = f"{auto_path.name} vs {corr_path.name}"
    if source_name not in data["stats"].get("sources", []):
        data["stats"].setdefault("sources", []).append(source_name)

    save_corrections(data, corrections_path)
    print(f"\nДобавлено новых коррекций: {added} (дубликатов пропущено: {len(new_corrections) - added})")
    print(f"Всего в базе: {len(data['pairs'])} коррекций")
    print(f"Сохранено: {corrections_path}")


if __name__ == "__main__":
    main()
