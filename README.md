# Book Page Pipeline v3

Пайплайн для книг с **дореформенной орфографией в PDF**: OCR по блокам с `bbox` → механическая коррекция → LLM → ручная проверка → TXT для дальнейшей вёрстки.

Одна книга = **одна папка** `out/<slug>/`. PDF, JSON, TXT, очередь corrector и логи — **только внутри неё**. В репозитории уже есть правила OCR, oldspelling, веб-review и CLI `bpp` — внешний `ocr2epub` не нужен.

### Все команды — один каталог, один флаг `--out`

```powershell
cd D:\ai_projects\working_projects\book_page_pipeline
$BOOK = "out\feb"

python -m bpp init --pdf "D:\...\book.pdf" --out $BOOK
python -m bpp run --out $BOOK
python -m bpp apply --out $BOOK
python -m bpp assemble --out $BOOK
python -m bpp correct --out $BOOK
python -m bpp review --out $BOOK
python -m bpp export --out $BOOK --title "…" --author "…"
```

PDF книги после `init` или `consolidate` всегда: **`out/<slug>/source.pdf`**.  
Старые workdir с PDF снаружи: `python -m bpp consolidate --out out/feb`.

Связанные проекты (исторически, для справки):

| Компонент | Где сейчас |
|-----------|------------|
| CLI `bpp`, OCR, assemble, export | `bpp/` |
| Правила OCR, oldspelling | `assets/rules/ocr/`, `assets/oldspelling.py` |
| Веб-review (crop PDF по bbox) | `pdf_faithful_corrector/` + `web/` |

---

## Схема пайплайна

```
PDF (текстовый слой / скан)
        │
        ▼
  bpp init + run          pages/page_NNNN.json  ← source of truth (блоки + bbox)
        │
        ▼
  bpp apply               text_raw → text → text_modern
        │
        ▼
  bpp assemble            book_structured.json, final_*.txt, corrector_queue.json
        │
        ├─► bpp correct   text_corrected (Ollama find_apply, опционально GigaChat)
        │
        ├─► bpp review      human review (тот же workdir, crop bbox)
        │
        └─► bpp export    book_export.txt (+ footnotes.json)
```

**Принципы v3**

1. JSON-блоки (`id`, `page`, `bbox`, `text_*`) — источник истины до export.
2. Не «сплющивать» книгу в один TXT до LLM: коррекция **по блоку**, find_apply JSON.
3. Колонтитулы фильтруются на OCR; заголовки глав — `role: heading`; сноски — `{{fn:N}}` + `footnotes[]`.
4. Двухколоночные PDF: флаг `--two-columns` (reading order слева→справа).

---

## Установка

```powershell
cd D:\ai_projects\working_projects\book_page_pipeline
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
```

Опционально — облачная LLM и Ollama:

```powershell
pip install -e ".[cloud]"
ollama pull qwen2.5:3b
```

Переменные окружения (если пути не стандартные):

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `BPP_RULES` / `OCR2EPUB_RULES` | `assets/rules/ocr` | char_confusions, regex, словари |
| `BPP_OLD_SPELLING` / `OCR2EPUB_OLD_SPELLING` | `assets/oldspelling.py` | лексикон дореформенной орфографии |
| `GIGACHAT_CREDENTIALS` | — | облачная LLM (`bpp correct --cloud`) |

---

## Быстрый старт (новая книга)

```powershell
$BOOK = "out\kniga"   # любой slug

# 1. Workdir + копия PDF
python -m bpp init `
  --pdf "D:\path\to\book.pdf" `
  --out $BOOK `
  --two-columns          # если две колонки на листе

# 2. OCR (resume: пропускает готовые pages/)
python -m bpp run --out $BOOK

# 3. Rules + oldspelling + modernize (без повторного OCR)
python -m bpp apply --out $BOOK

# 4. Сборка артеfactов + очередь corrector
python -m bpp assemble --out $BOOK

# 5. LLM (только блоки review; нужен ollama serve)
python -m bpp correct --out $BOOK

# 6. Ручная проверка — из этой же папки, без cd
python -m bpp review --out $BOOK

# 7. Финальный TXT
python -m bpp export --out $BOOK --title "Название" --author "Автор"
```

Тестовая книга **feb** уже лежит в `out/feb` (после `consolidate` PDF — `source.pdf` внутри папки).

**Старый способ** (если нужен отдельный запуск review без `bpp review`):

```powershell
python -m pdf_faithful_corrector.web_app --workdir "D:\...\book_page_pipeline\out\<slug>"
```

(из корня репозитория после `pip install -e .`)

---

## Команды CLI (`python -m bpp`)

| Команда | Описание |
|---------|----------|
| `init --pdf … --out out/<slug>` | Создать workdir, `book.json`, `source.pdf` |
| `list` | Список книг в `out/` |
| `migrate --out …` | `book.json` для старой папки без init |
| `run --out …` | OCR/extract → `pages/` (+ assemble по умолчанию) |
| `apply --out …` | Rules + oldspelling + modernize на готовых pages |
| `assemble --out …` | `book_structured.json`, TXT, `corrector_queue.json` |
| `correct --out …` | LLM find_apply по блокам (Ollama) |
| `review --out …` | Веб-корректор (pdf_faithful_corrector) для этого workdir |
| `consolidate --out …` | PDF → `source.pdf` внутри workdir, починка manifest |
| `export --out …` | `book_export.txt`, `footnotes.json` |

### Полезные флаги

**`run`**

- `--page-from N --page-to M` — диапазон листов
- `--two-columns` — двухколоночный reading order
- `--engine pymupdf|auto|tesseract` — движок OCR
- `--poetry` — не ломать переносы в стихах
- `--no-resume` / `--force` — перераспознать заново
- `--no-assemble` — не собирать после run

**`correct`**

- по умолчанию только блоки **review** (низкая similarity с PDF)
- `--all-blocks` — все блоки
- `--page-from 13 --page-to 13` — одна страница
- `--force` — перекорректировать уже готовые
- `--cloud` — после Ollama вызвать GigaChat для review
- `--ollama-model qwen2.5:3b` — модель (3B для слабого ПК)
- `--quiet` — без построчного прогресса

**`export`**

- `--title` / `--author` — метаданные в `book.json`
- `--no-refresh` — не пересобирать `book_structured.json` перед export

---

## Структура workdir `out/<slug>/`

```
out/<slug>/
  book.json                 # манифест: pdf_path = "source.pdf", flags, stages
  source.pdf                # единственный PDF книги (обязательно внутри workdir)
  manifest.json             # прогресс OCR
  pages/page_0013.json      # блоки одного листа (resume)
  book_structured.json      # вся книга, v2
  structured_rules.json     # для legacy corrector
  corrector_queue.json      # очередь v3: 1 запись = 1 блок
  corrector_decisions.json  # правки из UI
  rules_learned.jsonl       # выученные замены (LLM + UI)
  final_after_rules.txt     # после rules (дореформенный/modern mix)
  final_better.txt          # text_modern
  final_corrected.txt       # text_corrected (LLM/человек)
  book_export.txt           # копия для читателя / ручной вёрстки
  footnotes.json            # сноски (если есть)
  corrector_pages/          # PNG crop (опционально)
  logs/llm_trace.jsonl      # лог LLM
```

### `book.json` (stages)

```json
{
  "title": "feb",
  "pdf_path": "source.pdf",
  "pipeline_version": 3,
  "flags": { "two_columns": true, "engine": "pymupdf", "poetry": false },
  "stages": {
    "init": "done",
    "ocr": "done",
    "assemble": "done",
    "correct": "partial",
    "review": "pending",
    "export": "done"
  }
}
```

---

## Формат блока (страница)

```json
{
  "id": "p013_b01",
  "page": 13,
  "role": "paragraph",
  "text_raw": "…как в PDF…",
  "text": "…после rules…",
  "text_modern": "…после oldspelling/modernize…",
  "text_corrected": "…после LLM/человека…",
  "bbox": [26.4, 53.9, 183.4, 434.3],
  "wsize": 8.73,
  "line_count": 29,
  "status": "llm",
  "confidence": "review"
}
```

Роли: `paragraph`, `heading`, `verse`. Сноски: маркеры `{{fn:N}}` в тексте; тела — в `footnotes[]` на странице.

---

## OCR: что делает bpp

- Извлечение **по span-ам** PyMuPDF, union `bbox` на абзац
- **Two-columns**: левая колонка целиком, затем правая
- **Колонтитулы**: cross-page scan, margin zone → не попадают в `blocks[]`
- **Сноски**: superscript → `{{fn:N}}`, тело в `footnotes[]`
- **Rules**: char_confusions, regex, oldspelling lexicon из `assets/`
- **Resume**: готовые `pages/page_*.json` не перезаписываются без `--force`

---

## LLM-коррекция (`bpp correct`)

- Режим **find_apply**: модель возвращает JSON `{old, new}`, абзац не переписывается целиком
- Crop PDF по `bbox` → промпт + diff с эталоном
- Блоки с `similarity ≥ порога` пропускаются (режим review-only)
- Ошибки JSON у маленьких моделей → `status: llm_error` → править в UI
- Лог: `logs/llm_trace.jsonl`, правила: `rules_learned.jsonl`

На слабом ПК (i3, 16 GB, GTX 1050 2 GB): только **`qwen2.5:3b`**, `ollama serve` должен быть запущен.

---

## Ручная коррекция

```powershell
python -m bpp review --out out/<slug>
```

Открывает веб-UI с `--workdir` на эту папку. Очередь v3: crop по `bbox`, ключ `p013_b01`.

Альтернатива: `python -m pdf_faithful_corrector.web_app --workdir "…\out\<slug>"`.

Пересборка очереди: «↻ Пересобрать» в UI или `python -m bpp assemble --out out/<slug>`.

---

## Export

```powershell
python -m bpp export --out out/feb `
  --title "Маленькая принцесса" `
  --author "Ф. Г. Бернетт"
```

Создаёт:

- `book_export.txt` — из `final_corrected.txt` (или `final_better.txt`)
- `footnotes.json` — если в книге есть сноски
- обновляет `book.json` → `stages.export = done`

EPUB собирается отдельно, вручную. Для вёрстки также доступны `book_structured.json` (блоки с `role`, `page`) и `final_corrected.txt`.

---

## Типичные проблемы

| Симптом | Причина | Решение |
|---------|---------|---------|
| Текст «перемешан» на листе | одноколоночный extract для 2-col PDF | `bpp run --two-columns --force` |
| Колонтитулы в тексте | старый OCR без filter | перепрогон run + colontitles scan |
| `llm_error` / битый JSON | qwen2.5:3b на длинном блоке | правка в UI или `--cloud` |
| Ollama timeout | модель грузится долго | подождать, меньшая модель, `ollama ps` |
| bbox = 0 в очереди | legacy workdir | `bpp assemble` после v3 OCR |
| PDF снаружи workdir (feb → ocr2epub) | старый init/migrate | `python -m bpp consolidate --out out/feb` |

---

## Структура кода

```
book_page_pipeline/
  bpp/                     # CLI
  pdf_faithful_corrector/  # review backend
  web/                     # review UI
  assets/
    rules/ocr/             # char_confusions, regex, словари
    oldspelling.py
    llm_correction.py      # только bpp correct --cloud
  out/                     # книги (не коммитить)
  pyproject.toml
```

---

## Пример: полный цикл feb

```powershell
cd D:\ai_projects\working_projects\book_page_pipeline
$BOOK = "out\feb"

python -m bpp consolidate --out $BOOK   # если PDF был снаружи
python -m bpp run --out $BOOK --two-columns
python -m bpp apply --out $BOOK
python -m bpp assemble --out $BOOK
python -m bpp correct --out $BOOK --page-from 1 --page-to 20
python -m bpp review --out $BOOK
python -m bpp export --out $BOOK --title "Маленькая принцесса" --author "Ф. Г. Бернетт"
```

---

## Лицензия и данные

Папка `out/` содержит PDF и тексты книг — **не добавляйте в git**. В репозитории только код и `requirements.txt`.
