OCR → EPUB (с сохранением абзацев и корректурой)

Этот инструмент обрабатывает PDF с распознанным текстом (OCR), восстанавливает структуру документа (абзацы/заголовки), применяет правила дореформенной орфографии, нормализует по современным правилам и генерирует EPUB с автоматической обложкой.

Требования
- Python 3.10+
- pip

Установка
Windows PowerShell:
```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Механизм работы
1. Извлечение структуры: PDF → JSON с блоками текста (heading/paragraph)
2. Применение правил дореформенной орфографии (oldspelling.py)
3. Модернизация: дореволюционная → современная орфография/типографика
4. Проверка орфографии: LanguageTool (облако) + Natasha (синхронизация имён)
5. Контекстная проверка: местоимение+глагол, разорванные слова
6. Генерация EPUB: разбиение на главы, создание обложки, обновление оглавления

Самый полный и точный CLI (79.61% точности)
```bash
python pdf_to_epub.py \
  --pdf path/to/file.pdf \
  --outdir out \
  --title "Название книги" \
  --author "Имя Автора" \
  --lt-cloud \
  --natasha-sync \
  --context-check \
  --epub-template sample.epub
```

Оптимальный вариант (79.61% точности, быстрее)
```bash
python pdf_to_epub.py \
  --pdf path/to/file.pdf \
  --outdir out \
  --title "Название книги" \
  --author "Имя Автора" \
  --lt-cloud \
  --natasha-sync \
  --epub-template sample.epub
```

Объяснение флагов

Обязательные:
- `--pdf PATH` — путь к PDF файлу с распознанным текстом
- `--title "Название"` — название книги для EPUB
- `--epub-template PATH` — путь к шаблону EPUB (по умолчанию: sample.epub)

Основные опции:
- `--outdir DIR` — папка для результатов (по умолчанию: out)
- `--author "Автор"` — имя автора для обложки EPUB
- `--two-columns` — PDF с двумя колонками на странице
- `--no-oldspelling` — пропустить применение правил дореформенной орфографии

Проверка орфографии (рекомендуется):
- `--lt-cloud` — LanguageTool (облачная проверка орфографии и пробелов)
- `--chunk-size N` — размер блока для LanguageTool (по умолчанию: 6000 символов)
- `--natasha-check` — проверка именованных сущностей через Natasha (PER, LOC, ORG)
- `--natasha-types TYPES` — типы сущностей (по умолчанию: PER,LOC)
- `--natasha-out FILE` — файл отчёта проверки (по умолчанию: natasha_diff.txt)
- `--natasha-sync` — синхронизация имён из PDF с обработанным текстом (+0.19% точности)
- `--natasha-sync-report FILE` — файл отчёта синхронизации (по умолчанию: natasha_sync.txt)
- `--context-check` — контекстная проверка (местоимение+глагол, разорванные слова)
- `--context-out FILE` — файл с предупреждениями (по умолчанию: context_warnings.txt)
- `--context-pronouns LIST` — местоимения для проверки (по умолчанию: он,она,оно,они,мы,вы,ты)
- `--post-clean` — пост-очистка: склейка букв через пробел, исправление разорванных слов, латиница→кириллица

Локальная проверка орфографии (не рекомендуется):
- `--local-spell` — локальная проверка через pyspellchecker/jamspell/symspell (⚠️ снижает качество до 52.4%)
- `--local-spell-type TYPE` — тип проверщика: pyspellchecker, jamspell, symspell
- `--local-spell-model PATH` — путь к модели (jamspell) или словарю (symspell)
- `--local-spell-lang LANG` — язык проверки (по умолчанию: ru)

Токенизация:
- `--stanza-tokenize` — улучшить разбиение на предложения через Stanza НКРЯ
- `--stanza-model PATH` — путь к модели Stanza (.pt файл)

EPUB:
- `--epub-max-chapter-size KB` — максимальный размер главы в KB (по умолчанию: 50)
- `--epub-use-chapter-heads` — использовать поиск заголовков для разделения на главы (по умолчанию: разделение по размеру)
- `--cover-colors COLORS` — пять HEX-цветов через запятую (полоска, верхний блок, заголовок, градиент начало, градиент конец)

Результаты тестирования
Проведено расширенное тестирование 63 комбинаций инструментов с детальными метриками (типы ошибок OCR, Precision/Recall/F1, точность имён собственных, сохранение структуры).

**Результаты:** [Интерактивный дашборд](https://morozovaolga.github.io/ocr2epub/)

**Лучшие комбинации:**
- 🏆 **Максимальное качество:** `--lt-cloud --natasha-sync` (79.61% точности, ~8.4 сек)
- ⚡ **Быстрая обработка:** `--lt-cloud` (79.42% точности, ~7 сек)
- 📊 **Базовый вариант:** только модернизация (73.90% точности, ~1 сек)

**Не рекомендуется:**
- ❌ `--local-spell` с pyspellchecker — значительно снижает качество (до 52.4%)

Что получится в папке out/
- `structured.json` — извлечённые блоки текста по страницам (heading/paragraph)
- `structured.html` / `structured.txt` — черновой вывод после структурирования
- `structured_rules.json` — после применения правил oldspelling
- `final.html` / `final.txt` — современная орфография/типографика
- `flags.json` — пометки неоднозначных замен
- `final_clean.txt` / `final_clean.html` — после LanguageTool (если `--lt-cloud`)
- `final_better.txt` / `final_better.html` — после пост-очистки (если `--post-clean`)
- `Название_книги.epub` — EPUB файл с автоматически сгенерированной обложкой (если `--epub-template`)
