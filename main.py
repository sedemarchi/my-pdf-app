import os
import sys

# Critical fix for pythonw / no-console: redirect streams before importing uvicorn or logging
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None or not hasattr(sys.stderr, "write"):
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r", encoding="utf-8")

import shutil
import subprocess
import webbrowser

import threading
import time
import uuid
import zipfile
import fitz
import io
import base64
import re
from urllib.parse import quote
from datetime import datetime
from PIL import Image
from typing import List, Optional, Dict
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Global state to store initialized files from sys.argv
# and uploaded files during the session.
class PageItem(BaseModel):
    page_id: str
    file_id: str
    page_index: int
    page_num: int
    thumbnail: str
    source_name: str
    rotation: int = 0

class PageSpec(BaseModel):
    file_id: str
    page_index: int
    rotation: Optional[int] = 0

class AssembleRequest(BaseModel):
    pages: List[PageSpec]
    operation: Optional[str] = "merge_compress" # "merge_compress", "merge", "print"
    output_filename: Optional[str] = None
    passwords: Optional[Dict[str, str]] = None
    printer_name: Optional[str] = None
    copies: Optional[int] = 1

class FileItem(BaseModel):
    id: str
    original_name: str
    temp_path: str
    size: int
    page_count: int = 0
    scan_time_str: str = ""
    timestamp: float = 0.0
    thumbnail_base64: str = ""
    is_encrypted: bool = False
    pages: List[PageItem] = []
    source_dir: str = ""

class ProcessRequest(BaseModel):
    file_ids: List[str]
    operation: str
    output_filename: Optional[str] = None
    passwords: Optional[Dict[str, str]] = None
    printer_name: Optional[str] = None
    copies: Optional[int] = 1
    merge_before_print: Optional[bool] = True

class PrintRequest(BaseModel):
    file_ids: List[str]
    printer_name: Optional[str] = None
    copies: Optional[int] = 1
    merge_before_print: Optional[bool] = True
    passwords: Optional[Dict[str, str]] = None


session_files = {}
session_results = {}
primary_source_dir = ""

app = FastAPI(title="Local iLovePDF Clone")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

TEMP_DIR = os.path.join(APP_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

def parse_pdf_date_str(date_str: str) -> Optional[float]:
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.replace("D:", "").strip()
    m = re.search(r'(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?', s)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour = int(m.group(4)) if m.group(4) else 0
            minute = int(m.group(5)) if m.group(5) else 0
            second = int(m.group(6)) if m.group(6) else 0
            dt = datetime(year, month, day, hour, minute, second)
            return dt.timestamp()
        except Exception:
            pass
    return None

def get_pdf_metadata_info(temp_path: str):
    info = {
        "page_count": 0,
        "scan_time_str": "",
        "timestamp": 0.0,
        "thumbnail_base64": "",
        "is_encrypted": False
    }
    try:
        doc = fitz.open(temp_path)
        info["is_encrypted"] = doc.is_encrypted
        info["page_count"] = len(doc)
        
        meta = doc.metadata or {}
        raw_date = meta.get("creationDate") or meta.get("modDate")
        ts = parse_pdf_date_str(raw_date) if raw_date else None
        
        if not ts:
            ts = os.path.getmtime(temp_path)
            
        info["timestamp"] = ts
        dt_obj = datetime.fromtimestamp(ts)
        info["scan_time_str"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
        
        if len(doc) > 0 and not (doc.is_encrypted and not doc.authenticate("")):
            try:
                page = doc[0]
                pix = page.get_pixmap(dpi=45) # Fast 45 DPI thumbnail
                img_bytes = pix.tobytes("jpeg", jpg_quality=65)
                info["thumbnail_base64"] = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("utf-8")
            except Exception:
                pass
                
        doc.close()
    except Exception as e:
        ts = os.path.getmtime(temp_path)
        info["timestamp"] = ts
        info["scan_time_str"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        
    return info

# Endpoint to get files that were dropped on the app icon
@app.get("/api/initial-files")
def get_initial_files():
    return list(session_files.values())

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    temp_path = os.path.join(TEMP_DIR, f"{file_id}_{file.filename}")
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    size = os.path.getsize(temp_path)

    # Support image files: automatically convert JPG, PNG, WebP, BMP to 1-page PDF
    ext = os.path.splitext(file.filename)[1].lower()
    if ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'):
        converted_pdf_path = os.path.join(TEMP_DIR, f"{file_id}_img_converted.pdf")
        converted_successfully = False
        try:
            img_doc = fitz.open(temp_path)
            pdf_bytes = img_doc.convert_to_pdf()
            with open(converted_pdf_path, "wb") as f:
                f.write(pdf_bytes)
            img_doc.close()
            converted_successfully = True
        except Exception:
            try:
                img = Image.open(temp_path)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(converted_pdf_path, "PDF", resolution=100.0)
                converted_successfully = True
            except Exception as e_pil:
                print(f"Image conversion failed: {e_pil}")

        if converted_successfully and os.path.exists(converted_pdf_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
            temp_path = converted_pdf_path
            size = os.path.getsize(temp_path)

    meta_info = get_pdf_metadata_info(temp_path)
    
    pages_list = []
    try:
        doc = fitz.open(temp_path)
        if not (doc.is_encrypted and not doc.authenticate("")):
            for p_idx in range(len(doc)):
                try:
                    p = doc[p_idx]
                    pix = p.get_pixmap(dpi=100) # Crisp 100 DPI thumbnail for expandable view
                    img_bytes = pix.tobytes("jpeg", jpg_quality=82)
                    b64_str = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("utf-8")
                    pages_list.append(PageItem(
                        page_id=f"{file_id}_p{p_idx}",
                        file_id=file_id,
                        page_index=p_idx,
                        page_num=p_idx + 1,
                        thumbnail=b64_str,
                        source_name=file.filename,
                        rotation=0
                    ))
                except Exception as ep:
                    print(f"Error rendering page {p_idx}: {ep}")
        doc.close()
    except Exception as e:
        print(f"Error extracting pages from {temp_path}: {e}")

    item = FileItem(
        id=file_id,
        original_name=file.filename,
        temp_path=temp_path,
        size=size,
        page_count=meta_info["page_count"],
        scan_time_str=meta_info["scan_time_str"],
        timestamp=meta_info["timestamp"],
        thumbnail_base64=meta_info["thumbnail_base64"],
        is_encrypted=meta_info["is_encrypted"],
        pages=pages_list
    )
    session_files[file_id] = item
    return item

@app.get("/api/page-preview/{file_id}/{page_num}")
def get_page_preview(file_id: str, page_num: int, password: Optional[str] = None):
    if file_id not in session_files:
        raise HTTPException(status_code=404, detail="File not found")
    item = session_files[file_id]
    doc = open_and_unlock_pdf(item.temp_path, password=password, original_name=item.original_name)
    if page_num < 1 or page_num > len(doc):
        doc.close()
        raise HTTPException(status_code=400, detail="Invalid page number")
    
    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=150) # Crisp 150 DPI preview for checking page numbers
    img_bytes = pix.tobytes("jpeg", jpg_quality=85)
    doc.close()
    return Response(content=img_bytes, media_type="image/jpeg")

@app.delete("/api/files/{file_id}")
def delete_file(file_id: str):
    if file_id in session_files:
        item = session_files.pop(file_id)
        if os.path.exists(item.temp_path):
            os.remove(item.temp_path)
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="File not found")

def open_and_unlock_pdf(input_path: str, password: Optional[str] = None, original_name: str = "") -> fitz.Document:
    try:
        doc = fitz.open(input_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ファイルを開けませんでした: {str(e)}")

    if doc.is_encrypted:
        # 1. Try empty string / default authentication to auto-unlock read-restricted PDFs
        unlocked = doc.authenticate("")
        if not unlocked and password:
            # 2. Try user-supplied password
            unlocked = doc.authenticate(password)
        
        if not unlocked:
            doc.close()
            filename = original_name or os.path.basename(input_path)
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "PASSWORD_REQUIRED",
                    "filename": filename,
                    "message": f"ファイル '{filename}' はパスワードで保護されています。"
                }
            )
    return doc

import re

def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

def compress_fitz_doc(doc: fitz.Document, quality: int = 30, max_dim: int = 1000):
    for page in doc:
        for img in page.get_images():
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                pil_img = Image.open(io.BytesIO(image_bytes))
                if pil_img.mode in ("RGBA", "P"):
                    pil_img = pil_img.convert("RGB")
                
                if pil_img.width > max_dim or pil_img.height > max_dim:
                    pil_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                    
                out_io = io.BytesIO()
                pil_img.save(out_io, format="JPEG", quality=quality, optimize=True)
                new_image_bytes = out_io.getvalue()
                
                if len(new_image_bytes) < len(image_bytes):
                    page.replace_image(xref, stream=new_image_bytes)
            except Exception:
                continue

TARGET_SIZE_BYTES = 30 * 1024 * 1024  # 30MB

def compress_doc_to_target_size(doc: fitz.Document, output_path: str, target_bytes: int = TARGET_SIZE_BYTES):
    # Pass 1: Standard Strong Compression
    compress_fitz_doc(doc, quality=25, max_dim=1000)
    doc.save(output_path, garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
    
    current_size = os.path.getsize(output_path)
    if current_size <= target_bytes:
        return

    # Pass 2: Extra Strong Compression
    try:
        doc_retry = fitz.open(output_path)
        compress_fitz_doc(doc_retry, quality=15, max_dim=750)
        tmp_pass2 = output_path + ".pass2.pdf"
        doc_retry.save(tmp_pass2, garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
        doc_retry.close()
        
        if os.path.exists(tmp_pass2):
            if os.path.getsize(tmp_pass2) < current_size:
                shutil.move(tmp_pass2, output_path)
                current_size = os.path.getsize(output_path)
            else:
                os.remove(tmp_pass2)
    except Exception as e:
        print(f"Pass 2 compression error: {e}")

    if current_size <= target_bytes:
        return

    # Pass 3: Rasterization Fallback to strictly guarantee <= 30MB
    try:
        num_pages = len(doc)
        target_per_page = (target_bytes * 0.8) / max(num_pages, 1)
        
        dpi = 100
        jpg_q = 20
        if target_per_page < 40 * 1024:
            dpi = 80
            jpg_q = 18
        if target_per_page < 20 * 1024:
            dpi = 65
            jpg_q = 15
            
        new_doc = fitz.open()
        src_doc = fitz.open(output_path)
        for page in src_doc:
            pix = page.get_pixmap(dpi=dpi)
            img_bytes = pix.tobytes("jpeg", jpg_quality=jpg_q)
            img_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            img_page.insert_image(page.rect, stream=img_bytes)
        
        src_doc.close()
        tmp_pass3 = output_path + ".pass3.pdf"
        new_doc.save(tmp_pass3, garbage=4, deflate=True, clean=True)
        new_doc.close()
        
        if os.path.exists(tmp_pass3):
            shutil.move(tmp_pass3, output_path)
    except Exception as e:
        print(f"Pass 3 rasterization error: {e}")

def compress_pdf_file(input_path, output_path, quality=30, password=None, original_name=""):
    try:
        doc = open_and_unlock_pdf(input_path, password=password, original_name=original_name)
        compress_doc_to_target_size(doc, output_path)
        doc.close()
    except HTTPException:
        raise
    except Exception as e:
        print(f"Compress Error: {e}")
        shutil.copy2(input_path, output_path)

MAC_OCR_BIN = os.path.join(APP_DIR, "mac_ocr")

def run_mac_ocr_on_page(page: fitz.Page) -> List[str]:
    """Renders PDF page to PNG and runs native macOS Vision OCR for scanned image PDFs."""
    if not os.path.exists(MAC_OCR_BIN):
        return []
    try:
        pix = page.get_pixmap(dpi=150)
        tmp_img = os.path.join(TEMP_DIR, f"ocr_tmp_{uuid.uuid4().hex}.png")
        pix.save(tmp_img)
        
        cmd = [MAC_OCR_BIN, tmp_img]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        
        if os.path.exists(tmp_img):
            os.remove(tmp_img)
            
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return lines
    except Exception as e:
        print(f"Native OCR error: {e}")
        return []

def synthesize_smart_document_title(doc: fitz.Document, default_filename: str = "") -> str:
    """
    Analyzes cover page AND inner document content (via native PDF text or native macOS Vision OCR)
    to comprehend what the document as a whole actually is (e.g. 補助金事務マニュアル, 議事録, 報告書, 企画書, etc.)
    and synthesizes a clean, meaningful document title.
    """
    # 1. Metadata title check
    meta = doc.metadata or {}
    meta_title = meta.get("title", "").strip()
    if meta_title and len(meta_title) > 2 and not meta_title.lower().startswith("microsoft") and not meta_title.lower().endswith(".pdf") and not meta_title.lower().startswith("untitled"):
        clean_title = re.sub(r'[\/*?:"<>|]', '', meta_title).strip()
        if len(clean_title) >= 3 and not re.match(r'^(scan|img|doc)_\d+', clean_title, re.I):
            return clean_title[:45]

    # 2. Collect page text & OCR lines from up to first 3 pages
    page_texts = []
    
    for page_idx in range(min(3, len(doc))):
        try:
            page = doc[page_idx]
            raw_text = page.get_text("text").strip()
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            
            # If native text is empty or very short (< 15 chars), fallback to native macOS Vision OCR!
            if len("".join(lines)) < 15:
                ocr_lines = run_mac_ocr_on_page(page)
                if ocr_lines:
                    lines = ocr_lines
            page_texts.append(lines)
        except Exception as e:
            print(f"Error reading page {page_idx}: {e}")
            page_texts.append([])

    all_lines = []
    for lines in page_texts:
        all_lines.extend(lines)

    if not all_lines:
        base = os.path.splitext(default_filename)[0]
        base = re.sub(r'^Scan_\d{4}[-_]?\d{2}[-_]?\d{2}_?\d*', '', base, flags=re.IGNORECASE)
        base = re.sub(r'^IMG_\d+', '', base, flags=re.IGNORECASE)
        base = re.sub(r'[\/*?:"<>|]', '', base).strip("_- ")
        return base if base else "スキャン結合文書"

    # Category Keywords Mapping (Regex -> Document Category Label)
    DOC_TYPES = [
        (r'(補助金.*事務マニュアル|助成金.*事務マニュアル|補助金.*手引き|助成金.*手引き|補助金.*要綱|助成金.*要綱|公募要領|募集要領|交付要綱)', '補助金事務マニュアル'),
        (r'(補助金|助成金|交付金)', '補助金'),
        (r'(事務マニュアル|操作マニュアル|業務マニュアル|取扱説明書|マニュアル|手引き|ガイドブック|ハンドブック)', 'マニュアル'),
        (r'(議事録|会議録|定例会|幹部会|委員会|ミーティングメモ)', '議事録'),
        (r'(調査報告|実績報告|研究報告|業務報告|状況報告|報告書)', '報告書'),
        (r'(事業計画|基本計画|全体計画|実施計画|ロードマップ|計画書)', '計画書'),
        (r'(企画書|提案書|プレゼン)', '企画書'),
        (r'(要件定義|基本設計|詳細設計|仕様書|手順書)', '仕様書'),
        (r'(契約書|協定書|覚書|合意書)', '契約書'),
        (r'(決算書|財務諸表|貸借対照表|損益計算書|収支報告書|予算書)', '決算報告書'),
        (r'(請求書|見積書|納品書|領収書|発注書)', '請求書'),
        (r'(規約|規則|定款|要綱|ガイドライン)', '規約・ガイドライン'),
        (r'(アンケート|集計結果|調査結果)', '調査結果'),
        (r'(申請書|届出書|申込書|承諾書)', '申請書'),
        (r'(案内|通知|お知らせ|連絡事項)', '案内・通知')
    ]

    detected_doc_type = ""
    full_corpus = " ".join(all_lines)

    for regex_pat, type_name in DOC_TYPES:
        if re.search(regex_pat, full_corpus):
            detected_doc_type = type_name
            break

    # Noise filter helper
    def is_noise(line: str) -> bool:
        line_clean = line.strip()
        if len(line_clean) < 2:
            return True
        # Exclude dates: e.g., 2026/09/22, 令和8年9月22日, 令和8, 令和8年6月, 2026年9月
        if re.match(r'^(令和|平成)?\d{1,4}(年|\.|\/|-)?(\d{1,2}(月|\.|\/|-)?(\d{1,2}日?)?)?$', line_clean):
            return True
        # Exclude storage / system instructions
        if re.search(r'(保管用|保管してください|保存してください|提出期限|提示を求める|問合せ|TEL|FAX|http)', line_clean):
            return True
        # Exclude pure numbers, page numbers, timestamps
        if line_clean.isdigit() or re.match(r'^\d+/\d+$', line_clean) or re.match(r'^\d{1,2}:\d{2}', line_clean):
            return True
        # Exclude scan codes / generic system labels
        if re.match(r'^(scan|img|doc|no|page|\d+ページ)', line_clean, re.I):
            return True
        # Exclude generic standalone terms
        if line_clean in ("社内限り", "機密", "CONFIDENTIAL"):
            return True
        return False

    # Extract clean lines from Page 1
    page1_lines = page_texts[0] if page_texts else []
    clean_p1_lines = [re.sub(r'[\/*?:"<>|]', '', l).strip() for l in page1_lines if not is_noise(l)]

    main_topic_parts = []

    # Join meaningful title block lines on Page 1 (up to 5 lines, total max ~45 chars)
    total_len = 0
    seen = set()
    for l in clean_p1_lines[:5]:
        if l in seen:
            continue
        if total_len + len(l) > 45:
            break
        seen.add(l)
        main_topic_parts.append(l)
        total_len += len(l)

    if main_topic_parts:
        main_topic = "_".join(main_topic_parts)
    else:
        # Check Page 2 / 3 clean lines
        all_clean = []
        for p_lines in page_texts[1:]:
            for l in p_lines:
                if not is_noise(l):
                    all_clean.append(re.sub(r'[\/*?:"<>|]', '', l).strip())
        if all_clean:
            main_topic = "_".join(all_clean[:2])
        else:
            base = os.path.splitext(default_filename)[0]
            base = re.sub(r'^Scan_\d{4}[-_]?\d{2}[-_]?\d{2}_?\d*', '', base, flags=re.IGNORECASE)
            base = re.sub(r'^IMG_\d+', '', base, flags=re.IGNORECASE)
            base = re.sub(r'[\/*?:"<>|]', '', base).strip("_- ")
            main_topic = base if base else "スキャン文書"

    # Clean main_topic spacing
    main_topic = re.sub(r'\s+', '_', main_topic).strip("_")

    # Combine main_topic and detected_doc_type if detected_doc_type is not already present in main_topic
    final_title = main_topic
    if detected_doc_type:
        has_synonym = False
        for regex_pat, type_name in DOC_TYPES:
            if type_name == detected_doc_type and re.search(regex_pat, main_topic):
                has_synonym = True
                break
        if not has_synonym:
            final_title = f"{main_topic}_{detected_doc_type}"

    # Clean up formatting
    final_title = re.sub(r'[_]+', '_', final_title).strip("_")

    # Final check length limit
    if len(final_title) > 50:
        final_title = final_title[:50].rstrip("_")

    return final_title if final_title else "スキャン文書"

def suggest_merged_filename(items: List[FileItem], operation: str = "merge") -> str:
    """Proposes a clean output title based on cover page + document content (_COMP suffix appended if operation is compressed)."""
    if not items:
        return "結合文書_COMP.pdf" if operation in ("merge_compress", "compress") else "結合文書.pdf"
    
    # 1. Try comprehending document title from first item
    first_item = items[0]
    cover_title = ""
    try:
        doc = fitz.open(first_item.temp_path)
        cover_title = synthesize_smart_document_title(doc, default_filename=first_item.original_name)
        doc.close()
    except Exception as e:
        print(f"Error extracting document title: {e}")

    base_name = ""
    if cover_title and cover_title not in ("結合文書", "スキャン結合文書", "Untitled"):
        cleaned_title = re.sub(r'^Scan_\d{4}[-_]?\d{2}[-_]?\d{2}_?\d*', '', cover_title, flags=re.IGNORECASE)
        cleaned_title = re.sub(r'^IMG_\d+', '', cleaned_title, flags=re.IGNORECASE).strip("_- ")
        if len(cleaned_title) >= 2:
            base_name = cleaned_title

    # 2. Try common prefix or clean original filename
    if not base_name:
        names = []
        for item in items:
            base = os.path.splitext(item.original_name)[0]
            base = re.sub(r'^Scan_\d{4}[-_]?\d{2}[-_]?\d{2}_?\d*', '', base, flags=re.IGNORECASE)
            base = re.sub(r'^IMG_\d+', '', base, flags=re.IGNORECASE).strip("_- ")
            if base:
                names.append(base)

        if names:
            def common_prefix(strs):
                if not strs: return ""
                s1, s2 = min(strs), max(strs)
                for i, c in enumerate(s1):
                    if c != s2[i]:
                        return s1[:i]
                return s1

            prefix = common_prefix(names).rstrip("_- ")
            base_name = prefix if (prefix and len(prefix) >= 2) else names[0]

    if not base_name:
        base_name = "スキャン文書"

    # Remove any existing _COMP suffix from base_name to prevent double _COMP
    base_name = re.sub(r'_COMP$', '', base_name, flags=re.IGNORECASE)

    # Add _結合 suffix if multiple items
    suffix = "_結合" if len(items) > 1 else ""

    # Add _COMP suffix ONLY if operation is merge_compress or compress
    comp_suffix = "_COMP" if operation in ("merge_compress", "compress") else ""

    ext = ".zip" if operation in ("split", "to_jpg", "to_png") else ".pdf"

    return f"{base_name}{suffix}{comp_suffix}{ext}"

class SuggestNameRequest(BaseModel):
    file_ids: List[str]
    operation: Optional[str] = "merge"

@app.post("/api/suggest-filename")
def api_suggest_filename(request: SuggestNameRequest):
    items = [session_files.get(fid) for fid in request.file_ids if fid in session_files]
    suggested = suggest_merged_filename(items, operation=request.operation or "merge")
    return {"suggested_filename": suggested}

def get_system_printers() -> dict:
    printers = []
    default_printer = ""
    if sys.platform == "win32":
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            for p in win32print.EnumPrinters(flags):
                name = p[2]
                if name not in printers:
                    printers.append(name)
            try:
                default_printer = win32print.GetDefaultPrinter()
            except Exception:
                pass
        except Exception as e:
            print(f"Error enumerating printers on Windows: {e}")
    elif sys.platform in ("darwin", "linux"):
        try:
            res = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    m = re.match(r"^(?:printer|プリンター|プリンタ)\s+([^\s:]+)", line.strip(), re.IGNORECASE)
                    if m and m.group(1) not in printers:
                        printers.append(m.group(1))
            res_d = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
            if res_d.returncode == 0:
                m = re.search(r"(?:system default destination|宛先|送信先):\s*(\S+)", res_d.stdout, re.IGNORECASE)
                if m:
                    default_printer = m.group(1).strip()
        except Exception as e:
            print(f"Error getting Unix printers: {e}")

    if not printers and default_printer:
        printers.append(default_printer)

    return {
        "printers": printers,
        "default_printer": default_printer
    }

def print_pdf_doc_pages(doc: fitz.Document, printer_name: str = "", copies: int = 1, doc_name: str = "PDF Print Job") -> int:
    """
    Prints a fitz.Document to the specified printer.
    Returns total pages printed.
    """
    total_pages = len(doc)
    if total_pages == 0:
        return 0

    copies = max(1, min(copies, 99))

    if sys.platform == "win32":
        try:
            import win32ui
            import win32print
            import win32con
            from PIL import ImageWin

            target_printer = printer_name or win32print.GetDefaultPrinter()
            if not target_printer:
                raise ValueError("印刷先プリンターが見つかりません。")

            hDC = win32ui.CreateDC()
            hDC.CreatePrinterDC(target_printer)

            pw = hDC.GetDeviceCaps(win32con.HORZRES)
            ph = hDC.GetDeviceCaps(win32con.VERTRES)
            is_paper_landscape = (pw > ph)

            dpi_x = hDC.GetDeviceCaps(win32con.LOGPIXELSX) or 600
            dpi_y = hDC.GetDeviceCaps(win32con.LOGPIXELSY) or 600
            render_dpi = min(300, max(dpi_x, dpi_y))

            for copy_idx in range(copies):
                job_label = doc_name if copies == 1 else f"{doc_name} ({copy_idx+1}/{copies})"
                hDC.StartDoc(job_label)
                for page_num in range(total_pages):
                    hDC.StartPage()
                    page = doc[page_num]
                    page_w, page_h = page.rect.width, page.rect.height
                    page_is_landscape = (page_w > page_h)

                    rotate_deg = 90 if (page_is_landscape != is_paper_landscape) else 0

                    pix = page.get_pixmap(dpi=render_dpi, rotate=rotate_deg)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                    img_w, img_h = img.size
                    scale = min(pw / img_w, ph / img_h)
                    draw_w = int(img_w * scale)
                    draw_h = int(img_h * scale)
                    offset_x = (pw - draw_w) // 2
                    offset_y = (ph - draw_h) // 2

                    dib = ImageWin.Dib(img)
                    dib.draw(hDC.GetHandleOutput(), (offset_x, offset_y, offset_x + draw_w, offset_y + draw_h))
                    hDC.EndPage()

                hDC.EndDoc()

            return total_pages * copies

        except Exception as e:
            print(f"GDI Print error: {e}. Falling back to ShellExecute...")
            import win32api
            tmp_print_path = os.path.join(TEMP_DIR, f"print_{uuid.uuid4().hex}.pdf")
            doc.save(tmp_print_path)
            try:
                for _ in range(copies):
                    if printer_name:
                        win32api.ShellExecute(0, "printto", tmp_print_path, f'"{printer_name}"', ".", 0)
                    else:
                        win32api.ShellExecute(0, "print", tmp_print_path, None, ".", 0)
                    time.sleep(0.5)
                return total_pages * copies
            finally:
                def cleanup_delayed(path):
                    time.sleep(10)
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except Exception:
                        pass
                threading.Thread(target=cleanup_delayed, args=(tmp_print_path,), daemon=True).start()

    elif sys.platform in ("darwin", "linux"):
        tmp_print_path = os.path.join(TEMP_DIR, f"print_{uuid.uuid4().hex}.pdf")
        doc.save(tmp_print_path)
        try:
            cmd = ["lp"]
            if printer_name:
                cmd.extend(["-d", printer_name])
            if copies > 1:
                cmd.extend(["-n", str(copies)])
            cmd.append(tmp_print_path)
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return total_pages * copies
        finally:
            def cleanup_delayed(path):
                time.sleep(10)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            threading.Thread(target=cleanup_delayed, args=(tmp_print_path,), daemon=True).start()

    return 0

@app.get("/api/printers")
def api_get_printers():
    return get_system_printers()

@app.post("/api/print")
async def api_print_endpoint(request: PrintRequest):
    if not request.file_ids:
        raise HTTPException(status_code=400, detail="印刷するファイルが選択されていません。")

    items = [session_files.get(fid) for fid in request.file_ids if fid in session_files]
    if not items:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません。")

    passwords = request.passwords or {}
    printer_name = request.printer_name or ""
    copies = max(1, min(request.copies or 1, 99))
    merge_together = (request.merge_before_print is not False)
    total_printed_pages = 0

    try:
        if merge_together and len(items) > 1:
            merged_doc = fitz.open()
            for item in items:
                pwd = passwords.get(item.id)
                doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
                merged_doc.insert_pdf(doc)
                doc.close()
            job_title = f"{items[0].original_name} ほか{len(items)}件"
            pages_sent = print_pdf_doc_pages(merged_doc, printer_name=printer_name, copies=copies, doc_name=job_title)
            total_printed_pages = pages_sent
            merged_doc.close()
        else:
            for item in items:
                pwd = passwords.get(item.id)
                doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
                pages_sent = print_pdf_doc_pages(doc, printer_name=printer_name, copies=copies, doc_name=item.original_name)
                total_printed_pages += pages_sent
                doc.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"印刷処理中にエラーが発生しました: {str(e)}")

    target_display = printer_name or get_system_printers().get("default_printer") or "既定のプリンター"
    return {
        "status": "success",
        "message": f"{len(items)}件のファイル（計 {total_printed_pages} ページ）を「{target_display}」に印刷送信しました。",
        "printer": target_display,
        "files_count": len(items),
        "total_pages": total_printed_pages,
        "copies": copies
    }


@app.post("/api/process")
async def process_pdf(request: ProcessRequest):
    if not request.file_ids:
        raise HTTPException(status_code=400, detail="ファイルが選択されていません")
        
    # Maintain exact order requested by client
    items = [session_files.get(fid) for fid in request.file_ids if fid in session_files]
    if not items:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")

    output_id = str(uuid.uuid4())
    passwords = request.passwords or {}
    
    # Custom or suggested base filename
    raw_name = request.output_filename.strip() if request.output_filename else suggest_merged_filename(items)
    clean_base = re.sub(r'\.(pdf|zip)$', '', raw_name, flags=re.IGNORECASE).strip()
    
    # AT SAVE TIME: Determine whether to append _COMP strictly based on operation!
    if request.operation in ("merge_compress", "compress", "merge", "unlock"):
        if request.operation in ("merge_compress", "compress"):
            if not clean_base.upper().endswith("_COMP"):
                clean_base += "_COMP"
        else:
            clean_base = re.sub(r'_COMP$', '', clean_base, flags=re.IGNORECASE)
        out_filename = clean_base + ".pdf"
    elif request.operation in ("to_jpg", "to_png", "split"):
        clean_base = re.sub(r'_COMP$', '', clean_base, flags=re.IGNORECASE)
        out_filename = clean_base + ".zip"
    else:
        out_filename = clean_base + ".pdf"
    
    out_path = None

    if request.operation == "merge_compress":
        merged_doc = fitz.open()
        for item in items:
            pwd = passwords.get(item.id)
            doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
            merged_doc.insert_pdf(doc)
            doc.close()
        
        out_path = os.path.join(TEMP_DIR, f"{output_id}_merged_compressed.pdf")
        compress_doc_to_target_size(merged_doc, out_path)
        merged_doc.close()

    elif request.operation == "merge":
        merged_doc = fitz.open()
        for item in items:
            pwd = passwords.get(item.id)
            doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
            merged_doc.insert_pdf(doc)
            doc.close()
        out_path = os.path.join(TEMP_DIR, f"{output_id}_merged.pdf")
        merged_doc.save(out_path)
        merged_doc.close()
        
    elif request.operation == "split":
        if len(items) > 1:
            raise HTTPException(status_code=400, detail="ファイル分割は1ファイルずつ行ってください")
        item = items[0]
        pwd = passwords.get(item.id)
        doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
        zip_path = os.path.join(TEMP_DIR, f"{output_id}_split.zip")
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for page_num in range(len(doc)):
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                pdf_bytes = new_doc.write()
                new_doc.close()
                zf.writestr(f"page_{page_num+1}.pdf", pdf_bytes)
        doc.close()
        out_path = zip_path
        
    elif request.operation == "compress":
        if len(items) == 1:
            item = items[0]
            pwd = passwords.get(item.id)
            out_path = os.path.join(TEMP_DIR, f"{output_id}_compressed.pdf")
            compress_pdf_file(item.temp_path, out_path, quality=40, password=pwd, original_name=item.original_name)
        else:
            merged_doc = fitz.open()
            for item in items:
                pwd = passwords.get(item.id)
                doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
                merged_doc.insert_pdf(doc)
                doc.close()
            out_path = os.path.join(TEMP_DIR, f"{output_id}_compressed.pdf")
            compress_doc_to_target_size(merged_doc, out_path)
            merged_doc.close()
            
    elif request.operation == "unlock":
        if len(items) == 1:
            item = items[0]
            pwd = passwords.get(item.id)
            doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
            out_path = os.path.join(TEMP_DIR, f"{output_id}_unlocked.pdf")
            doc.save(out_path, clean=True)
            doc.close()
        else:
            merged_doc = fitz.open()
            for item in items:
                pwd = passwords.get(item.id)
                doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
                merged_doc.insert_pdf(doc)
                doc.close()
            out_path = os.path.join(TEMP_DIR, f"{output_id}_unlocked.pdf")
            merged_doc.save(out_path, clean=True)
            merged_doc.close()

    elif request.operation in ("to_jpg", "to_png"):
        img_format = "png" if request.operation == "to_png" else "jpeg"
        ext = "png" if request.operation == "to_png" else "jpg"
        
        total_pages = 0
        for item in items:
            pwd = passwords.get(item.id)
            doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
            total_pages += len(doc)
            doc.close()
            
        if total_pages == 1:
            item = items[0]
            pwd = passwords.get(item.id)
            doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
            base = os.path.splitext(item.original_name)[0]
            pix = doc[0].get_pixmap(dpi=200)
            out_path = os.path.join(TEMP_DIR, f"{output_id}_{base}.{ext}")
            pix.save(out_path)
            doc.close()
            out_filename = f"{base}_page_1.{ext}"
        else:
            zip_path = os.path.join(TEMP_DIR, f"{output_id}_images_{ext}.zip")
            with zipfile.ZipFile(zip_path, 'w') as zf:
                for item in items:
                    pwd = passwords.get(item.id)
                    doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
                    base = os.path.splitext(item.original_name)[0]
                    for page_num, page in enumerate(doc):
                        pix = page.get_pixmap(dpi=200)
                        img_bytes = pix.tobytes(img_format)
                        zf.writestr(f"{base}_page_{page_num+1}.{ext}", img_bytes)
                    doc.close()
            out_path = zip_path
            out_filename = f"images_{ext}.zip"

    elif request.operation == "print":
        print_req = PrintRequest(
            file_ids=request.file_ids,
            printer_name=request.printer_name,
            copies=request.copies or 1,
            merge_before_print=(request.merge_before_print is not False),
            passwords=passwords
        )
        return await api_print_endpoint(print_req)

    if not out_path or not os.path.exists(out_path):
        raise HTTPException(status_code=500, detail="出力ファイルの生成に失敗しました")

    size = os.path.getsize(out_path)
    session_results[output_id] = {
        "id": output_id,
        "path": out_path,
        "filename": out_filename,
        "size": size,
        "is_zip": out_filename.endswith(".zip"),
        "timestamp": time.time()
    }
    
    page_count = 0
    if out_filename.endswith(".pdf"):
        try:
            doc = fitz.open(out_path)
            page_count = len(doc)
            doc.close()
        except Exception:
            pass

    saved_to_source = ""
    target_dir = ""
    for item in items:
        if getattr(item, 'source_dir', '') and os.path.isdir(item.source_dir):
            target_dir = item.source_dir
            break
    if not target_dir and primary_source_dir and os.path.isdir(primary_source_dir):
        target_dir = primary_source_dir

    if target_dir:
        try:
            dest_file = os.path.join(target_dir, out_filename)
            shutil.copy2(out_path, dest_file)
            saved_to_source = dest_file
        except Exception as e:
            print(f"Could not auto-copy to source dir {target_dir}: {e}")

    return {
        "status": "success",
        "result_id": output_id,
        "filename": out_filename,
        "size": size,
        "page_count": page_count,
        "is_zip": out_filename.endswith(".zip"),
        "preview_url": f"/viewer.html?id={output_id}",
        "download_url": f"/api/download-result/{output_id}",
        "saved_to_source": saved_to_source
    }

@app.get("/api/download-result/{result_id}")
def download_result(result_id: str):
    if result_id not in session_results:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません。")
    item = session_results[result_id]
    encoded_fn = quote(item["filename"])
    return FileResponse(
        item["path"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"}
    )

@app.get("/api/view-pdf/{result_id}")
def view_pdf(result_id: str):
    if result_id not in session_results:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません。")
    item = session_results[result_id]
    encoded_fn = quote(item["filename"])
    return FileResponse(
        item["path"],
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded_fn}"}
    )

@app.get("/api/result-info/{result_id}")
def get_result_info(result_id: str):
    if result_id not in session_results:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません。")
    item = session_results[result_id]
    page_count = 0
    if not item["is_zip"]:
        try:
            doc = fitz.open(item["path"])
            page_count = len(doc)
            doc.close()
        except Exception:
            pass
    return {
        "result_id": item["id"],
        "filename": item["filename"],
        "size": item["size"],
        "is_zip": item["is_zip"],
        "page_count": page_count,
        "download_url": f"/api/download-result/{result_id}"
    }

@app.post("/api/assemble")
async def assemble_pages(request: AssembleRequest):
    if not request.pages:
        raise HTTPException(status_code=400, detail="ページが選択されていません")

    output_id = str(uuid.uuid4())
    passwords = request.passwords or {}
    out_path = None
    
    # 1. Open and unlock source documents (cached)
    opened_docs = {}
    source_items = []
    try:
        for p in request.pages:
            if p.file_id not in session_files:
                raise HTTPException(status_code=404, detail=f"ファイルが見つかりません (ID: {p.file_id})")
            if p.file_id not in opened_docs:
                item = session_files[p.file_id]
                source_items.append(item)
                pwd = passwords.get(p.file_id)
                doc = open_and_unlock_pdf(item.temp_path, password=pwd, original_name=item.original_name)
                opened_docs[p.file_id] = doc

        # 2. Assemble new document
        assembled_doc = fitz.open()
        for p in request.pages:
            src_doc = opened_docs[p.file_id]
            if p.page_index < 0 or p.page_index >= len(src_doc):
                continue
            assembled_doc.insert_pdf(src_doc, from_page=p.page_index, to_page=p.page_index)
            if p.rotation and (p.rotation % 360) != 0:
                cur_page = assembled_doc[-1]
                cur_page.set_rotation((cur_page.rotation + p.rotation) % 360)

        if len(assembled_doc) == 0:
            assembled_doc.close()
            raise HTTPException(status_code=400, detail="出力するページがありません")

        # 3. If printing
        if request.operation == "print":
            temp_print_path = os.path.join(TEMP_DIR, f"{output_id}_print.pdf")
            assembled_doc.save(temp_print_path)
            assembled_doc.close()
            try:
                printer = request.printer_name or get_default_printer_name()
                print_file_to_printer(temp_print_path, printer, copies=request.copies or 1)
                return {
                    "status": "success",
                    "message": f"{len(request.pages)}ページを「{printer or '既定プリンター'}」へ印刷送信しました"
                }
            finally:
                try:
                    if os.path.exists(temp_print_path):
                        os.remove(temp_print_path)
                except Exception:
                    pass

        # 4. Determine output filename
        raw_name = request.output_filename.strip() if request.output_filename else suggest_merged_filename(source_items, operation=request.operation or "merge_compress")
        clean_base = re.sub(r'\.(pdf|zip)$', '', raw_name, flags=re.IGNORECASE).strip()
        if request.operation in ("merge_compress", "compress"):
            if not clean_base.upper().endswith("_COMP"):
                clean_base += "_COMP"
        else:
            clean_base = re.sub(r'_COMP$', '', clean_base, flags=re.IGNORECASE)
        out_filename = clean_base + ".pdf"

        # 5. Save output file
        if request.operation in ("merge_compress", "compress"):
            out_path = os.path.join(TEMP_DIR, f"{output_id}_assembled_comp.pdf")
            compress_doc_to_target_size(assembled_doc, out_path)
        else:
            out_path = os.path.join(TEMP_DIR, f"{output_id}_assembled.pdf")
            assembled_doc.save(out_path)

        assembled_doc.close()

    finally:
        for doc in opened_docs.values():
            try:
                doc.close()
            except Exception:
                pass

    if not out_path or not os.path.exists(out_path):
        raise HTTPException(status_code=500, detail="出力ファイルの保存に失敗しました")

    size = os.path.getsize(out_path)
    page_count = len(request.pages)
    session_results[output_id] = {
        "id": output_id,
        "path": out_path,
        "filename": out_filename,
        "size": size,
        "is_zip": False,
        "timestamp": time.time()
    }

    saved_to_source = ""
    target_dir = ""
    for item in source_items:
        if getattr(item, 'source_dir', '') and os.path.isdir(item.source_dir):
            target_dir = item.source_dir
            break
    if not target_dir and primary_source_dir and os.path.isdir(primary_source_dir):
        target_dir = primary_source_dir

    if target_dir:
        try:
            dest_file = os.path.join(target_dir, out_filename)
            shutil.copy2(out_path, dest_file)
            saved_to_source = dest_file
        except Exception as e:
            print(f"Could not auto-copy to source dir {target_dir}: {e}")

    return {
        "status": "success",
        "result_id": output_id,
        "filename": out_filename,
        "size": size,
        "page_count": page_count,
        "is_zip": False,
        "preview_url": f"/viewer.html?id={output_id}",
        "download_url": f"/api/download-result/{output_id}",
        "saved_to_source": saved_to_source
    }


# Serve static files (HTML, CSS, JS)
if hasattr(sys, '_MEIPASS'):
    # When bundled by PyInstaller
    STATIC_BASE_DIR = sys._MEIPASS
else:
    STATIC_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(STATIC_BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

import socket

def cleanup_old_server_processes():
    current_pid = os.getpid()
    if sys.platform == "win32":
        try:
            res = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="ignore")
            for line in res.stdout.splitlines():
                if "127.0.0.1:8000" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = int(parts[-1])
                    if pid != current_pid and pid > 0:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        except Exception:
            pass
    else:
        try:
            res = subprocess.run(["pgrep", "-f", "python3 main.py"], capture_output=True, text=True)
            pids = [int(p.strip()) for p in res.stdout.splitlines() if p.strip().isdigit()]
            for p in pids:
                if p != current_pid:
                    try:
                        os.kill(p, 9)
                    except Exception:
                        pass
        except Exception:
            pass

def find_free_port(start_port=8000):
    cleanup_old_server_processes()
    for p in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start_port

SERVER_PORT = find_free_port(8000)

def open_browser():
    if os.environ.get("LOCALPDF_LAUNCHER") == "1":
        # Handled by LocalPDFTools.exe launcher directly
        return
    time.sleep(1.0)
    url = f"http://127.0.0.1:{SERVER_PORT}"
    
    # Try opening as standalone desktop app window (no browser tabs/address bar)
    if sys.platform == "win32":
        browser_candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for b_path in browser_candidates:
            if os.path.exists(b_path):
                try:
                    subprocess.Popen([b_path, "--new-window", f"--app={url}"])
                    return
                except Exception as e:
                    print(f"Failed to launch standalone app window: {e}")
                    break

    # Fallback to default browser
    webbrowser.open(url)



def init_app():
    global primary_source_dir
    # Process sys.argv
    args = sys.argv[1:]
    for arg in args:
        if os.path.isfile(arg):
            abs_arg = os.path.abspath(arg)
            abs_dir = os.path.dirname(abs_arg)
            if not primary_source_dir:
                primary_source_dir = abs_dir

            # Copy to temp dir
            file_id = str(uuid.uuid4())
            filename = os.path.basename(arg)
            temp_path = os.path.join(TEMP_DIR, f"{file_id}_{filename}")
            shutil.copy2(arg, temp_path)
            size = os.path.getsize(temp_path)

            ext = os.path.splitext(filename)[1].lower()
            if ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'):
                converted_pdf_path = os.path.join(TEMP_DIR, f"{file_id}_img_converted.pdf")
                converted_successfully = False
                try:
                    img_doc = fitz.open(temp_path)
                    pdf_bytes = img_doc.convert_to_pdf()
                    with open(converted_pdf_path, "wb") as f:
                        f.write(pdf_bytes)
                    img_doc.close()
                    converted_successfully = True
                except Exception:
                    try:
                        img = Image.open(temp_path)
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        img.save(converted_pdf_path, "PDF", resolution=100.0)
                        converted_successfully = True
                    except Exception as e_pil:
                        print(f"Image conversion failed: {e_pil}")
                if converted_successfully and os.path.exists(converted_pdf_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                    temp_path = converted_pdf_path
                    size = os.path.getsize(temp_path)

            meta_info = get_pdf_metadata_info(temp_path)
            pages_list = []
            try:
                doc = fitz.open(temp_path)
                if not (doc.is_encrypted and not doc.authenticate("")):
                    for p_idx in range(len(doc)):
                        try:
                            p = doc[p_idx]
                            pix = p.get_pixmap(dpi=100)
                            img_bytes = pix.tobytes("jpeg", jpg_quality=82)
                            b64_str = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("utf-8")
                            pages_list.append(PageItem(
                                page_id=f"{file_id}_p{p_idx}",
                                file_id=file_id,
                                page_index=p_idx,
                                page_num=p_idx + 1,
                                thumbnail=b64_str,
                                source_name=filename,
                                rotation=0
                            ))
                        except Exception as ep:
                            print(f"Error rendering page {p_idx}: {ep}")
                doc.close()
            except Exception as e:
                print(f"Error extracting pages from {temp_path}: {e}")

            item = FileItem(
                id=file_id,
                original_name=filename,
                temp_path=temp_path,
                size=size,
                page_count=meta_info["page_count"],
                scan_time_str=meta_info["scan_time_str"],
                timestamp=meta_info["timestamp"],
                thumbnail_base64=meta_info["thumbnail_base64"],
                is_encrypted=meta_info["is_encrypted"],
                pages=pages_list,
                source_dir=abs_dir
            )
            session_files[file_id] = item

if __name__ == "__main__":
    try:
        # Fix for PyInstaller / pythonw --noconsole (sys.stdout/stderr is None)
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")

        init_app()
        # Start browser in a background thread
        threading.Thread(target=open_browser, daemon=True).start()
        # Run server on dynamic free port
        uvicorn.run(app, host="127.0.0.1", port=SERVER_PORT, log_config=None)
    except Exception as e:

        with open(os.path.join(APP_DIR, "launch_error.log"), "a", encoding="utf-8") as f:
            import traceback
            f.write(f"Launch Exception: {e}\n{traceback.format_exc()}\n")
