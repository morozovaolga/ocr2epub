"""
Единый пайплайн от PDF к EPUB
Следует точной схеме из PIPELINE_SCHEMA.md
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Принудительно UTF-8 на Windows
if sys.platform == 'win32':
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


def run_cmd(cmd, description=""):
    """Запускает команду и выводит описание"""
    if description:
        print(f"\n{'='*80}")
        print(f"{description}")
        print(f"{'='*80}")
    print(f"$ {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Ошибка: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Единый пайплайн: PDF → EPUB (по схеме PIPELINE_SCHEMA.md)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:

1. Базовый вариант (только EPUB без LLM):
   python pdf_to_epub.py --pdf book.pdf --title "Название" --author "Автор" \\
     --epub-template sample.epub

2. С LLM-коррекцией через GigaChat (рекомендуется):
   python pdf_to_epub.py --pdf book.pdf --title "Название" --author "Автор" \\
     --llm-correct --llm-chunk-size 6000 \\
     --post-clean --epub-template sample.epub

3. Полный пайплайн (LanguageTool + GigaChat + пост-очистка):
   python pdf_to_epub.py --pdf book.pdf --title "Название" --author "Автор" \\
     --lt-cloud --yandex-speller \\
     --llm-correct --llm-chunk-size 6000 \\
     --post-clean --epub-template sample.epub
        """
    )
    
    # Основные параметры
    parser.add_argument('--pdf', required=True, help='Путь к PDF файлу')
    parser.add_argument('--outdir', default='out', help='Папка для результатов (по умолчанию: out)')
    parser.add_argument('--title', required=True, help='Название книги')
    parser.add_argument('--author', default='', help='Автор книги')
    parser.add_argument('--html', action='store_true', help='Генерировать промежуточные HTML-файлы для ручной проверки в браузере')
    
    # Этап 0: Предобработка PDF (опционально, перед OCR)
    parser.add_argument('--preprocess', action='store_true', help='Предобработка PDF: выравнивание, шумодав, контраст (перед OCR)')
    parser.add_argument('--preprocess-preset', default='medium', choices=['light', 'medium', 'heavy', 'binarize'],
                        help='Пресет предобработки (по умолчанию: medium)')
    parser.add_argument('--preprocess-dpi', type=int, default=300, help='DPI рендеринга для предобработки (по умолчанию: 300)')
    parser.add_argument('--preprocess-steps', default='', help='Шаги предобработки через запятую (переопределяет пресет)')
    parser.add_argument('--preprocess-pages', default='', help='Страницы для предобработки: "1-10" или "1,3,5-7" (по умолчанию: все)')
    
    # Этап 1: Извлечение структуры (обязательно)
    parser.add_argument('--two-columns', action='store_true', help='PDF с двумя колонками на странице')
    
    # Этап 2: Oldspelling (опционально)
    parser.add_argument('--no-oldspelling', action='store_true', help='Пропустить применение правил старой орфографии')
    
    # Этап 3: Stanza токенизация (опционально)
    parser.add_argument('--stanza-tokenize', action='store_true', help='Улучшить разбиение на предложения через Stanza')
    parser.add_argument('--stanza-model', default='', help='Путь к модели Stanza (.pt файл)')
    
    # Этап 4: Модернизация (всегда выполняется)
    
    # Этап 5: LanguageTool + YandexSpeller (опционально)
    parser.add_argument('--lt-cloud', action='store_true', help='Использовать LanguageTool (облачная проверка)')
    parser.add_argument('--yandex-speller', action='store_true', help='Дополнительно использовать Yandex.Speller (бесплатно)')
    parser.add_argument('--chunk-size', type=int, default=6000, help='Размер чанка для LanguageTool (по умолчанию: 6000)')
    
    # Этап 6: LLM-коррекция через GigaChat (опционально, самый мощный инструмент)
    parser.add_argument('--llm-correct', action='store_true', help='Коррекция текста через GigaChat')
    parser.add_argument('--llm-model', default='', help='Модель GigaChat (по умолчанию: GigaChat)')
    parser.add_argument('--llm-api-key', default='', help='GIGACHAT_CREDENTIALS (или через env)')
    parser.add_argument('--llm-chunk-size', type=int, default=3000, help='Размер чанка для LLM (по умолчанию: 3000)')
    
    # Этап 7: Контекстная проверка (опционально)
    parser.add_argument('--context-check', action='store_true', help='Контекстная проверка (местоимение+глагол)')
    parser.add_argument('--context-out', default='context_warnings.txt', help='Файл с предупреждениями контекстной проверки')
    parser.add_argument('--context-pronouns', default='он,она,оно,они,мы,вы,ты', help='Местоимения для контекстной проверки')
    
    # Этап 8: Пост-очистка (опционально)
    parser.add_argument('--post-clean', action='store_true', help='Пост-очистка: склейка букв через пробел, исправление разорванных слов, латиница→кириллица')
    
    # Этап 9: Генерация EPUB (опционально)
    parser.add_argument('--epub-template', nargs='?', const='sample.epub', help='Путь к шаблону EPUB (по умолчанию: sample.epub). Если указан, генерирует EPUB')
    parser.add_argument('--cover-colors', default='', help='Пять HEX-цветов через запятую (полоска, верхний блок, заголовок, градиент начало, градиент конец)')
    parser.add_argument('--epub-max-chapter-size', type=int, default=50, help='Максимальный размер главы/секции в KB (по умолчанию: 50)')
    parser.add_argument('--epub-use-chapter-heads', action='store_true', help='Использовать поиск заголовков для разделения на главы (по умолчанию: простое разделение по размеру)')
    
    # Дополнительные проверки (параллельно)
    parser.add_argument('--natasha-check', action='store_true', help='Проверка именованных сущностей через Natasha')
    parser.add_argument('--natasha-types', default='PER,LOC', help='Типы сущностей для Natasha (PER, LOC, ORG)')
    parser.add_argument('--natasha-out', default='natasha_diff.txt', help='Файл отчета Natasha проверки')
    parser.add_argument('--natasha-sync', action='store_true', help='Синхронизация именованных сущностей через Natasha')
    parser.add_argument('--natasha-sync-report', default='natasha_sync.txt', help='Файл отчета Natasha синхронизации')
    
    args = parser.parse_args()
    
    here = Path(__file__).parent
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    
    # Проверяем наличие PDF
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"❌ Ошибка: PDF файл не найден: {pdf_path}")
        return 1
    
    print("=" * 80)
    print("ПАЙПЛАЙН: PDF → EPUB")
    print("=" * 80)
    print(f"PDF: {pdf_path}")
    print(f"Название: {args.title}")
    if args.author:
        print(f"Автор: {args.author}")
    print(f"Папка результатов: {outdir}")
    print("\nЭтапы обработки:")
    
    step_num = 1
    
    # Этап 0: Предобработка PDF (опционально)
    if args.preprocess:
        print(f"  0. Предобработка PDF (улучшение качества скана)")
        
        preprocess_output = outdir / f"{pdf_path.stem}_preprocessed.pdf"
        preprocess_cmd = [
            sys.executable,
            str(here / "preprocess_pdf.py"),
            "--pdf", str(pdf_path),
            "--out", str(preprocess_output),
            "--preset", args.preprocess_preset,
            "--dpi", str(args.preprocess_dpi),
        ]
        if args.preprocess_steps:
            preprocess_cmd.extend(["--steps", args.preprocess_steps])
        if args.preprocess_pages:
            preprocess_cmd.extend(["--pages", args.preprocess_pages])
        
        if not run_cmd(preprocess_cmd, "Этап 0: Предобработка PDF"):
            return 1
        
        # Используем предобработанный PDF для дальнейших этапов
        if preprocess_output.exists():
            pdf_path = preprocess_output
            print(f"  Используется предобработанный PDF: {pdf_path}")
        else:
            print(f"⚠️  Предупреждение: предобработанный PDF не создан, используем оригинал")
    
    # Этап 1: Извлечение структуры из PDF (PyMuPDF)
    print(f"  {step_num}. Извлечение структуры из PDF")
    step_num += 1
    
    extract_cmd = [
        sys.executable,
        str(here / "extract_structured_text.py"),
        "--pdf", str(pdf_path),
        "--outdir", str(outdir)
    ]
    if args.two_columns:
        extract_cmd.append("--two-columns")
    
    if not run_cmd(extract_cmd, f"Этап 1: Извлечение структуры"):
        return 1
    
    # Определяем входной файл для следующих этапов
    structured_in = outdir / "structured.json"
    
    # Этап 2: Применение правил oldspelling (опционально)
    if not args.no_oldspelling:
        print(f"  {step_num}. Применение правил старой орфографии")
        step_num += 1
        
        apply_cmd = [
            sys.executable,
            str(here / "apply_rules_structured.py"),
            "--rules", str(here / "oldspelling.py"),
            "--in", str(structured_in),
            "--out", str(outdir / "structured_rules.json")
        ]
        if not run_cmd(apply_cmd, f"Этап 2: Применение правил oldspelling"):
            return 1
        
        structured_in = outdir / "structured_rules.json"
    
    # Этап 3: Stanza токенизация (опционально)
    if args.stanza_tokenize and args.stanza_model:
        print(f"  {step_num}. Stanza токенизация")
        step_num += 1
        
        stanza_cmd = [
            sys.executable,
            str(here / "stanza_tokenizer.py"),
            "--in", str(structured_in),
            "--out", str(outdir / "structured_tokenized.json"),
            "--model", args.stanza_model
        ]
        if not run_cmd(stanza_cmd, f"Этап 3: Stanza токенизация"):
            return 1
        
        structured_in = outdir / "structured_tokenized.json"
    
    # Этап 4: Модернизация (всегда выполняется)
    print(f"  {step_num}. Модернизация орфографии")
    step_num += 1
    
    modernize_cmd = [
        sys.executable,
        str(here / "modernize_structured.py"),
        "--in", str(structured_in),
        "--outdir", str(outdir),
        "--title", args.title
    ]
    if not run_cmd(modernize_cmd, f"Этап 4: Модернизация орфографии"):
        return 1
    
    # Определяем входной файл для проверок орфографии
    spell_input = outdir / "final.txt"
    
    # Этап 5: LanguageTool + YandexSpeller (опционально)
    if args.lt_cloud or args.yandex_speller:
        checkers_desc = []
        if args.lt_cloud:
            checkers_desc.append("LanguageTool")
        if args.yandex_speller:
            checkers_desc.append("Yandex.Speller")
        print(f"  {step_num}. Проверка: {' + '.join(checkers_desc)}")
        step_num += 1
        
        if not spell_input.exists():
            print(f"⚠️  Предупреждение: {spell_input.name} не найден — проверка пропущена")
        else:
            lt_cmd = [
                sys.executable,
                str(here / "lt_cloud.py"),
                "--in", str(spell_input),
                "--outdir", str(outdir),
                "--title", args.title + " (LT)",
                "--chunk-size", str(args.chunk_size),
            ]
            if args.yandex_speller:
                lt_cmd.append("--yandex-speller")
            if not args.lt_cloud:
                lt_cmd.append("--no-lt")
            if not run_cmd(lt_cmd, f"Этап 5: {' + '.join(checkers_desc)}"):
                return 1
            
            # Обновляем входной файл для следующего этапа
            lt_output = outdir / "final_clean.txt"
            if lt_output.exists():
                spell_input = lt_output
    
    # Этап 6: LLM-коррекция через GigaChat (опционально)
    if args.llm_correct:
        print(f"  {step_num}. LLM-коррекция (GigaChat)")
        step_num += 1
        
        if not spell_input.exists():
            print(f"⚠️  Предупреждение: {spell_input.name} не найден — LLM-коррекция пропущена")
        else:
            llm_cmd = [
                sys.executable,
                str(here / "llm_correction.py"),
                "--in", str(spell_input),
                "--outdir", str(outdir),
                "--title", args.title + " (LLM)",
                "--chunk-size", str(args.llm_chunk_size),
            ]
            if args.llm_model:
                llm_cmd.extend(["--model", args.llm_model])
            if args.llm_api_key:
                llm_cmd.extend(["--api-key", args.llm_api_key])
            if not run_cmd(llm_cmd, f"Этап 6: LLM-коррекция (GigaChat)"):
                return 1
            
            # Обновляем входной файл для следующего этапа
            llm_output = outdir / "final_llm.txt"
            if llm_output.exists():
                spell_input = llm_output
    
    # Этап 7: Контекстная проверка (опционально)
    if args.context_check:
        print(f"  {step_num}. Контекстная проверка")
        step_num += 1
        
        # Используем лучший доступный файл
        context_input = spell_input
        if not context_input.exists():
            print(f"⚠️  Предупреждение: {context_input.name} не найден — контекстная проверка пропущена")
        else:
            context_cmd = [
                sys.executable,
                str(here / "context_checker.py"),
                "--in", str(context_input),
                "--out", str(outdir / args.context_out),
                "--pronouns", args.context_pronouns
            ]
            if not run_cmd(context_cmd, f"Этап 7: Контекстная проверка"):
                return 1
    
    # Этап 8: Пост-очистка (опционально)
    if args.post_clean:
        print(f"  {step_num}. Пост-очистка")
        step_num += 1
        
        # Используем лучший доступный файл
        post_clean_input = spell_input
        
        if not post_clean_input.exists():
            print(f"⚠️  Предупреждение: {post_clean_input.name} не найден — пост-очистка пропущена")
        else:
            post_clean_cmd = [
                sys.executable,
                str(here / "post_cleanup.py"),
                "--in", str(post_clean_input),
                "--out", str(outdir / "final_better.txt"),
                "--html", str(outdir / "final_better.html"),
                "--title", args.title + " (Post-clean)"
            ]
            
            if not run_cmd(post_clean_cmd, f"Этап 8: Пост-очистка"):
                return 1
    
    # Natasha проверки (параллельно, после всех коррекций)
    if args.natasha_check:
        natasha_input = spell_input
        if natasha_input.exists():
            # Убеждаемся, что путь к выходному файлу не содержит дублирования outdir
            natasha_out_path = Path(args.natasha_out)
            # Если путь уже содержит outdir в начале, убираем его
            natasha_out_str = str(natasha_out_path)
            outdir_str = str(outdir)
            if natasha_out_str.startswith(outdir_str):
                # Убираем префикс outdir и ведущие слеши
                relative_path = natasha_out_str[len(outdir_str):].lstrip('\\/')
                natasha_out = outdir / relative_path if relative_path else outdir / natasha_out_path.name
            elif natasha_out_path.is_absolute():
                natasha_out = natasha_out_path
            else:
                natasha_out = outdir / natasha_out_path
            
            # Создаем директорию для выходного файла, если её нет
            natasha_out.parent.mkdir(parents=True, exist_ok=True)
            
            natasha_cmd = [
                sys.executable,
                str(here / "natasha_entity_check.py"),
                "--pdf", str(pdf_path),
                "--clean", str(natasha_input),
                "--out", str(natasha_out),
                "--types", args.natasha_types
            ]
            run_cmd(natasha_cmd, "Natasha проверка именованных сущностей")
    
    if args.natasha_sync:
        natasha_input = spell_input
        if natasha_input.exists():
            # Убеждаемся, что путь к отчету не содержит дублирования outdir
            natasha_report_path = Path(args.natasha_sync_report)
            # Если путь уже содержит outdir в начале, убираем его
            natasha_report_str = str(natasha_report_path)
            outdir_str = str(outdir)
            if natasha_report_str.startswith(outdir_str):
                # Убираем префикс outdir и ведущие слеши
                relative_path = natasha_report_str[len(outdir_str):].lstrip('\\/')
                natasha_report = outdir / relative_path if relative_path else outdir / natasha_report_path.name
            elif natasha_report_path.is_absolute():
                natasha_report = natasha_report_path
            else:
                natasha_report = outdir / natasha_report_path
            
            # Создаем директорию для отчета, если её нет
            natasha_report.parent.mkdir(parents=True, exist_ok=True)
            
            natasha_sync_cmd = [
                sys.executable,
                str(here / "natasha_sync.py"),
                "--pdf", str(pdf_path),
                "--clean", str(natasha_input),
                "--types", args.natasha_types,
                "--report", str(natasha_report)
            ]
            run_cmd(natasha_sync_cmd, "Natasha синхронизация именованных сущностей")
    
    # Этап 9: Генерация EPUB (опционально)
    if args.epub_template:
        print(f"  {step_num}. Генерация EPUB")
        
        # Определяем лучший источник для EPUB (по приоритету из схемы)
        # Приоритет: TXT (чистый обработанный текст) > JSON (структурированные данные) > HTML (может содержать лишнюю разметку)
        epub_sources = [
            outdir / "final_better.txt",  # После post-clean (если был)
            outdir / "final_llm.txt",     # После LLM-коррекции (если была)
            outdir / "final_clean.txt",   # После LanguageTool/YandexSpeller
            outdir / "final.txt",
            outdir / "structured_rules.json",
            outdir / "structured.json",
            outdir / "final_better.html",  # После post-clean (если был)
            outdir / "final_clean.html",
        ]
        
        epub_source = None
        for source in epub_sources:
            if source.exists():
                # Проверяем, что файл не пустой
                try:
                    is_valid = False
                    if source.suffix.lower() == ".txt":
                        # Для TXT файлов проверяем, что есть достаточно текста (не только пробелы и не только цифры)
                        content = source.read_text(encoding="utf-8").strip()
                        # Проверяем, что есть текст и он не слишком короткий (минимум 50 символов)
                        # И что это не только цифры/пробелы
                        if content and len(content) >= 50:
                            # Проверяем, что есть буквы, а не только цифры
                            has_letters = any(c.isalpha() for c in content)
                            is_valid = has_letters
                        else:
                            is_valid = False
                    elif source.suffix.lower() == ".json":
                        # Для JSON файлов проверяем, что есть блоки
                        data = json.loads(source.read_text(encoding="utf-8"))
                        blocks = data.get("blocks", [])
                        is_valid = bool(blocks and any(b.get("text", "").strip() for b in blocks))
                    elif source.suffix.lower() in (".html", ".htm"):
                        # Для HTML файлов проверяем, что есть контент (не только теги)
                        content = source.read_text(encoding="utf-8")
                        # Убираем все теги и проверяем наличие текста
                        text_only = re.sub(r'<[^>]+>', '', content).strip()
                        # Если HTML содержит только простой текст в <pre> (без структуры),
                        # лучше использовать TXT файл, который будет проверен позже
                        # Проверяем, что это не просто <pre> с текстом
                        if re.search(r'<pre[^>]*>', content, re.IGNORECASE):
                            # Если есть <pre>, проверяем, есть ли другие структурированные элементы
                            has_structure = bool(re.search(r'<(h[1-6]|p|div|section|article|header|footer)[^>]*>', content, re.IGNORECASE))
                            # Если нет структуры, лучше пропустить этот HTML в пользу TXT
                            if not has_structure:
                                is_valid = False  # Пропускаем простой HTML в пользу TXT
                            else:
                                is_valid = bool(text_only)
                        else:
                            is_valid = bool(text_only)
                    
                    if is_valid:
                        epub_source = source
                        break
                    else:
                        # Определяем причину, почему файл был пропущен
                        reason = "пустой"
                        if source.suffix.lower() == ".txt":
                            try:
                                content = source.read_text(encoding="utf-8").strip()
                                if not content:
                                    reason = "пустой"
                                elif len(content) < 50:
                                    reason = f"слишком короткий ({len(content)} символов)"
                                elif not any(c.isalpha() for c in content):
                                    reason = "содержит только цифры/символы"
                            except:
                                reason = "ошибка чтения"
                        print(f"⚠️  Файл {source.name} существует, но {reason} - пропускаем")
                except Exception as e:
                    print(f"⚠️  Предупреждение: ошибка при проверке {source.name}: {e}")
                    continue
        
        if not epub_source:
            print(f"⚠️  Предупреждение: не найден подходящий файл для генерации EPUB")
            print(f"   Проверенные файлы: {', '.join(str(s.name) for s in epub_sources)}")
            # Показываем статус каждого файла
            for source in epub_sources:
                if source.exists():
                    try:
                        size = source.stat().st_size
                        print(f"   - {source.name}: существует ({size} байт)")
                    except:
                        print(f"   - {source.name}: существует (размер неизвестен)")
                else:
                    print(f"   - {source.name}: не найден")
        else:
            print(f"📄 Используется источник для EPUB: {epub_source.name}")
            # Показываем краткую информацию о содержимом
            try:
                if epub_source.suffix.lower() == ".json":
                    import json
                    data = json.loads(epub_source.read_text(encoding="utf-8"))
                    blocks = data.get("blocks", [])
                    blocks_with_text = [b for b in blocks if b.get("text", "").strip()]
                    print(f"   Содержит {len(blocks)} блоков, из них {len(blocks_with_text)} с текстом")
                elif epub_source.suffix.lower() == ".txt":
                    content = epub_source.read_text(encoding="utf-8")
                    lines = [l.strip() for l in content.splitlines() if l.strip()]
                    print(f"   Содержит {len(lines)} непустых строк, размер: {len(content)} символов")
            except Exception as e:
                print(f"   ⚠️  Не удалось проанализировать содержимое: {e}")
            template_epub = Path(args.epub_template)
            if not template_epub.is_absolute():
                if (here / template_epub).exists():
                    template_epub = here / template_epub
                elif (here / "sample.epub").exists():
                    template_epub = here / "sample.epub"
            
            if not template_epub.exists():
                print(f"⚠️  Предупреждение: шаблон EPUB не найден: {template_epub}")
            else:
                # Санитизируем имя файла для Windows (убираем недопустимые символы)
                safe_title = re.sub(r'[<>:"/\\|?*]', '_', args.title)
                safe_title = safe_title.replace(' ', '_')
                output_epub = outdir / f"{safe_title}.epub"
                epub_cmd = [
                    sys.executable,
                    str(here / "generate_epub.py"),
                    "--template", str(template_epub),
                    "--in", str(epub_source),
                    "--out", str(output_epub),
                    "--title", args.title,
                    "--max-chapter-size", str(args.epub_max_chapter_size),
                ]
                if args.author:
                    epub_cmd.extend(["--author", args.author])
                if args.cover_colors:
                    epub_cmd.extend(["--cover-colors", args.cover_colors])
                if args.epub_use_chapter_heads:
                    epub_cmd.append("--use-chapter-heads")
                
                if not run_cmd(epub_cmd, f"Этап 8: Генерация EPUB"):
                    return 1
                
                # Проверяем, создался ли EPUB
                if output_epub.exists():
                    print("\n" + "=" * 80)
                    print("✅ EPUB УСПЕШНО СОЗДАН!")
                    print("=" * 80)
                    print(f"  📚 {output_epub}")
                    print("=" * 80)
    
    # Удаляем промежуточные HTML если --html не указан
    if not args.html:
        html_files = list(outdir.glob("*.html"))
        if html_files:
            for hf in html_files:
                hf.unlink(missing_ok=True)
    
    print("\n" + "=" * 80)
    print("✅ ОБРАБОТКА ЗАВЕРШЕНА")
    print("=" * 80)
    print(f"Результаты в папке: {outdir}")
    
    # Показываем созданные файлы
    show_patterns = ["*.txt", "*.json", "*.epub"]
    if args.html:
        show_patterns.insert(1, "*.html")
    print("\nСозданные файлы:")
    for pattern in show_patterns:
        files = list(outdir.glob(pattern))
        if files:
            print(f"\n{pattern}:")
            for f in sorted(files):
                print(f"  - {f.name}")
    
    return 0


if __name__ == '__main__':
    exit(main())
