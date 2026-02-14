# OCR2EPUB

Конвертация PDF в EPUB с коррекцией OCR-ошибок через GigaChat.

Извлекает текст из PDF, восстанавливает структуру, модернизирует дореволюционную орфографию, исправляет ошибки распознавания через GigaChat (Сбер) и генерирует EPUB с автоматической обложкой.

## Требования

- Python 3.10+
- API-ключ GigaChat (бесплатно, 3 млн токенов): [developers.sber.ru/studio](https://developers.sber.ru/studio/)

## Установка

**Windows PowerShell:**

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка GigaChat

1. Зайдите на [developers.sber.ru/studio](https://developers.sber.ru/studio/)
2. Зарегистрируйтесь/войдите через Сбер ID
3. Создайте проект, получите **авторизационные данные** (длинная строка в формате base64)
4. Создайте файл `.env` в корне проекта:

```env
GIGACHAT_CREDENTIALS=ваш-ключ-сюда
```

Бесплатный тариф: **3 млн токенов** — хватит на несколько книг.

## Быстрый старт

> Перед запуском удалите папку `out/` (или укажите другую через `--outdir`), чтобы старые файлы не мешали.

**Рекомендуемый вариант (GigaChat + пост-очистка):**

```bash
python pdf_to_epub.py \
  --pdf book.pdf \
  --outdir out \
  --title "Название книги" \
  --author "Автор" \
  --llm-correct \
  --llm-chunk-size 6000 \
  --post-clean \
  --epub-template sample.epub
```

**Полный пайплайн (LanguageTool + GigaChat + пост-очистка):**

```bash
python pdf_to_epub.py \
  --pdf book.pdf \
  --outdir out \
  --title "Название книги" \
  --author "Автор" \
  --lt-cloud \
  --yandex-speller \
  --llm-correct \
  --llm-chunk-size 6000 \
  --post-clean \
  --epub-template sample.epub
```

**Без LLM (полностью без ключей):**

```bash
python pdf_to_epub.py \
  --pdf book.pdf \
  --outdir out \
  --title "Название книги" \
  --author "Автор" \
  --lt-cloud \
  --yandex-speller \
  --post-clean \
  --epub-template sample.epub
```

## Этапы пайплайна

| # | Этап | Скрипт | Описание |
|---|------|--------|----------|
| 0 | Предобработка PDF | `preprocess_pdf.py` | Выравнивание, шумодав, контраст, бинаризация (перед OCR) |
| 1 | Извлечение структуры | `extract_structured_text.py` | PDF -> JSON с блоками текста (heading / paragraph) |
| 2 | Старая орфография | `oldspelling.py` | Применение правил дореформенной орфографии |
| 3 | Токенизация | `stanza_tokenizer.py` | Улучшение разбиения на предложения (опционально) |
| 4 | Модернизация | `modernize_structured.py` | Дореволюционная -> современная орфография и типографика |
| 5 | Проверка орфографии | `lt_cloud.py` | LanguageTool (облако) + Yandex.Speller |
| 6 | LLM-коррекция | `llm_correction.py` | Исправление OCR-ошибок через GigaChat (самый мощный этап) |
| 7 | Контекстная проверка | `context_checker.py` | Проверка местоимение + глагол, разорванные слова |
| 8 | Пост-очистка | `post_cleanup.py` | Склейка букв, замена латиницы -> кириллица |
| 9 | Natasha | `natasha_sync.py` | Синхронизация имён собственных из PDF |
| 10 | Генерация EPUB | `generate_epub.py` | Разбиение на главы, обложка, оглавление |

## Флаги командной строки

### Обязательные

| Флаг | Описание |
|------|----------|
| `--pdf PATH` | Путь к PDF-файлу |
| `--title "..."` | Название книги |

### Основные

| Флаг | Описание |
|------|----------|
| `--outdir DIR` | Папка для результатов (по умолчанию: `out`) |
| `--author "..."` | Автор книги (для обложки EPUB) |
| `--two-columns` | PDF с двумя колонками |
| `--no-oldspelling` | Пропустить правила дореформенной орфографии |
| `--html` | Генерировать промежуточные HTML-файлы для ручной проверки |

### Предобработка PDF (перед OCR)

| Флаг | Описание |
|------|----------|
| `--preprocess` | Включить предобработку PDF (выравнивание, шумодав, контраст) |
| `--preprocess-preset PRESET` | Пресет: `light`, `medium` (по умолчанию), `heavy`, `binarize` |
| `--preprocess-dpi N` | DPI рендеринга (по умолчанию: 300) |
| `--preprocess-steps STEPS` | Шаги через запятую (переопределяет пресет) |
| `--preprocess-pages PAGES` | Диапазон страниц: `1-10` или `1,3,5-7` |

### Проверка орфографии

| Флаг | Описание |
|------|----------|
| `--lt-cloud` | LanguageTool — облачная проверка орфографии |
| `--yandex-speller` | Yandex.Speller — дополнительная проверка |
| `--chunk-size N` | Размер блока для LanguageTool (по умолчанию: 6000) |

### LLM-коррекция (GigaChat)

| Флаг | Описание |
|------|----------|
| `--llm-correct` | Включить коррекцию через GigaChat |
| `--llm-model MODEL` | Модель GigaChat (по умолчанию: `GigaChat`) |
| `--llm-api-key KEY` | GIGACHAT_CREDENTIALS (или через `.env`) |
| `--llm-chunk-size N` | Размер чанка (по умолчанию: 3000) |
| `--llm-corrections FILE` | Файл коррекций для обучения (по умолчанию: `llm_corrections.json`) |

### Контекстная проверка и пост-очистка

| Флаг | Описание |
|------|----------|
| `--context-check` | Контекстная проверка (местоимение + глагол) |
| `--post-clean` | Склейка букв, латиница -> кириллица, разорванные слова |

### Natasha (именованные сущности)

| Флаг | Описание |
|------|----------|
| `--natasha-check` | Проверка сущностей (PER, LOC, ORG) |
| `--natasha-sync` | Синхронизация имён из PDF с обработанным текстом |

### Токенизация и EPUB

| Флаг | Описание |
|------|----------|
| `--stanza-tokenize` | Улучшить разбиение на предложения через Stanza |
| `--epub-template PATH` | Шаблон EPUB (по умолчанию: `sample.epub`) |
| `--epub-max-chapter-size KB` | Макс. размер главы в KB (по умолчанию: 50) |
| `--epub-use-chapter-heads` | Разделять главы по найденным заголовкам |
| `--cover-colors COLORS` | Пять HEX-цветов для обложки через запятую |

Также скрипт можно использовать отдельно (для подготовки PDF перед FineReader):

```bash
python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --preset medium
python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --preset binarize --dpi 400
python preprocess_pdf.py --pdf scan.pdf --out scan_clean.pdf --steps deskew,contrast,sharpen
```

## Выходные файлы (папка `out/`)

| Файл | Описание |
|------|----------|
| `structured.json` | Извлечённые блоки текста по страницам |
| `final.txt` | После модернизации орфографии |
| `final_clean.txt` | После LanguageTool + Yandex.Speller |
| `final_llm.txt` | После LLM-коррекции (GigaChat) |
| `final_better.txt` | После пост-очистки |
| `Название_книги.epub` | Готовый EPUB с обложкой |

## Стоимость

GigaChat Lite (Сбер) — **бесплатно**, 3 млн токенов. Хватает на несколько книг по 200+ страниц.

Ключ получаете на [developers.sber.ru/studio](https://developers.sber.ru/studio/), регистрация через Сбер ID.

## Обучение на ручных исправлениях

LLM-коррекция учится на ваших правках и с каждой книгой работает точнее.

### Цикл обучения

```
1. Обработать PDF пайплайном         -> out/final_llm.txt
2. Вручную исправить оставшиеся ошибки -> corrected.txt
3. Извлечь пары "ошибка -> исправление" -> llm_corrections.json
4. При следующем запуске -- коррекции применяются автоматически
```

**Шаг 1.** Обработайте PDF:

```bash
python pdf_to_epub.py --pdf book.pdf --title "Название" \
  --llm-correct --epub-template sample.epub
```

**Шаг 2.** Откройте `out/final_llm.txt`, исправьте оставшиеся ошибки, сохраните как `corrected.txt`.

**Шаг 3.** Извлеките коррекции:

```bash
python llm_learn.py --auto out/final_llm.txt --corrected corrected.txt
```

**Шаг 4.** Проверьте базу:

```bash
python llm_learn.py --show
```

### Как это работает

При наличии файла `llm_corrections.json` в следующий запуск:

1. **Few-shot примеры** — до 15 пар коррекций добавляются в промпт GigaChat, модель видит типичные ошибки и учится их распознавать.
2. **Словарь автозамен** — однословные пары применяются автоматически после LLM (точное совпадение по границам слов).

Чем больше книг обработаете — тем точнее результат.

## Лицензия

См. файл [LICENSE](LICENSE).
