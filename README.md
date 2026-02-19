# OCR2EPUB

Конвертация PDF в EPUB с коррекцией OCR-ошибок через GigaChat.

Извлекает текст из PDF, восстанавливает структуру, модернизирует дореволюционную орфографию, исправляет ошибки распознавания через GigaChat (Сбер) и генерирует EPUB с автоматической обложкой.

## Требования

- Python 3.10+
- API-ключ GigaChat: [developers.sber.ru/studio](https://developers.sber.ru/studio/)
- Java (для валидации EPUB через [epubcheck](https://pypi.org/project/epubcheck/), опционально)

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
| 1 | Извлечение структуры | `extract_structured_text.py` | PDF -> JSON с блоками текста, автоудаление номеров страниц |
| 2 | Старая орфография | `oldspelling.py` | Применение правил дореформенной орфографии |
| 3 | Токенизация | `stanza_tokenizer.py` | Улучшение разбиения на предложения (опционально) |
| 4 | Модернизация | `modernize_structured.py` | Дореволюционная -> современная орфография и типографика |
| 5 | Проверка орфографии | `lt_cloud.py` | LanguageTool (облако) + Yandex.Speller |
| 6 | LLM-коррекция | `llm_correction.py` | Исправление OCR-ошибок через GigaChat (самый мощный этап) |
| 7 | Контекстная проверка | `context_checker.py` | Проверка местоимение + глагол, разорванные слова |
| 8 | Пост-очистка | `post_cleanup.py` | Склейка букв, исправление разорванных слов (navec + pymorphy2), замена латиницы -> кириллица |
| 9 | Natasha | `natasha_sync.py` | Синхронизация имён собственных из PDF |
| 10 | Генерация EPUB | `generate_epub.py` | Разбиение на главы, обложка, оглавление, валидация epubcheck |

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
| `--keep-page-numbers` | Не удалять номера страниц (по умолчанию: удаляются) |
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
| `--llm-cautious` | Осторожный режим: не менять сомнительные слова, а вынести в `doubt_words.txt` |

### Контекстная проверка и пост-очистка

| Флаг | Описание |
|------|----------|
| `--context-check` | Контекстная проверка (местоимение + глагол) |
| `--post-clean` | Склейка букв, склейка разорванных слов (navec + pymorphy2), латиница -> кириллица |

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

## Пост-очистка: алгоритм склейки разорванных слов

Этап 8 (`post_cleanup.py`) включает продвинутый алгоритм исправления слов, разорванных OCR-движком пробелами (например, «челове к» → «человек», «ме сте» → «месте»).

### Как работает `merge_broken_words`

1. **Токенизация** — текст разбивается на кириллические слова и разделители.
2. **Попарный обход** — для каждой тройки `(левое_слово, пробел, правое_слово)` проверяется, есть ли склеенное слово в словаре navec (500K слов).
3. **Защита от ложных склеек** — если `left` является предлогом, союзом, частицей или местоимением, а `right` — самостоятельное слово (confidence > 0.3 по pymorphy2), склейка блокируется. Это предотвращает ложные результаты вида «с нами» → ~~«снами»~~, «в воде» → ~~«вводе»~~.
4. **Исключение для наречий** — если результат склейки является наречием (ADVB), склейка разрешается даже для предлогов: «в бок» → «вбок», «на верх» → «наверх».
5. **Проверка нормальных форм** — для однобуквенных предлогов (`в`, `с`, `к`) проверяется, что лемма склеенного слова = предлог + лемма правого (блокирует «с нос» → ~~«снос»~~, но пропускает «в летел» → «влетел»). Для многобуквенных — что лемма правого входит в лемму склеенного.
6. **Многопроходность** — до 5 проходов для каскадных склеек («бе дный» → «бедный»).

### Зависимости для пост-очистки

| Пакет | Назначение |
|-------|-----------|
| `navec` | Словарь из 500K русских слов — основной фильтр при склейке |
| `pymorphy2` | Морфологический анализ: POS-теги, confidence, нормальные формы |

Модель navec загружается из `~/.navec/navec_hudlit_v1_12B_500K_300d_100q.tar`. Установка:

```bash
pip install navec
python -c "from navec import Navec; Navec.load('navec_hudlit_v1_12B_500K_300d_100q.tar')"
```

Или вручную: скачайте [navec_hudlit_v1_12B_500K_300d_100q.tar](https://storage.yandexcloud.net/natasha-navec/packs/navec_hudlit_v1_12B_500K_300d_100q.tar) и положите в `~/.navec/`.

Если navec или pymorphy2 недоступны, пост-очистка всё равно работает, но без склейки разорванных слов.

## Выходные файлы (папка `out/`)

| Файл | Описание |
|------|----------|
| `structured.json` | Извлечённые блоки текста по страницам |
| `final.txt` | После модернизации орфографии |
| `final_clean.txt` | После LanguageTool + Yandex.Speller |
| `final_llm.txt` | После LLM-коррекции (GigaChat) |
| `final_better.txt` | После пост-очистки |
| `doubt_words.txt` | Сомнительные слова (при `--llm-cautious`) |
| `Название_книги.epub` | Готовый EPUB с обложкой (валидируется epubcheck) |

## Лицензия

См. файл [LICENSE](LICENSE).
