# OCR Rules

Храним детерминированные правила для исправления типичных OCR/скан ошибок.

Файлы
- `char_confusions.json` — символьные соответствия (лат↔кир, цифры↔буквы).
- `regex_rules.jsonl` — построчно JSON с regex→replace для абзацев/заголовков.
- `whitelist.jsonl` — белый список строк/паттернов, которые нельзя менять (опционально).
- `dictionaries/normalize.jsonl` — словарные замены терминов (опционально).
- `dictionaries/human_learned.jsonl` — **общая** база замен из ручной коррекции (UI review, action=edit). Пополняется автоматически при сохранении правок и «Применить»; читается `bpp apply` на всех книгах.

Схема `regex_rules.jsonl`
- `id` (str) — идентификатор правила.
- `pattern` (str, Python regex)
- `replace` (str)
- `scope` (list[str]) — роли блоков: `paragraph`, `header`, `footnote` (если не указано — все).
- `status` (str) — `active|shadow|disabled` (по умолчанию `active`).
- `notes` (str) — комментарий.

Пример строки:
{"id":"R-CYR-LAT-001","pattern":"(?<![A-Za-z])[A](?=\\w)","replace":"А","scope":["paragraph","header"],"status":"active","notes":"Латинская A внутри русских слов"}

Примечание: `\\p{Ll}` поддерживается библиотекой `regex`. В текущем пайплайне используем стандартный `re`, поэтому предпочитайте классы `\\w`, явные диапазоны или простые якоря.
