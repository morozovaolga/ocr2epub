"""
Р•РґРёРЅС‹Р№ РїР°Р№РїР»Р°Р№РЅ РѕС‚ PDF Рє EPUB
РЎР»РµРґСѓРµС‚ С‚РѕС‡РЅРѕР№ СЃС…РµРјРµ РёР· PIPELINE_SCHEMA.md
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime

# РџСЂРёРЅСѓРґРёС‚РµР»СЊРЅРѕ UTF-8 РЅР° Windows
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
    """Р—Р°РїСѓСЃРєР°РµС‚ РєРѕРјР°РЅРґСѓ Рё РІС‹РІРѕРґРёС‚ РѕРїРёСЃР°РЅРёРµ"""
    if description:
        print(f"\n{'='*80}")
        print(f"{description}")
        print(f"{'='*80}")
    print(f"$ {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError as e:
        print(f"РћС€РёР±РєР°: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Р•РґРёРЅС‹Р№ РїР°Р№РїР»Р°Р№РЅ: PDF в†’ EPUB (РїРѕ СЃС…РµРјРµ PIPELINE_SCHEMA.md)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
РџСЂРёРјРµСЂС‹ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёСЏ:

1. Р‘Р°Р·РѕРІС‹Р№ РІР°СЂРёР°РЅС‚ (С‚РѕР»СЊРєРѕ EPUB Р±РµР· LLM):
   python pdf_to_epub.py --pdf book.pdf --title "РќР°Р·РІР°РЅРёРµ" --author "РђРІС‚РѕСЂ" \\
     --epub-template sample.epub

2. РЎ LLM-РєРѕСЂСЂРµРєС†РёРµР№ С‡РµСЂРµР· GigaChat (СЂРµРєРѕРјРµРЅРґСѓРµС‚СЃСЏ):
   python pdf_to_epub.py --pdf book.pdf --title "РќР°Р·РІР°РЅРёРµ" --author "РђРІС‚РѕСЂ" \\
     --llm-correct --llm-chunk-size 6000 \\
     --post-clean --epub-template sample.epub

3. РџРѕР»РЅС‹Р№ РїР°Р№РїР»Р°Р№РЅ (LanguageTool + GigaChat + РїРѕСЃС‚-РѕС‡РёСЃС‚РєР°):
   python pdf_to_epub.py --pdf book.pdf --title "РќР°Р·РІР°РЅРёРµ" --author "РђРІС‚РѕСЂ" \\
     --lt-cloud --yandex-speller \\
     --llm-correct --llm-chunk-size 6000 \\
     --post-clean --epub-template sample.epub
        """
    )
    
    # РћСЃРЅРѕРІРЅС‹Рµ РїР°СЂР°РјРµС‚СЂС‹
    parser.add_argument('--pdf', required=True, help='РџСѓС‚СЊ Рє PDF С„Р°Р№Р»Сѓ')
    parser.add_argument('--outdir', default='out', help='РџР°РїРєР° РґР»СЏ СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: out)')
    parser.add_argument('--title', required=True, help='РќР°Р·РІР°РЅРёРµ РєРЅРёРіРё')
    parser.add_argument('--author', default='', help='РђРІС‚РѕСЂ РєРЅРёРіРё')
    parser.add_argument('--html', action='store_true', help='Р“РµРЅРµСЂРёСЂРѕРІР°С‚СЊ РїСЂРѕРјРµР¶СѓС‚РѕС‡РЅС‹Рµ HTML-С„Р°Р№Р»С‹ РґР»СЏ СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё РІ Р±СЂР°СѓР·РµСЂРµ')
    parser.add_argument(
        '--profile',
        default='',
        choices=['', 'prose', 'scan-old', 'poetry', 'fast'],
        help='РџСЂРѕС„РёР»СЊ РєР°С‡РµСЃС‚РІР°: РІС‹СЃС‚Р°РІР»СЏРµС‚ СЂРµРєРѕРјРµРЅРґСѓРµРјС‹Рµ С„Р»Р°РіРё РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ (РёС… РјРѕР¶РЅРѕ РїРµСЂРµРѕРїСЂРµРґРµР»СЏС‚СЊ СЏРІРЅРѕ)',
    )
    parser.add_argument(
        '--quality-report',
        nargs='?',
        const='quality_report.md',
        default='',
        help='Р•СЃР»Рё СѓРєР°Р·Р°РЅ, СЃРѕС…СЂР°РЅСЏРµС‚ РѕС‚С‡С‘С‚ Рѕ РїСЂРѕРіРѕРЅРµ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: out/quality_report.md). РњРѕР¶РЅРѕ СѓРєР°Р·Р°С‚СЊ РёРјСЏ С„Р°Р№Р»Р°.',
    )
    
    # Р­С‚Р°Рї 0: РџСЂРµРґРѕР±СЂР°Р±РѕС‚РєР° PDF (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ, РїРµСЂРµРґ OCR)
    parser.add_argument('--preprocess', action='store_true', help='РџСЂРµРґРѕР±СЂР°Р±РѕС‚РєР° PDF: РІС‹СЂР°РІРЅРёРІР°РЅРёРµ, С€СѓРјРѕРґР°РІ, РєРѕРЅС‚СЂР°СЃС‚ (РїРµСЂРµРґ OCR)')
    parser.add_argument('--preprocess-preset', default='medium', choices=['light', 'medium', 'heavy', 'binarize'],
                        help='РџСЂРµСЃРµС‚ РїСЂРµРґРѕР±СЂР°Р±РѕС‚РєРё (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: medium)')
    parser.add_argument('--preprocess-dpi', type=int, default=300, help='DPI СЂРµРЅРґРµСЂРёРЅРіР° РґР»СЏ РїСЂРµРґРѕР±СЂР°Р±РѕС‚РєРё (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 300)')
    parser.add_argument('--preprocess-steps', default='', help='РЁР°РіРё РїСЂРµРґРѕР±СЂР°Р±РѕС‚РєРё С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ (РїРµСЂРµРѕРїСЂРµРґРµР»СЏРµС‚ РїСЂРµСЃРµС‚)')
    parser.add_argument('--preprocess-pages', default='', help='РЎС‚СЂР°РЅРёС†С‹ РґР»СЏ РїСЂРµРґРѕР±СЂР°Р±РѕС‚РєРё: "1-10" РёР»Рё "1,3,5-7" (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: РІСЃРµ)')
    
    # Р­С‚Р°Рї 1: РР·РІР»РµС‡РµРЅРёРµ СЃС‚СЂСѓРєС‚СѓСЂС‹ (РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ)
    parser.add_argument('--two-columns', action='store_true', help='PDF СЃ РґРІСѓРјСЏ РєРѕР»РѕРЅРєР°РјРё РЅР° СЃС‚СЂР°РЅРёС†Рµ')
    parser.add_argument('--ocr-engine', default='auto',
                        choices=['auto', 'pymupdf', 'easyocr', 'tesseract', 'doctr'],
                        help='OCR-РґРІРёР¶РѕРє: auto (PyMuPDF + С„РѕР»Р»Р±СЌРє), pymupdf, easyocr, tesseract, doctr (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: auto)')
    parser.add_argument('--ocr-dpi', type=int, default=300,
                        help='DPI СЂРµРЅРґРµСЂРёРЅРіР° РґР»СЏ OCR-РґРІРёР¶РєРѕРІ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 300)')
    parser.add_argument('--poetry', action='store_true',
                        help='РџСЂРёРЅСѓРґРёС‚РµР»СЊРЅРѕ СЃС‡РёС‚Р°С‚СЊ РІСЃРµ Р±Р»РѕРєРё (РєСЂРѕРјРµ Р·Р°РіРѕР»РѕРІРєРѕРІ) СЃС‚РёС…Р°РјРё вЂ” СЃРѕС…СЂР°РЅСЏС‚СЊ РїРµСЂРµРЅРѕСЃС‹ СЃС‚СЂРѕРє')
    
    # Р­С‚Р°Рї 2: Oldspelling (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--no-oldspelling', action='store_true', help='РџСЂРѕРїСѓСЃС‚РёС‚СЊ РїСЂРёРјРµРЅРµРЅРёРµ РїСЂР°РІРёР» СЃС‚Р°СЂРѕР№ РѕСЂС„РѕРіСЂР°С„РёРё')
    
    # Р­С‚Р°Рї 3: Stanza С‚РѕРєРµРЅРёР·Р°С†РёСЏ (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--stanza-tokenize', action='store_true', help='РЈР»СѓС‡С€РёС‚СЊ СЂР°Р·Р±РёРµРЅРёРµ РЅР° РїСЂРµРґР»РѕР¶РµРЅРёСЏ С‡РµСЂРµР· Stanza')
    parser.add_argument('--stanza-model', default='', help='РџСѓС‚СЊ Рє РјРѕРґРµР»Рё Stanza (.pt С„Р°Р№Р»)')
    
    # Р­С‚Р°Рї 4: РњРѕРґРµСЂРЅРёР·Р°С†РёСЏ (РІСЃРµРіРґР° РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ)
    
    # Р­С‚Р°Рї 4.5: Р”РµСЃРїРїРµР№СЃРёРЅРі вЂ” СЃРєР»РµР№РєР° СЂР°Р·РѕСЂРІР°РЅРЅС‹С… РїСЂРѕР±РµР»Р°РјРё СЃР»РѕРІ (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--despace', action='store_true', help='РђРіСЂРµСЃСЃРёРІРЅР°СЏ СЃРєР»РµР№РєР° СЂР°Р·РѕСЂРІР°РЅРЅС‹С… РїСЂРѕР±РµР»Р°РјРё СЃР»РѕРІ (РґР»СЏ СЃРєР°РЅРѕРІ СЃС‚Р°СЂС‹С… РєРЅРёРі СЃ СѓРІРµР»РёС‡РµРЅРЅС‹Рј РєРµСЂРЅРёРЅРіРѕРј)')
    
    # Р­С‚Р°Рї 4.6: Р Р°Р·Р±РёРµРЅРёРµ СЃРєР»РµРµРЅРЅС‹С… СЃР»РѕРІ (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--word-split', action='store_true', help='Р Р°Р·Р±РёРµРЅРёРµ СЃРєР»РµРµРЅРЅС‹С… СЃР»РѕРІ (РѕР±СЂР°С‚РЅС‹Р№ РґРµСЃРїРµР№СЃРёРЅРі: "РђС„Р°РЅР°СЃРёР№РќРёРєРёС‚РёРЅ" в†’ "РђС„Р°РЅР°СЃРёР№ РќРёРєРёС‚РёРЅ")')
    
    # Р­С‚Р°Рї 5: LanguageTool + YandexSpeller (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--lt-cloud', action='store_true', help='РСЃРїРѕР»СЊР·РѕРІР°С‚СЊ LanguageTool (РѕР±Р»Р°С‡РЅР°СЏ РїСЂРѕРІРµСЂРєР°)')
    parser.add_argument('--yandex-speller', action='store_true', help='Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ Yandex.Speller (Р±РµСЃРїР»Р°С‚РЅРѕ)')
    parser.add_argument('--chunk-size', type=int, default=6000, help='Р Р°Р·РјРµСЂ С‡Р°РЅРєР° РґР»СЏ LanguageTool (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 6000)')
    
    # Р­С‚Р°Рї 6: LLM-РєРѕСЂСЂРµРєС†РёСЏ С‡РµСЂРµР· GigaChat (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ, СЃР°РјС‹Р№ РјРѕС‰РЅС‹Р№ РёРЅСЃС‚СЂСѓРјРµРЅС‚)
    parser.add_argument('--llm-correct', action='store_true', help='РљРѕСЂСЂРµРєС†РёСЏ С‚РµРєСЃС‚Р° С‡РµСЂРµР· GigaChat')
    parser.add_argument('--llm-model', default='', help='РњРѕРґРµР»СЊ GigaChat (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: GigaChat)')
    parser.add_argument('--llm-api-key', default='', help='GIGACHAT_CREDENTIALS (РёР»Рё С‡РµСЂРµР· env)')
    parser.add_argument('--llm-chunk-size', type=int, default=3000, help='Р Р°Р·РјРµСЂ С‡Р°РЅРєР° РґР»СЏ LLM (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 3000)')
    parser.add_argument('--llm-cautious', action='store_true', help='РћСЃС‚РѕСЂРѕР¶РЅС‹Р№ СЂРµР¶РёРј LLM: РЅРµ РјРµРЅСЏС‚СЊ СЃРѕРјРЅРёС‚РµР»СЊРЅС‹Рµ СЃР»РѕРІР°, Р° РІС‹РЅРµСЃС‚Рё РёС… РІ doubt_words.txt')
    parser.add_argument('--llm-old-russian', action='store_true', help='Р РµР¶РёРј РґР»СЏ СЃС‚Р°СЂРѕСЂСѓСЃСЃРєРёС…/РґСЂРµРІРЅРµСЂСѓСЃСЃРєРёС… С‚РµРєСЃС‚РѕРІ: Р°РіСЂРµСЃСЃРёРІРЅР°СЏ СЃРєР»РµР№РєР° СЂР°Р·РѕСЂРІР°РЅРЅС‹С… СЃР»РѕРІ, СЃРѕС…СЂР°РЅРµРЅРёРµ Р°СЂС…Р°РёР·РјРѕРІ')
    parser.add_argument('--llm-user-context', default='', help='Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Р№ РєРѕРЅС‚РµРєСЃС‚ РґР»СЏ LLM (РЅР°РїСЂ. "РўРµРєСЃС‚ РёР· В«РҐРѕР¶РґРµРЅРёСЏ Р·Р° С‚СЂРё РјРѕСЂСЏВ» XV РІРµРєР°")')
    parser.add_argument('--llm-overlap-chars', type=int, default=0, help='РќР°С…Р»С‘СЃС‚ РґР»СЏ LLM: С…РІРѕСЃС‚ РїСЂРµРґС‹РґСѓС‰РµРіРѕ С‡Р°РЅРєР° (СЃРёРјРІРѕР»С‹) РєР°Рє read-only РєРѕРЅС‚РµРєСЃС‚ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 0)')
    parser.add_argument('--llm-overlap-paragraphs', type=int, default=0, help='РќР°С…Р»С‘СЃС‚ РґР»СЏ LLM РїРѕ Р°Р±Р·Р°С†Р°Рј (РїСЂРµРґРїРѕС‡С‚РёС‚РµР»СЊРЅРµРµ СЃРёРјРІРѕР»РѕРІ, РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 0)')
    parser.add_argument('--llm-book-memory', action='store_true', help='LLM: РІРєР»СЋС‡РёС‚СЊ вЂњРїР°РјСЏС‚СЊ РєРЅРёРіРёвЂќ (РїРѕРґС‚СЏРіРёРІР°С‚СЊ РїРѕС…РѕР¶РёРµ РјРµСЃС‚Р° РёР· РґСЂСѓРіРёС… С‡Р°СЃС‚РµР№ С‚РµРєСЃС‚Р° РґР»СЏ СЃРѕРіР»Р°СЃРѕРІР°РЅРЅРѕСЃС‚Рё РёРјС‘РЅ/С‚РµСЂРјРёРЅРѕРІ)')
    parser.add_argument('--llm-memory-topk', type=int, default=3, help='LLM РїР°РјСЏС‚СЊ РєРЅРёРіРё: СЃРєРѕР»СЊРєРѕ РїРѕС…РѕР¶РёС… С„СЂР°РіРјРµРЅС‚РѕРІ РґРѕР±Р°РІР»СЏС‚СЊ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 3)')
    parser.add_argument('--llm-memory-exclude-window', type=int, default=20, help='LLM РїР°РјСЏС‚СЊ РєРЅРёРіРё: РёСЃРєР»СЋС‡Р°С‚СЊ Р°Р±Р·Р°С†С‹ СЂСЏРґРѕРј СЃ С‚РµРєСѓС‰РёРј С‡Р°РЅРєРѕРј (РїРѕ РѕР±Рµ СЃС‚РѕСЂРѕРЅС‹, РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 20)')
    parser.add_argument('--llm-memory-max-chars', type=int, default=1200, help='LLM РїР°РјСЏС‚СЊ РєРЅРёРіРё: Р»РёРјРёС‚ РЅР° РѕР±СЉС‘Рј retrieval-РєРѕРЅС‚РµРєСЃС‚Р° (СЃРёРјРІРѕР»С‹, РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 1200)')
    
    # Р­С‚Р°Рї 7: РљРѕРЅС‚РµРєСЃС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--context-check', action='store_true', help='РљРѕРЅС‚РµРєСЃС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР° (РјРµСЃС‚РѕРёРјРµРЅРёРµ+РіР»Р°РіРѕР»)')
    parser.add_argument('--context-out', default='context_warnings.txt', help='Р¤Р°Р№Р» СЃ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёСЏРјРё РєРѕРЅС‚РµРєСЃС‚РЅРѕР№ РїСЂРѕРІРµСЂРєРё')
    parser.add_argument('--context-pronouns', default='РѕРЅ,РѕРЅР°,РѕРЅРѕ,РѕРЅРё,РјС‹,РІС‹,С‚С‹', help='РњРµСЃС‚РѕРёРјРµРЅРёСЏ РґР»СЏ РєРѕРЅС‚РµРєСЃС‚РЅРѕР№ РїСЂРѕРІРµСЂРєРё')
    
    # Р­С‚Р°Рї 8: РџРѕСЃС‚-РѕС‡РёСЃС‚РєР° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--post-clean', action='store_true', help='РџРѕСЃС‚-РѕС‡РёСЃС‚РєР°: СЃРєР»РµР№РєР° Р±СѓРєРІ С‡РµСЂРµР· РїСЂРѕР±РµР», РёСЃРїСЂР°РІР»РµРЅРёРµ СЂР°Р·РѕСЂРІР°РЅРЅС‹С… СЃР»РѕРІ, Р»Р°С‚РёРЅРёС†Р°в†’РєРёСЂРёР»Р»РёС†Р°')
    
    # Р­С‚Р°Рї 9: Р“РµРЅРµСЂР°С†РёСЏ EPUB (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    parser.add_argument('--epub-template', nargs='?', const='sample.epub', help='РџСѓС‚СЊ Рє С€Р°Р±Р»РѕРЅСѓ EPUB (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: sample.epub). Р•СЃР»Рё СѓРєР°Р·Р°РЅ, РіРµРЅРµСЂРёСЂСѓРµС‚ EPUB')
    parser.add_argument('--cover-colors', default='', help='РџСЏС‚СЊ HEX-С†РІРµС‚РѕРІ С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ (РїРѕР»РѕСЃРєР°, РІРµСЂС…РЅРёР№ Р±Р»РѕРє, Р·Р°РіРѕР»РѕРІРѕРє, РіСЂР°РґРёРµРЅС‚ РЅР°С‡Р°Р»Рѕ, РіСЂР°РґРёРµРЅС‚ РєРѕРЅРµС†)')
    parser.add_argument('--epub-max-chapter-size', type=int, default=50, help='РњР°РєСЃРёРјР°Р»СЊРЅС‹Р№ СЂР°Р·РјРµСЂ РіР»Р°РІС‹/СЃРµРєС†РёРё РІ KB (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: 50)')
    parser.add_argument('--epub-use-chapter-heads', action='store_true', help='РСЃРїРѕР»СЊР·РѕРІР°С‚СЊ РїРѕРёСЃРє Р·Р°РіРѕР»РѕРІРєРѕРІ РґР»СЏ СЂР°Р·РґРµР»РµРЅРёСЏ РЅР° РіР»Р°РІС‹ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ: РїСЂРѕСЃС‚РѕРµ СЂР°Р·РґРµР»РµРЅРёРµ РїРѕ СЂР°Р·РјРµСЂСѓ)')
    
    # Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ РїСЂРѕРІРµСЂРєРё (РїР°СЂР°Р»Р»РµР»СЊРЅРѕ)
    parser.add_argument('--natasha-check', action='store_true', help='РџСЂРѕРІРµСЂРєР° РёРјРµРЅРѕРІР°РЅРЅС‹С… СЃСѓС‰РЅРѕСЃС‚РµР№ С‡РµСЂРµР· Natasha')
    parser.add_argument('--natasha-types', default='PER,LOC', help='РўРёРїС‹ СЃСѓС‰РЅРѕСЃС‚РµР№ РґР»СЏ Natasha (PER, LOC, ORG)')
    parser.add_argument('--natasha-out', default='natasha_diff.txt', help='Р¤Р°Р№Р» РѕС‚С‡РµС‚Р° Natasha РїСЂРѕРІРµСЂРєРё')
    parser.add_argument('--natasha-sync', action='store_true', help='РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ РёРјРµРЅРѕРІР°РЅРЅС‹С… СЃСѓС‰РЅРѕСЃС‚РµР№ С‡РµСЂРµР· Natasha')
    parser.add_argument('--natasha-sync-report', default='natasha_sync.txt', help='Р¤Р°Р№Р» РѕС‚С‡РµС‚Р° Natasha СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё')
    
    args = parser.parse_args()

    argv = set(sys.argv[1:])
    def _has_any(*flags: str) -> bool:
        return any(f in argv for f in flags)
    def _set_default(attr: str, value, *flags: str):
        if not _has_any(*flags):
            setattr(args, attr, value)

    # РџСЂРѕС„РёР»Рё РєР°С‡РµСЃС‚РІР° (СЃС‚Р°РІРёРј С‚РѕР»СЊРєРѕ РµСЃР»Рё РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ Р·Р°РґР°РІР°Р» С„Р»Р°РіРё СЏРІРЅРѕ)
    if args.profile == 'fast':
        _set_default('llm_correct', False, '--llm-correct')
        _set_default('lt_cloud', False, '--lt-cloud')
        _set_default('yandex_speller', False, '--yandex-speller')
        _set_default('post_clean', False, '--post-clean')
        _set_default('natasha_sync', False, '--natasha-sync')
    elif args.profile == 'poetry':
        _set_default('poetry', True, '--poetry')
        _set_default('llm_correct', False, '--llm-correct')
        _set_default('post_clean', True, '--post-clean')
    elif args.profile == 'scan-old':
        _set_default('preprocess', True, '--preprocess')
        _set_default('ocr_engine', 'easyocr', '--ocr-engine')
        _set_default('llm_correct', False, '--llm-correct')
        _set_default('llm_old_russian', True, '--llm-old-russian')
        _set_default('llm_chunk_size', 4500, '--llm-chunk-size')
        _set_default('llm_overlap_paragraphs', 1, '--llm-overlap-paragraphs')
        _set_default('post_clean', True, '--post-clean')
        _set_default('natasha_sync', True, '--natasha-sync')
    elif args.profile == 'prose':
        _set_default('lt_cloud', True, '--lt-cloud')
        _set_default('yandex_speller', True, '--yandex-speller')
        _set_default('llm_correct', False, '--llm-correct')
        _set_default('llm_chunk_size', 5000, '--llm-chunk-size')
        _set_default('llm_overlap_paragraphs', 1, '--llm-overlap-paragraphs')
        _set_default('llm_book_memory', True, '--llm-book-memory')
        _set_default('llm_memory_topk', 3, '--llm-memory-topk')
        _set_default('llm_memory_exclude_window', 30, '--llm-memory-exclude-window')
        _set_default('post_clean', True, '--post-clean')
        _set_default('natasha_sync', True, '--natasha-sync')
    
    here = Path(__file__).parent
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    
    # РџСЂРѕРІРµСЂСЏРµРј РЅР°Р»РёС‡РёРµ PDF
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"вќЊ РћС€РёР±РєР°: PDF С„Р°Р№Р» РЅРµ РЅР°Р№РґРµРЅ: {pdf_path}")
        return 1
    
    print("=" * 80)
    print("РџРђР™РџР›РђР™Рќ: PDF в†’ EPUB")
    print("=" * 80)
    print(f"PDF: {pdf_path}")
    print(f"РќР°Р·РІР°РЅРёРµ: {args.title}")
    if args.author:
        print(f"РђРІС‚РѕСЂ: {args.author}")
    print(f"РџР°РїРєР° СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ: {outdir}")
    print("\nР­С‚Р°РїС‹ РѕР±СЂР°Р±РѕС‚РєРё:")
    
    step_num = 1
    
    # Р­С‚Р°Рї 0: РџСЂРµРґРѕР±СЂР°Р±РѕС‚РєР° PDF (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.preprocess:
        print(f"  0. РџСЂРµРґРѕР±СЂР°Р±РѕС‚РєР° PDF (СѓР»СѓС‡С€РµРЅРёРµ РєР°С‡РµСЃС‚РІР° СЃРєР°РЅР°)")
        
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
        
        if not run_cmd(preprocess_cmd, "Р­С‚Р°Рї 0: РџСЂРµРґРѕР±СЂР°Р±РѕС‚РєР° PDF"):
            return 1
        
        # РСЃРїРѕР»СЊР·СѓРµРј РїСЂРµРґРѕР±СЂР°Р±РѕС‚Р°РЅРЅС‹Р№ PDF РґР»СЏ РґР°Р»СЊРЅРµР№С€РёС… СЌС‚Р°РїРѕРІ
        if preprocess_output.exists():
            pdf_path = preprocess_output
            print(f"  РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РїСЂРµРґРѕР±СЂР°Р±РѕС‚Р°РЅРЅС‹Р№ PDF: {pdf_path}")
        else:
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: РїСЂРµРґРѕР±СЂР°Р±РѕС‚Р°РЅРЅС‹Р№ PDF РЅРµ СЃРѕР·РґР°РЅ, РёСЃРїРѕР»СЊР·СѓРµРј РѕСЂРёРіРёРЅР°Р»")
    
    # Р­С‚Р°Рї 1: РР·РІР»РµС‡РµРЅРёРµ СЃС‚СЂСѓРєС‚СѓСЂС‹ РёР· PDF
    engine = getattr(args, 'ocr_engine', 'auto')
    ocr_dpi = getattr(args, 'ocr_dpi', 300)
    engine_label = engine if engine != 'auto' else 'auto (PyMuPDF + OCR-С„РѕР»Р»Р±СЌРє)'
    print(f"  {step_num}. РР·РІР»РµС‡РµРЅРёРµ СЃС‚СЂСѓРєС‚СѓСЂС‹ РёР· PDF (РґРІРёР¶РѕРє: {engine_label})")
    step_num += 1

    if engine in ('easyocr', 'tesseract', 'doctr') or engine == 'auto':
        ocr_cmd = [
            sys.executable,
            str(here / "ocr_engine.py"),
            "--pdf", str(pdf_path),
            "--outdir", str(outdir),
            "--engine", engine,
            "--dpi", str(ocr_dpi),
        ]
        if args.two_columns:
            ocr_cmd.append("--two-columns")
        if args.poetry:
            ocr_cmd.append("--poetry")
        if not run_cmd(ocr_cmd, f"Р­С‚Р°Рї 1: РР·РІР»РµС‡РµРЅРёРµ СЃС‚СЂСѓРєС‚СѓСЂС‹ (OCR: {engine})"):
            return 1
    else:
        extract_cmd = [
            sys.executable,
            str(here / "extract_structured_text.py"),
            "--pdf", str(pdf_path),
            "--outdir", str(outdir)
        ]
        if args.two_columns:
            extract_cmd.append("--two-columns")
        if args.poetry:
            extract_cmd.append("--poetry")
        if not run_cmd(extract_cmd, f"Р­С‚Р°Рї 1: РР·РІР»РµС‡РµРЅРёРµ СЃС‚СЂСѓРєС‚СѓСЂС‹ (PyMuPDF)"):
            return 1

    # РћРїСЂРµРґРµР»СЏРµРј РІС…РѕРґРЅРѕР№ С„Р°Р№Р» РґР»СЏ СЃР»РµРґСѓСЋС‰РёС… СЌС‚Р°РїРѕРІ
    structured_in = outdir / "structured.json"
    
    # Р­С‚Р°Рї 2: РџСЂРёРјРµРЅРµРЅРёРµ РїСЂР°РІРёР» oldspelling (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if not args.no_oldspelling:
        print(f"  {step_num}. РџСЂРёРјРµРЅРµРЅРёРµ РїСЂР°РІРёР» СЃС‚Р°СЂРѕР№ РѕСЂС„РѕРіСЂР°С„РёРё")
        step_num += 1
        
        apply_cmd = [
            sys.executable,
            str(here / "apply_rules_structured.py"),
            "--rules", str(here / "oldspelling.py"),
            "--in", str(structured_in),
            "--out", str(outdir / "structured_rules.json")
        ]
        if not run_cmd(apply_cmd, f"Р­С‚Р°Рї 2: РџСЂРёРјРµРЅРµРЅРёРµ РїСЂР°РІРёР» oldspelling"):
            return 1
        
        structured_in = outdir / "structured_rules.json"
    
    # Р­С‚Р°Рї 3: Stanza С‚РѕРєРµРЅРёР·Р°С†РёСЏ (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.stanza_tokenize and args.stanza_model:
        print(f"  {step_num}. Stanza С‚РѕРєРµРЅРёР·Р°С†РёСЏ")
        step_num += 1
        
        stanza_cmd = [
            sys.executable,
            str(here / "stanza_tokenizer.py"),
            "--in", str(structured_in),
            "--out", str(outdir / "structured_tokenized.json"),
            "--model", args.stanza_model
        ]
        if not run_cmd(stanza_cmd, f"Р­С‚Р°Рї 3: Stanza С‚РѕРєРµРЅРёР·Р°С†РёСЏ"):
            return 1
        
        structured_in = outdir / "structured_tokenized.json"
    
    # Р­С‚Р°Рї 4: РњРѕРґРµСЂРЅРёР·Р°С†РёСЏ (РІСЃРµРіРґР° РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ)
    print(f"  {step_num}. РњРѕРґРµСЂРЅРёР·Р°С†РёСЏ РѕСЂС„РѕРіСЂР°С„РёРё")
    step_num += 1
    
    modernize_cmd = [
        sys.executable,
        str(here / "modernize_structured.py"),
        "--in", str(structured_in),
        "--outdir", str(outdir),
        "--title", args.title
    ]
    if not run_cmd(modernize_cmd, f"Р­С‚Р°Рї 4: РњРѕРґРµСЂРЅРёР·Р°С†РёСЏ РѕСЂС„РѕРіСЂР°С„РёРё"):
        return 1
    
    # РћРїСЂРµРґРµР»СЏРµРј РІС…РѕРґРЅРѕР№ С„Р°Р№Р» РґР»СЏ РїСЂРѕРІРµСЂРѕРє РѕСЂС„РѕРіСЂР°С„РёРё
    spell_input = outdir / "final.txt"
    
    # Р­С‚Р°Рї 4.5: Р”РµСЃРїРїРµР№СЃРёРЅРі (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.despace:
        print(f"  {step_num}. Р”РµСЃРїРїРµР№СЃРёРЅРі (СЃРєР»РµР№РєР° СЂР°Р·РѕСЂРІР°РЅРЅС‹С… СЃР»РѕРІ)")
        step_num += 1
        
        if not spell_input.exists():
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: {spell_input.name} РЅРµ РЅР°Р№РґРµРЅ вЂ” РґРµСЃРїРїРµР№СЃРёРЅРі РїСЂРѕРїСѓС‰РµРЅ")
        else:
            despace_out = outdir / "final_despaced.txt"
            despace_cmd = [
                sys.executable,
                str(here / "despacer.py"),
                "--in", str(spell_input),
                "--out", str(despace_out),
                "--title", args.title + " (Despaced)",
            ]
            if not run_cmd(despace_cmd, f"Р­С‚Р°Рї 4.5: Р”РµСЃРїРїРµР№СЃРёРЅРі"):
                return 1
            
            if despace_out.exists():
                spell_input = despace_out
    
    # Р­С‚Р°Рї 4.6: Р Р°Р·Р±РёРµРЅРёРµ СЃРєР»РµРµРЅРЅС‹С… СЃР»РѕРІ (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.word_split:
        print(f"  {step_num}. Р Р°Р·Р±РёРµРЅРёРµ СЃРєР»РµРµРЅРЅС‹С… СЃР»РѕРІ (word splitting)")
        step_num += 1
        
        if not spell_input.exists():
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: {spell_input.name} РЅРµ РЅР°Р№РґРµРЅ вЂ” word splitting РїСЂРѕРїСѓС‰РµРЅ")
        else:
            wsplit_out = outdir / "final_wsplit.txt"
            wsplit_cmd = [
                sys.executable,
                str(here / "word_splitter.py"),
                "--in", str(spell_input),
                "--out", str(wsplit_out),
                "--title", args.title + " (Word Split)",
            ]
            if not run_cmd(wsplit_cmd, f"Р­С‚Р°Рї 4.6: Р Р°Р·Р±РёРµРЅРёРµ СЃРєР»РµРµРЅРЅС‹С… СЃР»РѕРІ"):
                return 1
            
            if wsplit_out.exists():
                spell_input = wsplit_out
    
    # Р­С‚Р°Рї 5: LanguageTool + YandexSpeller (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.lt_cloud or args.yandex_speller:
        checkers_desc = []
        if args.lt_cloud:
            checkers_desc.append("LanguageTool")
        if args.yandex_speller:
            checkers_desc.append("Yandex.Speller")
        print(f"  {step_num}. РџСЂРѕРІРµСЂРєР°: {' + '.join(checkers_desc)}")
        step_num += 1
        
        if not spell_input.exists():
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: {spell_input.name} РЅРµ РЅР°Р№РґРµРЅ вЂ” РїСЂРѕРІРµСЂРєР° РїСЂРѕРїСѓС‰РµРЅР°")
        else:
            lt_cmd = [
                sys.executable,
                str(here / "lt_cloud.py"),
                "--in", str(spell_input),
                "--outdir", str(outdir),
                "--title", args.title + " (LT)",
                "--chunk-size", str(args.chunk_size),
                "--stats-json", str(outdir / "lt_stats.json"),
            ]
            if args.yandex_speller:
                lt_cmd.append("--yandex-speller")
            if not args.lt_cloud:
                lt_cmd.append("--no-lt")
            if not run_cmd(lt_cmd, f"Р­С‚Р°Рї 5: {' + '.join(checkers_desc)}"):
                return 1
            
            # РћР±РЅРѕРІР»СЏРµРј РІС…РѕРґРЅРѕР№ С„Р°Р№Р» РґР»СЏ СЃР»РµРґСѓСЋС‰РµРіРѕ СЌС‚Р°РїР°
            lt_output = outdir / "final_clean.txt"
            if lt_output.exists():
                spell_input = lt_output
    
    # Р­С‚Р°Рї 6: LLM-РєРѕСЂСЂРµРєС†РёСЏ С‡РµСЂРµР· GigaChat (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.llm_correct:
        print(f"  {step_num}. LLM-РєРѕСЂСЂРµРєС†РёСЏ (GigaChat)")
        step_num += 1
        
        if not spell_input.exists():
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: {spell_input.name} РЅРµ РЅР°Р№РґРµРЅ вЂ” LLM-РєРѕСЂСЂРµРєС†РёСЏ РїСЂРѕРїСѓС‰РµРЅР°")
        else:
            llm_cmd = [
                sys.executable,
                str(here / "llm_correction.py"),
                "--in", str(spell_input),
                "--outdir", str(outdir),
                "--title", args.title + " (LLM)",
                "--chunk-size", str(args.llm_chunk_size),
                "--stats-json", str(outdir / "llm_stats.json"),
            ]
            if args.llm_model:
                llm_cmd.extend(["--model", args.llm_model])
            if args.llm_api_key:
                llm_cmd.extend(["--api-key", args.llm_api_key])
            if args.llm_cautious:
                llm_cmd.append("--cautious")
            if args.llm_old_russian:
                llm_cmd.append("--old-russian")
            if args.llm_user_context:
                llm_cmd.extend(["--user-context", args.llm_user_context])
            if args.llm_overlap_chars and args.llm_overlap_chars > 0:
                llm_cmd.extend(["--overlap-chars", str(args.llm_overlap_chars)])
            if args.llm_overlap_paragraphs and args.llm_overlap_paragraphs > 0:
                llm_cmd.extend(["--overlap-paragraphs", str(args.llm_overlap_paragraphs)])
            if args.llm_book_memory:
                llm_cmd.append("--book-memory")
                llm_cmd.extend(["--memory-topk", str(args.llm_memory_topk)])
                llm_cmd.extend(["--memory-exclude-window", str(args.llm_memory_exclude_window)])
                llm_cmd.extend(["--memory-max-chars", str(args.llm_memory_max_chars)])
            if not run_cmd(llm_cmd, f"Р­С‚Р°Рї 6: LLM-РєРѕСЂСЂРµРєС†РёСЏ (GigaChat)"):
                return 1
            
            # РћР±РЅРѕРІР»СЏРµРј РІС…РѕРґРЅРѕР№ С„Р°Р№Р» РґР»СЏ СЃР»РµРґСѓСЋС‰РµРіРѕ СЌС‚Р°РїР°
            llm_output = outdir / "final_llm.txt"
            if llm_output.exists():
                spell_input = llm_output
    
    # Р­С‚Р°Рї 7: РљРѕРЅС‚РµРєСЃС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.context_check:
        print(f"  {step_num}. РљРѕРЅС‚РµРєСЃС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР°")
        step_num += 1
        
        # РСЃРїРѕР»СЊР·СѓРµРј Р»СѓС‡С€РёР№ РґРѕСЃС‚СѓРїРЅС‹Р№ С„Р°Р№Р»
        context_input = spell_input
        if not context_input.exists():
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: {context_input.name} РЅРµ РЅР°Р№РґРµРЅ вЂ” РєРѕРЅС‚РµРєСЃС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР° РїСЂРѕРїСѓС‰РµРЅР°")
        else:
            context_cmd = [
                sys.executable,
                str(here / "context_checker.py"),
                "--in", str(context_input),
                "--out", str(outdir / args.context_out),
                "--pronouns", args.context_pronouns
            ]
            if not run_cmd(context_cmd, f"Р­С‚Р°Рї 7: РљРѕРЅС‚РµРєСЃС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР°"):
                return 1
    
    # Р­С‚Р°Рї 8: РџРѕСЃС‚-РѕС‡РёСЃС‚РєР° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.post_clean:
        print(f"  {step_num}. РџРѕСЃС‚-РѕС‡РёСЃС‚РєР°")
        step_num += 1
        
        # РСЃРїРѕР»СЊР·СѓРµРј Р»СѓС‡С€РёР№ РґРѕСЃС‚СѓРїРЅС‹Р№ С„Р°Р№Р»
        post_clean_input = spell_input
        
        if not post_clean_input.exists():
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: {post_clean_input.name} РЅРµ РЅР°Р№РґРµРЅ вЂ” РїРѕСЃС‚-РѕС‡РёСЃС‚РєР° РїСЂРѕРїСѓС‰РµРЅР°")
        else:
            post_clean_cmd = [
                sys.executable,
                str(here / "post_cleanup.py"),
                "--in", str(post_clean_input),
                "--out", str(outdir / "final_better.txt"),
                "--html", str(outdir / "final_better.html"),
                "--title", args.title + " (Post-clean)"
            ]
            if args.poetry:
                post_clean_cmd.append("--preserve-newlines")
            
            if not run_cmd(post_clean_cmd, f"Р­С‚Р°Рї 8: РџРѕСЃС‚-РѕС‡РёСЃС‚РєР°"):
                return 1
    
    # Natasha РїСЂРѕРІРµСЂРєРё (РїР°СЂР°Р»Р»РµР»СЊРЅРѕ, РїРѕСЃР»Рµ РІСЃРµС… РєРѕСЂСЂРµРєС†РёР№)
    if args.natasha_check:
        natasha_input = spell_input
        if natasha_input.exists():
            # РЈР±РµР¶РґР°РµРјСЃСЏ, С‡С‚Рѕ РїСѓС‚СЊ Рє РІС‹С…РѕРґРЅРѕРјСѓ С„Р°Р№Р»Сѓ РЅРµ СЃРѕРґРµСЂР¶РёС‚ РґСѓР±Р»РёСЂРѕРІР°РЅРёСЏ outdir
            natasha_out_path = Path(args.natasha_out)
            # Р•СЃР»Рё РїСѓС‚СЊ СѓР¶Рµ СЃРѕРґРµСЂР¶РёС‚ outdir РІ РЅР°С‡Р°Р»Рµ, СѓР±РёСЂР°РµРј РµРіРѕ
            natasha_out_str = str(natasha_out_path)
            outdir_str = str(outdir)
            if natasha_out_str.startswith(outdir_str):
                # РЈР±РёСЂР°РµРј РїСЂРµС„РёРєСЃ outdir Рё РІРµРґСѓС‰РёРµ СЃР»РµС€Рё
                relative_path = natasha_out_str[len(outdir_str):].lstrip('\\/')
                natasha_out = outdir / relative_path if relative_path else outdir / natasha_out_path.name
            elif natasha_out_path.is_absolute():
                natasha_out = natasha_out_path
            else:
                natasha_out = outdir / natasha_out_path
            
            # РЎРѕР·РґР°РµРј РґРёСЂРµРєС‚РѕСЂРёСЋ РґР»СЏ РІС‹С…РѕРґРЅРѕРіРѕ С„Р°Р№Р»Р°, РµСЃР»Рё РµС‘ РЅРµС‚
            natasha_out.parent.mkdir(parents=True, exist_ok=True)
            
            natasha_cmd = [
                sys.executable,
                str(here / "natasha_entity_check.py"),
                "--pdf", str(pdf_path),
                "--clean", str(natasha_input),
                "--out", str(natasha_out),
                "--types", args.natasha_types
            ]
            run_cmd(natasha_cmd, "Natasha РїСЂРѕРІРµСЂРєР° РёРјРµРЅРѕРІР°РЅРЅС‹С… СЃСѓС‰РЅРѕСЃС‚РµР№")
    
    if args.natasha_sync:
        natasha_input = spell_input
        if natasha_input.exists():
            # РЈР±РµР¶РґР°РµРјСЃСЏ, С‡С‚Рѕ РїСѓС‚СЊ Рє РѕС‚С‡РµС‚Сѓ РЅРµ СЃРѕРґРµСЂР¶РёС‚ РґСѓР±Р»РёСЂРѕРІР°РЅРёСЏ outdir
            natasha_report_path = Path(args.natasha_sync_report)
            # Р•СЃР»Рё РїСѓС‚СЊ СѓР¶Рµ СЃРѕРґРµСЂР¶РёС‚ outdir РІ РЅР°С‡Р°Р»Рµ, СѓР±РёСЂР°РµРј РµРіРѕ
            natasha_report_str = str(natasha_report_path)
            outdir_str = str(outdir)
            if natasha_report_str.startswith(outdir_str):
                # РЈР±РёСЂР°РµРј РїСЂРµС„РёРєСЃ outdir Рё РІРµРґСѓС‰РёРµ СЃР»РµС€Рё
                relative_path = natasha_report_str[len(outdir_str):].lstrip('\\/')
                natasha_report = outdir / relative_path if relative_path else outdir / natasha_report_path.name
            elif natasha_report_path.is_absolute():
                natasha_report = natasha_report_path
            else:
                natasha_report = outdir / natasha_report_path
            
            # РЎРѕР·РґР°РµРј РґРёСЂРµРєС‚РѕСЂРёСЋ РґР»СЏ РѕС‚С‡РµС‚Р°, РµСЃР»Рё РµС‘ РЅРµС‚
            natasha_report.parent.mkdir(parents=True, exist_ok=True)
            
            natasha_sync_cmd = [
                sys.executable,
                str(here / "natasha_sync.py"),
                "--pdf", str(pdf_path),
                "--clean", str(natasha_input),
                "--types", args.natasha_types,
                "--report", str(natasha_report)
            ]
            run_cmd(natasha_sync_cmd, "Natasha СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ РёРјРµРЅРѕРІР°РЅРЅС‹С… СЃСѓС‰РЅРѕСЃС‚РµР№")
    
    # Р­С‚Р°Рї 9: Р“РµРЅРµСЂР°С†РёСЏ EPUB (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.epub_template:
        print(f"  {step_num}. Р“РµРЅРµСЂР°С†РёСЏ EPUB")
        
        # РћРїСЂРµРґРµР»СЏРµРј Р»СѓС‡С€РёР№ РёСЃС‚РѕС‡РЅРёРє РґР»СЏ EPUB (РїРѕ РїСЂРёРѕСЂРёС‚РµС‚Сѓ РёР· СЃС…РµРјС‹)
        # РџСЂРёРѕСЂРёС‚РµС‚: TXT (С‡РёСЃС‚С‹Р№ РѕР±СЂР°Р±РѕС‚Р°РЅРЅС‹Р№ С‚РµРєСЃС‚) > JSON (СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅС‹Рµ РґР°РЅРЅС‹Рµ) > HTML (РјРѕР¶РµС‚ СЃРѕРґРµСЂР¶Р°С‚СЊ Р»РёС€РЅСЋСЋ СЂР°Р·РјРµС‚РєСѓ)
        epub_sources = [
            outdir / "final_better.txt",  # РџРѕСЃР»Рµ post-clean (РµСЃР»Рё Р±С‹Р»)
            outdir / "final_llm.txt",     # РџРѕСЃР»Рµ LLM-РєРѕСЂСЂРµРєС†РёРё (РµСЃР»Рё Р±С‹Р»Р°)
            outdir / "final_clean.txt",   # РџРѕСЃР»Рµ LanguageTool/YandexSpeller
            outdir / "final_despaced.txt", # РџРѕСЃР»Рµ РґРµСЃРїРїРµР№СЃРёРЅРіР° (РµСЃР»Рё Р±С‹Р»)
            outdir / "final_wsplit.txt",   # РџРѕСЃР»Рµ СЂР°Р·Р±РёРµРЅРёСЏ СЃРєР»РµРµРЅРЅС‹С… СЃР»РѕРІ (РµСЃР»Рё Р±С‹Р»)
            outdir / "final_structured.json",  # РњРѕРґРµСЂРЅРёР·РёСЂРѕРІР°РЅРЅС‹Р№ JSON СЃ СЂРѕР»СЏРјРё (СЃС‚РёС…Рё)
            outdir / "final.txt",
            outdir / "structured_rules.json",
            outdir / "structured.json",
            outdir / "final_better.html",  # РџРѕСЃР»Рµ post-clean (РµСЃР»Рё Р±С‹Р»)
            outdir / "final_clean.html",
        ]
        
        epub_source = None
        for source in epub_sources:
            if source.exists():
                # РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ С„Р°Р№Р» РЅРµ РїСѓСЃС‚РѕР№
                try:
                    is_valid = False
                    if source.suffix.lower() == ".txt":
                        # Р”Р»СЏ TXT С„Р°Р№Р»РѕРІ РїСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ РµСЃС‚СЊ РґРѕСЃС‚Р°С‚РѕС‡РЅРѕ С‚РµРєСЃС‚Р° (РЅРµ С‚РѕР»СЊРєРѕ РїСЂРѕР±РµР»С‹ Рё РЅРµ С‚РѕР»СЊРєРѕ С†РёС„СЂС‹)
                        content = source.read_text(encoding="utf-8").strip()
                        # РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ РµСЃС‚СЊ С‚РµРєСЃС‚ Рё РѕРЅ РЅРµ СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРёР№ (РјРёРЅРёРјСѓРј 50 СЃРёРјРІРѕР»РѕРІ)
                        # Р С‡С‚Рѕ СЌС‚Рѕ РЅРµ С‚РѕР»СЊРєРѕ С†РёС„СЂС‹/РїСЂРѕР±РµР»С‹
                        if content and len(content) >= 50:
                            # РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ РµСЃС‚СЊ Р±СѓРєРІС‹, Р° РЅРµ С‚РѕР»СЊРєРѕ С†РёС„СЂС‹
                            has_letters = any(c.isalpha() for c in content)
                            is_valid = has_letters
                        else:
                            is_valid = False
                    elif source.suffix.lower() == ".json":
                        # Р”Р»СЏ JSON С„Р°Р№Р»РѕРІ РїСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ РµСЃС‚СЊ Р±Р»РѕРєРё
                        data = json.loads(source.read_text(encoding="utf-8"))
                        blocks = data.get("blocks", [])
                        is_valid = bool(blocks and any(b.get("text", "").strip() for b in blocks))
                    elif source.suffix.lower() in (".html", ".htm"):
                        # Р”Р»СЏ HTML С„Р°Р№Р»РѕРІ РїСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ РµСЃС‚СЊ РєРѕРЅС‚РµРЅС‚ (РЅРµ С‚РѕР»СЊРєРѕ С‚РµРіРё)
                        content = source.read_text(encoding="utf-8")
                        # РЈР±РёСЂР°РµРј РІСЃРµ С‚РµРіРё Рё РїСЂРѕРІРµСЂСЏРµРј РЅР°Р»РёС‡РёРµ С‚РµРєСЃС‚Р°
                        text_only = re.sub(r'<[^>]+>', '', content).strip()
                        # Р•СЃР»Рё HTML СЃРѕРґРµСЂР¶РёС‚ С‚РѕР»СЊРєРѕ РїСЂРѕСЃС‚РѕР№ С‚РµРєСЃС‚ РІ <pre> (Р±РµР· СЃС‚СЂСѓРєС‚СѓСЂС‹),
                        # Р»СѓС‡С€Рµ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ TXT С„Р°Р№Р», РєРѕС‚РѕСЂС‹Р№ Р±СѓРґРµС‚ РїСЂРѕРІРµСЂРµРЅ РїРѕР·Р¶Рµ
                        # РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ СЌС‚Рѕ РЅРµ РїСЂРѕСЃС‚Рѕ <pre> СЃ С‚РµРєСЃС‚РѕРј
                        if re.search(r'<pre[^>]*>', content, re.IGNORECASE):
                            # Р•СЃР»Рё РµСЃС‚СЊ <pre>, РїСЂРѕРІРµСЂСЏРµРј, РµСЃС‚СЊ Р»Рё РґСЂСѓРіРёРµ СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅС‹Рµ СЌР»РµРјРµРЅС‚С‹
                            has_structure = bool(re.search(r'<(h[1-6]|p|div|section|article|header|footer)[^>]*>', content, re.IGNORECASE))
                            # Р•СЃР»Рё РЅРµС‚ СЃС‚СЂСѓРєС‚СѓСЂС‹, Р»СѓС‡С€Рµ РїСЂРѕРїСѓСЃС‚РёС‚СЊ СЌС‚РѕС‚ HTML РІ РїРѕР»СЊР·Сѓ TXT
                            if not has_structure:
                                is_valid = False  # РџСЂРѕРїСѓСЃРєР°РµРј РїСЂРѕСЃС‚РѕР№ HTML РІ РїРѕР»СЊР·Сѓ TXT
                            else:
                                is_valid = bool(text_only)
                        else:
                            is_valid = bool(text_only)
                    
                    if is_valid:
                        epub_source = source
                        break
                    else:
                        # РћРїСЂРµРґРµР»СЏРµРј РїСЂРёС‡РёРЅСѓ, РїРѕС‡РµРјСѓ С„Р°Р№Р» Р±С‹Р» РїСЂРѕРїСѓС‰РµРЅ
                        reason = "РїСѓСЃС‚РѕР№"
                        if source.suffix.lower() == ".txt":
                            try:
                                content = source.read_text(encoding="utf-8").strip()
                                if not content:
                                    reason = "РїСѓСЃС‚РѕР№"
                                elif len(content) < 50:
                                    reason = f"СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРёР№ ({len(content)} СЃРёРјРІРѕР»РѕРІ)"
                                elif not any(c.isalpha() for c in content):
                                    reason = "СЃРѕРґРµСЂР¶РёС‚ С‚РѕР»СЊРєРѕ С†РёС„СЂС‹/СЃРёРјРІРѕР»С‹"
                            except:
                                reason = "РѕС€РёР±РєР° С‡С‚РµРЅРёСЏ"
                        print(f"вљ пёЏ  Р¤Р°Р№Р» {source.name} СЃСѓС‰РµСЃС‚РІСѓРµС‚, РЅРѕ {reason} - РїСЂРѕРїСѓСЃРєР°РµРј")
                except Exception as e:
                    print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: РѕС€РёР±РєР° РїСЂРё РїСЂРѕРІРµСЂРєРµ {source.name}: {e}")
                    continue
        
        if not epub_source:
            print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: РЅРµ РЅР°Р№РґРµРЅ РїРѕРґС…РѕРґСЏС‰РёР№ С„Р°Р№Р» РґР»СЏ РіРµРЅРµСЂР°С†РёРё EPUB")
            print(f"   РџСЂРѕРІРµСЂРµРЅРЅС‹Рµ С„Р°Р№Р»С‹: {', '.join(str(s.name) for s in epub_sources)}")
            # РџРѕРєР°Р·С‹РІР°РµРј СЃС‚Р°С‚СѓСЃ РєР°Р¶РґРѕРіРѕ С„Р°Р№Р»Р°
            for source in epub_sources:
                if source.exists():
                    try:
                        size = source.stat().st_size
                        print(f"   - {source.name}: СЃСѓС‰РµСЃС‚РІСѓРµС‚ ({size} Р±Р°Р№С‚)")
                    except:
                        print(f"   - {source.name}: СЃСѓС‰РµСЃС‚РІСѓРµС‚ (СЂР°Р·РјРµСЂ РЅРµРёР·РІРµСЃС‚РµРЅ)")
                else:
                    print(f"   - {source.name}: РЅРµ РЅР°Р№РґРµРЅ")
        else:
            print(f"рџ“„ РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РёСЃС‚РѕС‡РЅРёРє РґР»СЏ EPUB: {epub_source.name}")
            # РџРѕРєР°Р·С‹РІР°РµРј РєСЂР°С‚РєСѓСЋ РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ СЃРѕРґРµСЂР¶РёРјРѕРј
            try:
                if epub_source.suffix.lower() == ".json":
                    data = json.loads(epub_source.read_text(encoding="utf-8"))
                    blocks = data.get("blocks", [])
                    blocks_with_text = [b for b in blocks if b.get("text", "").strip()]
                    print(f"   РЎРѕРґРµСЂР¶РёС‚ {len(blocks)} Р±Р»РѕРєРѕРІ, РёР· РЅРёС… {len(blocks_with_text)} СЃ С‚РµРєСЃС‚РѕРј")
                elif epub_source.suffix.lower() == ".txt":
                    content = epub_source.read_text(encoding="utf-8")
                    lines = [l.strip() for l in content.splitlines() if l.strip()]
                    print(f"   РЎРѕРґРµСЂР¶РёС‚ {len(lines)} РЅРµРїСѓСЃС‚С‹С… СЃС‚СЂРѕРє, СЂР°Р·РјРµСЂ: {len(content)} СЃРёРјРІРѕР»РѕРІ")
            except Exception as e:
                print(f"   вљ пёЏ  РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕР°РЅР°Р»РёР·РёСЂРѕРІР°С‚СЊ СЃРѕРґРµСЂР¶РёРјРѕРµ: {e}")
            template_epub = Path(args.epub_template)
            if not template_epub.is_absolute():
                if (here / template_epub).exists():
                    template_epub = here / template_epub
                elif (here / "sample.epub").exists():
                    template_epub = here / "sample.epub"
            
            if not template_epub.exists():
                print(f"вљ пёЏ  РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ: С€Р°Р±Р»РѕРЅ EPUB РЅРµ РЅР°Р№РґРµРЅ: {template_epub}")
            else:
                # РЎР°РЅРёС‚РёР·РёСЂСѓРµРј РёРјСЏ С„Р°Р№Р»Р° РґР»СЏ Windows (СѓР±РёСЂР°РµРј РЅРµРґРѕРїСѓСЃС‚РёРјС‹Рµ СЃРёРјРІРѕР»С‹)
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
                    "--epubcheck-json", str(outdir / "epubcheck_report.json"),
                ]
                if args.author:
                    epub_cmd.extend(["--author", args.author])
                if args.cover_colors:
                    epub_cmd.extend(["--cover-colors", args.cover_colors])
                if args.epub_use_chapter_heads:
                    epub_cmd.append("--use-chapter-heads")
                
                if not run_cmd(epub_cmd, f"Р­С‚Р°Рї 8: Р“РµРЅРµСЂР°С†РёСЏ EPUB"):
                    return 1
                
                # РџСЂРѕРІРµСЂСЏРµРј, СЃРѕР·РґР°Р»СЃСЏ Р»Рё EPUB
                if output_epub.exists():
                    print("\n" + "=" * 80)
                    print("вњ… EPUB РЈРЎРџР•РЁРќРћ РЎРћР—Р”РђРќ!")
                    print("=" * 80)
                    print(f"  рџ“љ {output_epub}")
                    print("=" * 80)
    
    # РЈРґР°Р»СЏРµРј РїСЂРѕРјРµР¶СѓС‚РѕС‡РЅС‹Рµ HTML РµСЃР»Рё --html РЅРµ СѓРєР°Р·Р°РЅ
    if not args.html:
        html_files = list(outdir.glob("*.html"))
        if html_files:
            for hf in html_files:
                hf.unlink(missing_ok=True)
    
    print("\n" + "=" * 80)
    print("вњ… РћР‘Р РђР‘РћРўРљРђ Р—РђР’Р•Р РЁР•РќРђ")
    print("=" * 80)
    print(f"Р РµР·СѓР»СЊС‚Р°С‚С‹ РІ РїР°РїРєРµ: {outdir}")

    # РћС‚С‡С‘С‚ РєР°С‡РµСЃС‚РІР° (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ)
    if args.quality_report:
        try:
            rep_path = Path(args.quality_report)
            if not rep_path.is_absolute():
                rep_path = outdir / rep_path

            def _read_json(p: Path):
                if not p.exists():
                    return None
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return None

            lt_stats = _read_json(outdir / "lt_stats.json")
            llm_stats = _read_json(outdir / "llm_stats.json")
            epubcheck = _read_json(outdir / "epubcheck_report.json")

            # Natasha sync report
            natasha_report_path = Path(args.natasha_sync_report)
            if not natasha_report_path.is_absolute():
                natasha_report_path = outdir / natasha_report_path
            natasha_report = natasha_report_path.read_text(encoding="utf-8", errors="ignore") if natasha_report_path.exists() else ""

            # Doubt words
            doubt_path = outdir / "doubt_words.txt"
            doubts_count = 0
            if doubt_path.exists():
                txt = doubt_path.read_text(encoding="utf-8", errors="ignore")
                doubts_count = len([l for l in txt.splitlines() if l.strip() and not l.strip().startswith("=")])

            # РС‚РѕРіРѕРІС‹Рµ С„Р°Р№Р»С‹
            final_candidates = [
                outdir / "final_better.txt",
                outdir / "final_llm.txt",
                outdir / "final_clean.txt",
                outdir / "final.txt",
            ]
            final_path = next((p for p in final_candidates if p.exists()), None)
            final_chars = final_path.stat().st_size if final_path else 0

            lines = []
            lines.append(f"# OCR2EPUB вЂ” РѕС‚С‡С‘С‚ РєР°С‡РµСЃС‚РІР°\n")
            lines.append(f"- Р”Р°С‚Р°: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"- PDF: `{args.pdf}`")
            lines.append(f"- РџСЂРѕС„РёР»СЊ: `{args.profile or 'вЂ”'}`")
            lines.append(f"- Р’С‹С…РѕРґ: `{outdir}`\n")

            lines.append("## РСЃРїРѕР»СЊР·РѕРІР°РЅРЅС‹Рµ СЌС‚Р°РїС‹")
            lines.append(f"- OCR engine: `{getattr(args, 'ocr_engine', 'auto')}`")
            lines.append(f"- LanguageTool: `{bool(args.lt_cloud)}`")
            lines.append(f"- Yandex.Speller: `{bool(args.yandex_speller)}`")
            lines.append(f"- LLM: `{bool(args.llm_correct)}`")
            lines.append(f"- Post-clean: `{bool(args.post_clean)}`")
            lines.append(f"- Natasha sync: `{bool(args.natasha_sync)}`\n")

            lines.append("## РњРµС‚СЂРёРєРё")
            if epubcheck:
                valid = epubcheck.get("valid", None)
                status = epubcheck.get("status", "unknown")
                errs = epubcheck.get("errors", 0)
                warns = epubcheck.get("warnings", 0)
                lines.append(f"- EPUBCheck: СЃС‚Р°С‚СѓСЃ `{status}`, valid=`{valid}`, **{errs} РѕС€РёР±РѕРє**, **{warns} РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№**")
                msgs = epubcheck.get("messages") or []
                if msgs and (errs or warns):
                    lines.append("  - РџСЂРёРјРµСЂС‹ СЃРѕРѕР±С‰РµРЅРёР№:")
                    for m in msgs[:8]:
                        lvl = m.get("level", "INFO")
                        msg = m.get("message", "")
                        loc = m.get("location", "")
                        loc_part = f" ({loc})" if loc else ""
                        lines.append(f"    - `{lvl}` {msg}{loc_part}")
            if lt_stats:
                lines.append(f"- LT РёСЃРїСЂР°РІР»РµРЅРёР№: **{lt_stats.get('applied_total', 0)}**")
                lines.append(f"  - РџРѕ С‡РµРєРµСЂР°Рј: `{lt_stats.get('applied_by_checker', {})}`")
            if llm_stats:
                lines.append(f"- LLM С‡Р°РЅРєРѕРІ: **{llm_stats.get('chunks', llm_stats.get('chunks', 0))}**")
                lines.append(f"- LLM С‚РѕРєРµРЅС‹: `{llm_stats.get('input_tokens', 0)} in + {llm_stats.get('output_tokens', 0)} out`")
                lines.append(f"- LLM overlap: `{llm_stats.get('overlap_paragraphs', 0)} РїР°СЂР°РіСЂР°С„РѕРІ / {llm_stats.get('overlap_chars', 0)} СЃРёРјРІРѕР»РѕРІ`")
                lines.append(f"- LLM book-memory: `{bool(llm_stats.get('book_memory'))}` (topk={llm_stats.get('memory_topk')})")
                lines.append(f"- РЎРѕРјРЅРµРЅРёР№ (doubt_words): **{llm_stats.get('doubts_count', doubts_count)}**")
            else:
                lines.append(f"- РЎРѕРјРЅРµРЅРёР№ (doubt_words): **{doubts_count}**")
            if final_path:
                lines.append(f"- РС‚РѕРіРѕРІС‹Р№ С‚РµРєСЃС‚: `{final_path.name}` ({final_chars} Р±Р°Р№С‚)")
            lines.append("")

            if natasha_report.strip():
                lines.append("## Natasha sync (РІС‹РґРµСЂР¶РєР°)")
                # РќРµ СЂР°Р·РґСѓРІР°РµРј РѕС‚С‡С‘С‚
                nat_lines = natasha_report.strip().splitlines()
                lines.extend(nat_lines[:50])
                if len(nat_lines) > 50:
                    lines.append("... (РѕР±СЂРµР·Р°РЅРѕ) ...")
                lines.append("")

            lines.append("## Р РµРєРѕРјРµРЅРґР°С†РёРё")
            lines.append("- Р”Р»СЏ РїСЂРѕР·С‹ РѕР±С‹С‡РЅРѕ Р»СѓС‡С€Рµ: `--llm-overlap-paragraphs 1` Рё `--llm-book-memory`.")
            lines.append("- Р•СЃР»Рё РєР°С‡РµСЃС‚РІРѕ РїР°РґР°РµС‚ РїРѕ РјРµСЂРµ С‚РµРєСЃС‚Р°: СѓРјРµРЅСЊС€Р°Р№С‚Рµ `--llm-chunk-size` РґРѕ 4000вЂ“5500.")
            lines.append("- Р”Р»СЏ РєРѕРЅС‚СЂРѕР»СЏ РІС‘СЂСЃС‚РєРё: РІСЃРµРіРґР° РїСЂРѕРІРµСЂСЏР№С‚Рµ EPUB РІ 2вЂ“3 С‡РёС‚Р°Р»РєР°С… + `epubcheck`.\n")

            rep_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"рџ“ќ РћС‚С‡С‘С‚ РєР°С‡РµСЃС‚РІР° СЃРѕС…СЂР°РЅС‘РЅ: {rep_path}")
        except Exception as exc:
            print(f"вљ пёЏ  РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕР·РґР°С‚СЊ quality-report: {exc}")
    
    # РџРѕРєР°Р·С‹РІР°РµРј СЃРѕР·РґР°РЅРЅС‹Рµ С„Р°Р№Р»С‹
    show_patterns = ["*.txt", "*.json", "*.epub"]
    if args.html:
        show_patterns.insert(1, "*.html")
    print("\nРЎРѕР·РґР°РЅРЅС‹Рµ С„Р°Р№Р»С‹:")
    for pattern in show_patterns:
        files = list(outdir.glob(pattern))
        if files:
            print(f"\n{pattern}:")
            for f in sorted(files):
                print(f"  - {f.name}")
    
    return 0


if __name__ == '__main__':
    exit(main())

