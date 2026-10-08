import io
import os
import zipfile
import fitz  # PyMuPDF
from PIL import Image
import streamlit as st

# ==========================================
# ページ基本設定（Google Sites iframe 最適化）
# ==========================================
st.set_page_config(
    page_title="Local iLovePDF | PDF万能ツール",
    page_icon="📑",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Google Sites iframe 埋め込み用のCSSチューニング
# 余計な余白、Streamlitヘッダー・フッターを非表示にし、スマートな見た目にします
st.markdown(
    """
    <style>
    /* Streamlit標準のヘッダー・フッター・メニューを非表示 */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 上下の余白を詰めてiframe内でコンパクトに収める */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 2rem !important;
        padding-left: 1.2rem !important;
        padding-right: 1.2rem !important;
        max-width: 840px;
    }
    
    /* アプリヘッダー */
    .header-box {
        text-align: center;
        padding: 1.2rem 1rem;
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border-radius: 12px;
        color: white;
        margin-bottom: 1.2rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .header-title {
        font-size: 1.6rem;
        font-weight: 700;
        margin-bottom: 0.3rem;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
    }
    .header-desc {
        font-size: 0.88rem;
        color: #94a3b8;
        margin: 0;
    }
    
    /* アップローダーとボタンのスタイル改善 */
    .stDownloadButton button, .stButton button {
        border-radius: 8px;
        font-weight: 600;
    }
    
    /* 統計カード */
    .stat-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 10px 14px;
        margin: 8px 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==========================================
# ユーティリティ関数
# ==========================================
def format_size(size_bytes: int) -> str:
    """バイト数を人間が読みやすい形式に変換"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def compress_fitz_doc(doc: fitz.Document, quality: int = 35, max_dim: int = 1000):
    """PDF内の埋め込み画像を圧縮・リサイズ"""
    for page in doc:
        for img in page.get_images():
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                pil_img = Image.open(io.BytesIO(image_bytes))
                
                if pil_img.mode in ("RGBA", "P"):
                    pil_img = pil_img.convert("RGB")
                
                # 指定最大解像度を超えている場合は縮小
                if pil_img.width > max_dim or pil_img.height > max_dim:
                    pil_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                
                out_io = io.BytesIO()
                pil_img.save(out_io, format="JPEG", quality=quality, optimize=True)
                new_image_bytes = out_io.getvalue()
                
                # 元より小さくなった場合のみ置換
                if len(new_image_bytes) < len(image_bytes):
                    page.replace_image(xref, stream=new_image_bytes)
            except Exception:
                continue

def get_pdf_bytes_compressed(doc: fitz.Document, quality: int, max_dim: int) -> bytes:
    """画像を圧縮し、PDF構造をデフレートしてバイト列を返す"""
    compress_fitz_doc(doc, quality=quality, max_dim=max_dim)
    return doc.tobytes(garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)

# ==========================================
# 画面ヘッダー
# ==========================================
st.markdown(
    """
    <div class="header-box">
        <div class="header-title">📑 Local iLovePDF <span style="font-size:0.9rem; background:#4f46e5; padding:2px 8px; border-radius:12px;">Web版</span></div>
        <p class="header-desc">結合・圧縮・分割・画像化・パスワード解除をブラウザ上で高速処理します。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ==========================================
# 機能選択タブ
# ==========================================
tab_merge, tab_compress, tab_merge_comp, tab_split, tab_img, tab_unlock = st.tabs(
    [
        "📑 PDF結合",
        "🗜️ PDF圧縮",
        "📑🗜️ 結合＆圧縮",
        "✂️ PDF分割",
        "🖼️ 画像変換",
        "🔓 解除",
    ]
)

# ----------------------------------------------------
# 1. PDF結合
# ----------------------------------------------------
with tab_merge:
    st.subheader("📑 複数PDFの結合")
    st.caption("2つ以上のPDFファイルをドラッグ＆ドロップしてください。指定順に1つのPDFに結合します。")
    
    files = st.file_uploader(
        "PDFファイルを選択 (複数可)",
        type=["pdf"],
        accept_multiple_files=True,
        key="merge_uploader",
    )
    
    if files:
        st.write(f"📁 選択中: **{len(files)} 件のファイル**")
        with st.expander("ファイル一覧と順序の確認", expanded=False):
            for i, f in enumerate(files, 1):
                st.write(f"{i}. **{f.name}** ({format_size(f.size)})")
        
        custom_name = st.text_input("出力ファイル名", value="merged.pdf", key="merge_outname")
        if not custom_name.lower().endswith(".pdf"):
            custom_name += ".pdf"
            
        if len(files) < 2:
            st.info("💡 結合するには2つ以上のPDFファイルを選択してください。")
        else:
            if st.button("🚀 結合を実行する", type="primary", use_container_width=True, key="btn_merge"):
                with st.spinner("PDFを結合しています..."):
                    try:
                        merged_doc = fitz.open()
                        total_pages = 0
                        for f in files:
                            doc = fitz.open(stream=f.getvalue(), filetype="pdf")
                            if doc.is_encrypted:
                                st.error(f"⚠️ 「{f.name}」はパスワード保護されているため結合できません。「解除」タブで保護を解除してください。")
                                doc.close()
                                merged_doc.close()
                                st.stop()
                            merged_doc.insert_pdf(doc)
                            total_pages += len(doc)
                            doc.close()
                        
                        out_bytes = merged_doc.tobytes(garbage=4, deflate=True)
                        merged_doc.close()
                        
                        st.success(f"✅ 結合が完了しました！（全 {total_pages} ページ / {format_size(len(out_bytes))}）")
                        st.download_button(
                            label=f"📥 結合済みPDFをダウンロード ({custom_name})",
                            data=out_bytes,
                            file_name=custom_name,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True,
                        )
                    except Exception as e:
                        st.error(f"結合処理中にエラーが発生しました: {e}")

# ----------------------------------------------------
# 2. PDF圧縮
# ----------------------------------------------------
with tab_compress:
    st.subheader("🗜️ PDFのファイルサイズ圧縮")
    st.caption("画質を維持しながらPDF内部の画像や不要データを軽量化します。複数ファイルの一括圧縮にも対応しています。")
    
    comp_files = st.file_uploader(
        "圧縮したいPDFファイルを選択",
        type=["pdf"],
        accept_multiple_files=True,
        key="comp_uploader",
    )
    
    col1, col2 = st.columns(2)
    with col1:
        comp_level = st.selectbox(
            "圧縮レベル",
            ["標準圧縮（おすすめ・十分軽量）", "強力圧縮（画像サイズ縮小）", "極小圧縮（ファイルサイズ最優先）"],
            index=0,
            key="comp_level_select",
        )
    with col2:
        if comp_level.startswith("標準"):
            quality, max_dim = 35, 1200
        elif comp_level.startswith("強力"):
            quality, max_dim = 20, 900
        else:
            quality, max_dim = 15, 600
        st.caption(f"設定: JPEG品質 {quality}%, 最大解像度 {max_dim}px")

    if comp_files:
        if st.button("🚀 圧縮を実行する", type="primary", use_container_width=True, key="btn_compress"):
            with st.spinner("PDFを圧縮中..."):
                try:
                    if len(comp_files) == 1:
                        f = comp_files[0]
                        orig_bytes = f.getvalue()
                        doc = fitz.open(stream=orig_bytes, filetype="pdf")
                        if doc.is_encrypted:
                            st.error(f"⚠️ 「{f.name}」はパスワード保護されています。先に「解除」タブで解除してください。")
                            doc.close()
                            st.stop()
                        
                        compressed_bytes = get_pdf_bytes_compressed(doc, quality, max_dim)
                        doc.close()
                        
                        # 圧縮結果が元より大きくなってしまった場合は元を使用
                        final_bytes = compressed_bytes if len(compressed_bytes) < len(orig_bytes) else orig_bytes
                        savings = len(orig_bytes) - len(final_bytes)
                        pct = (savings / len(orig_bytes)) * 100 if len(orig_bytes) > 0 else 0
                        
                        base_name, ext = os.path.splitext(f.name)
                        out_name = f"{base_name}_COMP.pdf"
                        
                        st.success("✅ 圧縮が完了しました！")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("元サイズ", format_size(len(orig_bytes)))
                        c2.metric("圧縮後サイズ", format_size(len(final_bytes)))
                        c3.metric("削減率", f"-{pct:.1f}%", delta=f"-{format_size(savings)}")
                        
                        st.download_button(
                            label=f"📥 圧縮PDFをダウンロード ({out_name})",
                            data=final_bytes,
                            file_name=out_name,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True,
                        )
                    else:
                        # 複数ファイルはZIPでまとめて提供
                        zip_buffer = io.BytesIO()
                        total_orig = 0
                        total_comp = 0
                        
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                            for f in comp_files:
                                orig_b = f.getvalue()
                                total_orig += len(orig_b)
                                doc = fitz.open(stream=orig_b, filetype="pdf")
                                if doc.is_encrypted:
                                    zf.writestr(f.name, orig_b)
                                    total_comp += len(orig_b)
                                    doc.close()
                                    continue
                                comp_b = get_pdf_bytes_compressed(doc, quality, max_dim)
                                doc.close()
                                final_b = comp_b if len(comp_b) < len(orig_b) else orig_b
                                total_comp += len(final_b)
                                
                                base_name, _ = os.path.splitext(f.name)
                                zf.writestr(f"{base_name}_COMP.pdf", final_b)
                                
                        zip_data = zip_buffer.getvalue()
                        savings = total_orig - total_comp
                        pct = (savings / total_orig) * 100 if total_orig > 0 else 0
                        
                        st.success(f"✅ {len(comp_files)} 件のファイルを一括圧縮しました！")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("合計元サイズ", format_size(total_orig))
                        c2.metric("合計圧縮後", format_size(total_comp))
                        c3.metric("削減率", f"-{pct:.1f}%", delta=f"-{format_size(savings)}")
                        
                        st.download_button(
                            label=f"📥 圧縮済みZIPをダウンロード (compressed_files.zip)",
                            data=zip_data,
                            file_name="compressed_files.zip",
                            mime="application/zip",
                            type="primary",
                            use_container_width=True,
                        )
                except Exception as e:
                    st.error(f"圧縮処理中にエラーが発生しました: {e}")

# ----------------------------------------------------
# 3. 結合＆圧縮
# ----------------------------------------------------
with tab_merge_comp:
    st.subheader("📑🗜️ PDF結合 ＆ 一括軽量化")
    st.caption("複数ファイルを1つに結合し、自動でファイルサイズを圧縮してダウンロードします。")
    
    mc_files = st.file_uploader(
        "結合＆圧縮したいPDFを選択 (複数可)",
        type=["pdf"],
        accept_multiple_files=True,
        key="merge_comp_uploader",
    )
    
    if mc_files:
        mc_custom_name = st.text_input("出力ファイル名", value="merged_COMP.pdf", key="mc_outname")
        if not mc_custom_name.lower().endswith(".pdf"):
            mc_custom_name += ".pdf"
            
        if len(mc_files) < 2:
            st.info("💡 結合するには2つ以上のPDFファイルを選択してください。")
        else:
            if st.button("🚀 結合＆圧縮を実行する", type="primary", use_container_width=True, key="btn_mc"):
                with st.spinner("結合および圧縮を実行中..."):
                    try:
                        merged_doc = fitz.open()
                        total_orig_size = 0
                        for f in mc_files:
                            fb = f.getvalue()
                            total_orig_size += len(fb)
                            doc = fitz.open(stream=fb, filetype="pdf")
                            if doc.is_encrypted:
                                st.error(f"⚠️ 「{f.name}」はパスワード保護されています。")
                                doc.close()
                                merged_doc.close()
                                st.stop()
                            merged_doc.insert_pdf(doc)
                            doc.close()
                            
                        # 圧縮処理
                        compress_fitz_doc(merged_doc, quality=30, max_dim=1000)
                        out_bytes = merged_doc.tobytes(garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
                        merged_doc.close()
                        
                        savings = total_orig_size - len(out_bytes)
                        pct = (savings / total_orig_size) * 100 if total_orig_size > 0 else 0
                        
                        st.success(f"✅ 結合＆圧縮が完了しました！")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("結合前 合計", format_size(total_orig_size))
                        c2.metric("結合圧縮後", format_size(len(out_bytes)))
                        c3.metric("削減率", f"-{pct:.1f}%", delta=f"-{format_size(savings)}")
                        
                        st.download_button(
                            label=f"📥 結合圧縮済みPDFをダウンロード ({mc_custom_name})",
                            data=out_bytes,
                            file_name=mc_custom_name,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True,
                        )
                    except Exception as e:
                        st.error(f"結合＆圧縮中にエラーが発生しました: {e}")

# ----------------------------------------------------
# 4. PDF分割
# ----------------------------------------------------
with tab_split:
    st.subheader("✂️ PDFの分割・抽出")
    st.caption("1つのPDFをページごとに個別のPDFに分割、または指定ページのみを抽出します。")
    
    split_file = st.file_uploader(
        "分割したいPDFを選択",
        type=["pdf"],
        accept_multiple_files=False,
        key="split_uploader",
    )
    
    if split_file:
        try:
            doc = fitz.open(stream=split_file.getvalue(), filetype="pdf")
            if doc.is_encrypted:
                st.error("⚠️ このPDFはパスワード保護されています。「解除」タブで保護を解除してください。")
                doc.close()
                st.stop()
            
            page_count = len(doc)
            st.info(f"📄 ファイル名: **{split_file.name}** （全 **{page_count}** ページ）")
            
            split_mode = st.radio(
                "分割方法",
                ["全ページを個別のPDFに分割（ZIP保存）", "指定ページ範囲を抽出"],
                horizontal=True,
                key="split_mode_radio",
            )
            
            extract_range = ""
            if split_mode == "指定ページ範囲を抽出":
                extract_range = st.text_input(
                    "抽出するページ (例: 1-3, 5)",
                    value="1",
                    help="カンマ区切りで個別ページやハイフンで範囲を指定できます（1始まり）",
                )
                
            if st.button("🚀 分割・抽出を実行する", type="primary", use_container_width=True, key="btn_split"):
                with st.spinner("処理を実行中..."):
                    base_name, _ = os.path.splitext(split_file.name)
                    
                    if split_mode.startswith("全ページ"):
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                            for p_num in range(page_count):
                                new_doc = fitz.open()
                                new_doc.insert_pdf(doc, from_page=p_num, to_page=p_num)
                                p_bytes = new_doc.tobytes(garbage=4, deflate=True)
                                new_doc.close()
                                zf.writestr(f"{base_name}_p{p_num+1:03d}.pdf", p_bytes)
                                
                        doc.close()
                        zip_data = zip_buffer.getvalue()
                        st.success(f"✅ 全 {page_count} ページを個別PDFに分割しました！")
                        st.download_button(
                            label=f"📥 分割PDFアーカイブをダウンロード ({base_name}_split.zip)",
                            data=zip_data,
                            file_name=f"{base_name}_split.zip",
                            mime="application/zip",
                            type="primary",
                            use_container_width=True,
                        )
                    else:
                        # ページ番号パース
                        pages_to_extract = set()
                        parts = extract_range.split(",")
                        for part in parts:
                            part = part.strip()
                            if "-" in part:
                                s, e = part.split("-", 1)
                                if s.isdigit() and e.isdigit():
                                    for p in range(int(s), int(e) + 1):
                                        if 1 <= p <= page_count:
                                            pages_to_extract.add(p - 1)
                            elif part.isdigit():
                                p = int(part)
                                if 1 <= p <= page_count:
                                    pages_to_extract.add(p - 1)
                                    
                        sorted_pages = sorted(list(pages_to_extract))
                        if not sorted_pages:
                            st.warning("有効なページ番号が指定されていません。")
                            doc.close()
                        else:
                            new_doc = fitz.open()
                            for p_idx in sorted_pages:
                                new_doc.insert_pdf(doc, from_page=p_idx, to_page=p_idx)
                            out_bytes = new_doc.tobytes(garbage=4, deflate=True)
                            new_doc.close()
                            doc.close()
                            
                            st.success(f"✅ 指定された {len(sorted_pages)} ページを抽出しました！")
                            st.download_button(
                                label=f"📥 抽出したPDFをダウンロード ({base_name}_extracted.pdf)",
                                data=out_bytes,
                                file_name=f"{base_name}_extracted.pdf",
                                mime="application/pdf",
                                type="primary",
                                use_container_width=True,
                            )
        except Exception as e:
            st.error(f"分割処理中にエラーが発生しました: {e}")

# ----------------------------------------------------
# 5. PDFから画像変換
# ----------------------------------------------------
with tab_img:
    st.subheader("🖼️ PDFを画像（JPG / PNG）に変換")
    st.caption("PDFの各ページを高画質な画像に変換します。1ページの場合は画像単体、複数ページはZIPでダウンロードできます。")
    
    img_file = st.file_uploader(
        "画像に変換したいPDFを選択",
        type=["pdf"],
        accept_multiple_files=False,
        key="img_uploader",
    )
    
    if img_file:
        col_fmt, col_dpi = st.columns(2)
        with col_fmt:
            img_format = st.selectbox("画像フォーマット", ["PNG (劣化なし・文字が鮮明)", "JPG (写真向き・容量小)"], index=0)
            is_png = img_format.startswith("PNG")
            ext = "png" if is_png else "jpg"
            save_format = "png" if is_png else "jpeg"
        with col_dpi:
            dpi_choice = st.selectbox("解像度 (DPI)", ["150 DPI (標準・Web閲覧向け)", "200 DPI (高解像度・おすすめ)", "300 DPI (印刷品質・高精細)"], index=1)
            dpi = int(dpi_choice.split()[0])
            
        if st.button("🚀 画像に変換する", type="primary", use_container_width=True, key="btn_to_img"):
            with st.spinner("PDFを画像に変換中..."):
                try:
                    doc = fitz.open(stream=img_file.getvalue(), filetype="pdf")
                    if doc.is_encrypted:
                        st.error("⚠️ このPDFは保護されています。「解除」タブで保護を解除してください。")
                        doc.close()
                        st.stop()
                        
                    total_pages = len(doc)
                    base_name, _ = os.path.splitext(img_file.name)
                    
                    if total_pages == 1:
                        # 単一ページは直接画像でダウンロード
                        pix = doc[0].get_pixmap(dpi=dpi)
                        img_bytes = pix.tobytes(save_format)
                        doc.close()
                        
                        st.success("✅ 画像変換が完了しました！")
                        st.image(img_bytes, caption=f"{base_name}_p1.{ext}", width=300)
                        st.download_button(
                            label=f"📥 画像をダウンロード ({base_name}_p1.{ext})",
                            data=img_bytes,
                            file_name=f"{base_name}_p1.{ext}",
                            mime=f"image/{ext}",
                            type="primary",
                            use_container_width=True,
                        )
                    else:
                        # 複数ページはZIPにパック
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                            for idx, page in enumerate(doc):
                                pix = page.get_pixmap(dpi=dpi)
                                img_bytes = pix.tobytes(save_format)
                                zf.writestr(f"{base_name}_p{idx+1:03d}.{ext}", img_bytes)
                        doc.close()
                        zip_data = zip_buffer.getvalue()
                        
                        st.success(f"✅ 全 {total_pages} ページの画像変換が完了しました！")
                        st.download_button(
                            label=f"📥 画像ZIPをダウンロード ({base_name}_images_{ext}.zip)",
                            data=zip_data,
                            file_name=f"{base_name}_images_{ext}.zip",
                            mime="application/zip",
                            type="primary",
                            use_container_width=True,
                        )
                except Exception as e:
                    st.error(f"画像変換中にエラーが発生しました: {e}")

# ----------------------------------------------------
# 6. パスワード解除
# ----------------------------------------------------
with tab_unlock:
    st.subheader("🔓 PDFパスワード解除")
    st.caption("パスワードで保護されているPDFの暗号化を解除し、パスワード不要で開けるPDFとして保存します。")
    
    unlock_file = st.file_uploader(
        "パスワード解除したいPDFを選択",
        type=["pdf"],
        accept_multiple_files=False,
        key="unlock_uploader",
    )
    
    if unlock_file:
        pwd = st.text_input("設定されているパスワードを入力してください", type="password", key="unlock_pwd")
        if st.button("🚀 保護を解除する", type="primary", use_container_width=True, key="btn_unlock"):
            if not pwd:
                st.warning("パスワードを入力してください。")
            else:
                with st.spinner("パスワードを検証・解除中..."):
                    try:
                        doc = fitz.open(stream=unlock_file.getvalue(), filetype="pdf")
                        if not doc.is_encrypted:
                            st.info("ℹ️ このPDFはもともとパスワード保護されていません。")
                            doc.close()
                        else:
                            rc = doc.authenticate(pwd)
                            if rc > 0:
                                out_bytes = doc.tobytes(garbage=4, deflate=True, clean=True)
                                doc.close()
                                base_name, _ = os.path.splitext(unlock_file.name)
                                out_name = f"{base_name}_unlocked.pdf"
                                
                                st.success("✅ パスワードの解除に成功しました！")
                                st.download_button(
                                    label=f"📥 保護解除済みPDFをダウンロード ({out_name})",
                                    data=out_bytes,
                                    file_name=out_name,
                                    mime="application/pdf",
                                    type="primary",
                                    use_container_width=True,
                                )
                            else:
                                doc.close()
                                st.error("❌ パスワードが正しくありません。再確認してください。")
                    except Exception as e:
                        st.error(f"解除処理中にエラーが発生しました: {e}")
