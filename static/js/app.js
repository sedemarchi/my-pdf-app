document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('drop-zone');
    const dropIcon = document.getElementById('drop-icon');
    const dropTitle = document.getElementById('drop-title');
    const dropSub = document.getElementById('drop-sub');
    const dropPrintBadge = document.getElementById('drop-print-badge');
    const fileInput = document.getElementById('file-input');
    const fileSelectBtn = document.getElementById('file-select-btn');
    const fileListContainer = document.getElementById('file-list-container');
    const fileList = document.getElementById('file-list');
    const fileCount = document.getElementById('file-count');
    const addMoreBtn = document.getElementById('add-more-btn');
    const clearAllBtn = document.getElementById('clear-all-btn');
    const loadingOverlay = document.getElementById('loading-overlay');
    const processBtn = document.getElementById('process-btn');
    const operationSelect = document.getElementById('operation-select');
    const outputFilenameInput = document.getElementById('output-filename-input');
    const outputFilenameBox = document.getElementById('output-filename-box');

    // Page Workbench Elements
    const pageThumbnailCanvas = document.getElementById('page-thumbnail-canvas');
    const pageCountBadge = document.getElementById('page-count-badge');
    const btnAssembleCompress = document.getElementById('btn-assemble-compress');
    const btnAssembleMerge = document.getElementById('btn-assemble-merge');
    const btnAssemblePrint = document.getElementById('btn-assemble-print');
    const btnResultBack = document.getElementById('btn-result-back');
    const thumbSizeSlider = document.getElementById('thumb-size-slider');
    const btnThumbSmaller = document.getElementById('btn-thumb-smaller');
    const btnThumbLarger = document.getElementById('btn-thumb-larger');
    const btnToggleGrid = document.getElementById('btn-toggle-grid');

    // Quick Auto-Print Panel Elements
    const quickPrintPanel = document.getElementById('quick-print-panel');
    const autoPrintToggle = document.getElementById('auto-print-toggle');
    const printModeStatus = document.getElementById('print-mode-status');
    const printerSelect = document.getElementById('printer-select');
    const printCopiesInput = document.getElementById('print-copies-input');
    const printMergeCheck = document.getElementById('print-merge-check');
    const refreshPrintersBtn = document.getElementById('refresh-printers-btn');
    const toastContainer = document.getElementById('toast-container');

    // Smart Sort Buttons
    const sortDateBtn = document.getElementById('sort-date-btn');
    const sortNameBtn = document.getElementById('sort-name-btn');
    const sortReverseBtn = document.getElementById('sort-reverse-btn');

    // Zoom Preview Modal Elements
    const previewModal = document.getElementById('preview-modal');
    const previewFilename = document.getElementById('preview-filename');
    const previewPageIndicator = document.getElementById('preview-page-indicator');
    const previewPageNumber = document.getElementById('preview-page-number');
    const previewImage = document.getElementById('preview-image');
    const previewImgContainer = document.querySelector('.preview-img-container');
    const closePreviewBtn = document.getElementById('close-preview-btn');
    const prevPageBtn = document.getElementById('prev-page-btn');
    const nextPageBtn = document.getElementById('next-page-btn');
    const zoomInBtn = document.getElementById('zoom-in-btn');
    const zoomOutBtn = document.getElementById('zoom-out-btn');
    const zoomLevelEl = document.getElementById('zoom-level');

    // Password modal elements
    const passwordModal = document.getElementById('password-modal');
    const passwordFilename = document.getElementById('password-filename');
    const modalPasswordInput = document.getElementById('modal-password-input');
    const modalCancelBtn = document.getElementById('modal-cancel-btn');
    const modalSubmitBtn = document.getElementById('modal-submit-btn');

    let files = []; // [{id, original_name, size, page_count, scan_time_str, timestamp, thumbnail_base64, pages}]
    let pages = []; // [{ page_id, file_id, page_index, page_num, thumbnail, source_name, rotation }]
    let filePasswords = {}; // { file_id: "password" }
    let currentLockedFileId = null;
    let pendingDirectPrintFiles = null; // Store files for retry if password needed during direct print
    let hasProcessed = false;
    let draggedPageIndex = null;
    let lastDroppedFileHandle = null;

    function captureDroppedFileHandle(e) {
        if (e.dataTransfer && e.dataTransfer.items) {
            for (const item of e.dataTransfer.items) {
                if (item.kind === 'file' && typeof item.getAsFileSystemHandle === 'function') {
                    item.getAsFileSystemHandle().then(handle => {
                        if (handle) {
                            lastDroppedFileHandle = handle;
                        }
                    }).catch(err => console.debug('getAsFileSystemHandle not available:', err));
                    break;
                }
            }
        }
    }

    // Preview state
    let previewFile = null;
    let previewCurrentPage = 1;
    let previewZoomLevel = 1.0;

    // Toast Notification Utility
    function showToast(message, type = 'info', duration = 4500) {
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        
        let iconHtml = '<i class="fa-solid fa-circle-info"></i>';
        if (type === 'success') iconHtml = '<i class="fa-solid fa-circle-check" style="color:#10b981;"></i>';
        else if (type === 'error') iconHtml = '<i class="fa-solid fa-triangle-exclamation" style="color:#ef4444;"></i>';
        else if (type === 'print') iconHtml = '<i class="fa-solid fa-print" style="color:#0284c7;"></i>';

        toast.innerHTML = `${iconHtml} <span>${message}</span>`;
        toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateY(15px)';
            toast.style.transition = 'all 0.3s ease';
            setTimeout(() => {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 300);
        }, duration);
    }

    // Load Printer Settings from localStorage & Server
    async function fetchPrinters() {
        if (window.location.protocol === 'file:') return;
        try {
            const res = await fetch('/api/printers');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const printers = data.printers || [];
            const defaultPrinter = data.default_printer || "";

            printerSelect.innerHTML = '';
            if (printers.length === 0) {
                const opt = document.createElement('option');
                opt.value = "";
                opt.textContent = "利用可能なプリンターが見つかりません";
                printerSelect.appendChild(opt);
                return;
            }

            const savedPrinter = localStorage.getItem('localpdf_selected_printer');

            printers.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p;
                const isDef = (p === defaultPrinter);
                opt.textContent = isDef ? `★ ${p} (既定)` : p;
                printerSelect.appendChild(opt);
            });

            // Restore selection
            if (savedPrinter && printers.includes(savedPrinter)) {
                printerSelect.value = savedPrinter;
            } else if (defaultPrinter && printers.includes(defaultPrinter)) {
                printerSelect.value = defaultPrinter;
            }
        } catch (e) {
            console.error("Failed to fetch printers", e);
            printerSelect.innerHTML = '<option value="">プリンター取得エラー</option>';
        }
    }

    if (refreshPrintersBtn) {
        refreshPrintersBtn.addEventListener('click', () => {
            fetchPrinters();
            showToast("プリンター一覧を更新しました", "info", 2000);
        });
    }

    if (printerSelect) {
        printerSelect.addEventListener('change', () => {
            localStorage.setItem('localpdf_selected_printer', printerSelect.value);
        });
    }

    if (printCopiesInput) {
        const savedCopies = localStorage.getItem('localpdf_print_copies');
        if (savedCopies) printCopiesInput.value = savedCopies;
        printCopiesInput.addEventListener('change', () => {
            localStorage.setItem('localpdf_print_copies', printCopiesInput.value);
        });
    }

    if (printMergeCheck) {
        const savedMerge = localStorage.getItem('localpdf_print_merge');
        if (savedMerge !== null) printMergeCheck.checked = (savedMerge === 'true');
        printMergeCheck.addEventListener('change', () => {
            localStorage.setItem('localpdf_print_merge', printMergeCheck.checked);
        });
    }

    // Auto-Print Toggle Mode Handling
    function updateAutoPrintUI(isOn) {
        const iconModeTool = document.getElementById('icon-mode-tool');
        const iconModePrint = document.getElementById('icon-mode-print');
        const destBadge = document.getElementById('dest-badge');
        const destIcon = document.getElementById('dest-icon');

        if (isOn) {
            if (iconModeTool) iconModeTool.classList.remove('tool-active');
            if (iconModePrint) iconModePrint.classList.add('print-active');
            if (quickPrintPanel) quickPrintPanel.classList.remove('hidden');
            if (dropZone) {
                dropZone.classList.add('print-mode');
                dropZone.title = 'PDF・画像をドロップして即時印刷（クリックで選択）';
            }
            if (destBadge) {
                destBadge.className = 'flow-badge badge-dest-print';
                destBadge.title = '即時印刷';
            }
            if (destIcon) destIcon.className = 'fa-solid fa-print';
            if (dropTitle) dropTitle.textContent = 'PDFを即印刷';
        } else {
            if (iconModeTool) iconModeTool.classList.add('tool-active');
            if (iconModePrint) iconModePrint.classList.remove('print-active');
            if (quickPrintPanel) quickPrintPanel.classList.add('hidden');
            if (dropZone) {
                dropZone.classList.remove('print-mode');
                dropZone.title = 'PDF・画像をドロップして結合・圧縮（クリックで選択）';
            }
            if (destBadge) {
                destBadge.className = 'flow-badge badge-dest-tool';
                destBadge.title = '結合・圧縮PDF';
            }
            if (destIcon) destIcon.className = 'fa-solid fa-file-pdf';
            if (dropTitle) dropTitle.textContent = 'PDFをドロップ';
        }
    }

    if (autoPrintToggle) {
        const savedAutoPrint = localStorage.getItem('localpdf_auto_print');
        const isAutoPrintOn = (savedAutoPrint === 'true');
        autoPrintToggle.checked = isAutoPrintOn;
        updateAutoPrintUI(isAutoPrintOn);

        autoPrintToggle.addEventListener('change', () => {
            const isChecked = autoPrintToggle.checked;
            localStorage.setItem('localpdf_auto_print', isChecked);
            updateAutoPrintUI(isChecked);
            if (isChecked) {
                showToast("⚡ 即時印刷ON", "print", 2000);
            } else {
                showToast("🛠️ ツールモードON", "info", 2000);
            }
        });
    }

    // Initial printer fetch
    fetchPrinters();

    // ==========================================
    // Thumbnail Zoom & Grid Layout Controls
    // ==========================================
    function setThumbnailSize(size) {
        size = Math.max(90, Math.min(280, parseInt(size, 10) || 145));
        document.documentElement.style.setProperty('--card-width', `${size}px`);
        const thumbH = Math.round(size * 0.85);
        document.documentElement.style.setProperty('--thumb-height', `${thumbH}px`);
        if (thumbSizeSlider) thumbSizeSlider.value = size;
        localStorage.setItem('localpdf_thumb_size', size);
    }

    function setGridMode(isGrid) {
        if (!pageThumbnailCanvas) return;
        if (isGrid) {
            pageThumbnailCanvas.classList.remove('strip-mode');
        } else {
            pageThumbnailCanvas.classList.add('strip-mode');
        }
        if (btnToggleGrid) {
            btnToggleGrid.classList.toggle('active-grid', isGrid);
            btnToggleGrid.title = isGrid 
                ? '表示切替: 現在グリッド展開中（クリックで1行スクロールへ）' 
                : '表示切替: 現在1行スクロール中（クリックでグリッド展開へ）';
            btnToggleGrid.innerHTML = isGrid 
                ? '<i class="fa-solid fa-table-cells-large"></i>' 
                : '<i class="fa-solid fa-arrows-left-right"></i>';
        }
        localStorage.setItem('localpdf_grid_mode', isGrid ? 'true' : 'false');
    }

    const savedThumbSize = localStorage.getItem('localpdf_thumb_size');
    if (savedThumbSize) {
        setThumbnailSize(savedThumbSize);
    } else {
        setThumbnailSize(145);
    }

    const savedGridMode = localStorage.getItem('localpdf_grid_mode');
    // Default to true (grid mode) so it immediately fills the expanded block in Google Sites!
    const isGrid = (savedGridMode === null || savedGridMode === 'true');
    setGridMode(isGrid);

    if (thumbSizeSlider) {
        thumbSizeSlider.addEventListener('input', (e) => {
            setThumbnailSize(e.target.value);
        });
    }
    if (btnThumbSmaller) {
        btnThumbSmaller.addEventListener('click', () => {
            const cur = parseInt(thumbSizeSlider ? thumbSizeSlider.value : 145, 10);
            setThumbnailSize(cur - 20);
        });
    }
    if (btnThumbLarger) {
        btnThumbLarger.addEventListener('click', () => {
            const cur = parseInt(thumbSizeSlider ? thumbSizeSlider.value : 145, 10);
            setThumbnailSize(cur + 20);
        });
    }
    if (btnToggleGrid) {
        btnToggleGrid.addEventListener('click', () => {
            const curGrid = !pageThumbnailCanvas.classList.contains('strip-mode');
            setGridMode(!curGrid);
        });
    }




    // Check if opened via file:// protocol directly
    if (window.location.protocol === 'file:') {
        const serverWarning = document.getElementById('server-warning');
        if (serverWarning) serverWarning.classList.remove('hidden');
    }

    // Check for initial files passed via sys.argv (Drag-and-Drop on icon)
    fetchInitialFiles();

    async function fetchInitialFiles() {
        if (window.location.protocol === 'file:') return;
        try {
            const res = await fetch('/api/initial-files');
            const initialFiles = await res.json();
            if (initialFiles.length > 0) {
                files = initialFiles;
                pages = [];
                initialFiles.forEach(item => {
                    if (item.pages && item.pages.length > 0) {
                        item.pages.forEach(p => {
                            pages.push({
                                page_id: p.page_id,
                                file_id: item.id,
                                page_index: p.page_index,
                                page_num: p.page_num,
                                thumbnail: p.thumbnail,
                                source_name: item.original_name,
                                rotation: 0
                            });
                        });
                    } else {
                        pages.push({
                            page_id: item.id + '_p0',
                            file_id: item.id,
                            page_index: 0,
                            page_num: 1,
                            thumbnail: item.thumbnail_base64 || '',
                            source_name: item.original_name,
                            rotation: 0
                        });
                    }
                });
                renderPageCards();
            }
        } catch (e) {
            console.error("Failed to fetch initial files", e);
        }
    }

    // Window-level Drag and Drop
    window.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    window.addEventListener('dragleave', (e) => {
        e.preventDefault();
        if (e.clientX === 0 && e.clientY === 0) {
            dropZone.classList.remove('dragover');
        }
    });

    window.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        captureDroppedFileHandle(e);
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFiles(e.dataTransfer.files);
        }
    });

    // Drop Zone Handlers
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('dragover');
        captureDroppedFileHandle(e);
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFiles(e.dataTransfer.files);
        }
    });

    fileSelectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    addMoreBtn.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFiles(e.target.files);
        }
    });

    async function handleFiles(newFiles) {
        if (hasProcessed) {
            files = [];
            pages = [];
            filePasswords = {};
            hasProcessed = false;
        }
        const resultCard = document.getElementById('process-result-card');
        if (resultCard) resultCard.classList.add('hidden');

        const newlyUploaded = [];
        showLoading(true);
        for (let i = 0; i < newFiles.length; i++) {
            const f = newFiles[i];
            const nameLower = f.name.toLowerCase();
            const validExts = ['.pdf', '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'];
            const isValid = validExts.some(ext => nameLower.endsWith(ext)) || f.type === 'application/pdf' || f.type.startsWith('image/');
            if (!isValid) {
                alert(`PDFまたは画像ファイル（JPG, PNG, WebP等）を選択してください: ${f.name}`);
                continue;
            }
            const uploadedItem = await uploadFile(f);
            if (uploadedItem) newlyUploaded.push(uploadedItem);
        }
        showLoading(false);

        // Flatten each page from uploaded files into pages array
        newlyUploaded.forEach(item => {
            if (item.pages && item.pages.length > 0) {
                item.pages.forEach(p => {
                    pages.push({
                        page_id: p.page_id,
                        file_id: item.id,
                        page_index: p.page_index,
                        page_num: p.page_num,
                        thumbnail: p.thumbnail,
                        source_name: item.original_name,
                        rotation: 0
                    });
                });
            } else {
                pages.push({
                    page_id: item.id + '_p0',
                    file_id: item.id,
                    page_index: 0,
                    page_num: 1,
                    thumbnail: item.thumbnail_base64 || '',
                    source_name: item.original_name,
                    rotation: 0
                });
            }
        });

        // Auto-print mode check
        if (autoPrintToggle && autoPrintToggle.checked && pages.length > 0) {
            await assemblePages('print');
            return;
        }

        renderPageCards();
    }

    async function uploadFile(file) {
        if (window.location.protocol === 'file:') {
            alert(`⚠️ index.html を直接ダブルクリックで開いているため通信できません。\n\n「LocalPDFTools-Start.bat」または「python main.py」でサーバーを起動し、http://127.0.0.1:8000 にアクセスしてください。`);
            return null;
        }
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            files.push(data);
            return data;
        } catch (e) {
            console.error("Upload failed", e);
            alert(`ファイル [${file.name}] のアップロードに失敗しました。\n\n理由: ${e.message}\nPythonサーバー (main.py) が起動しているか確認してください。`);
            return null;
        }
    }

    function removeAllFiles() {
        files.forEach(f => {
            fetch(`/api/files/${f.id}`, { method: 'DELETE' }).catch(() => {});
        });
        files = [];
        pages = [];
        filePasswords = {};
        hasProcessed = false;
        const resultCard = document.getElementById('process-result-card');
        if (resultCard) resultCard.classList.add('hidden');
        renderPageCards();
    }

    function movePage(index, direction) {
        const target = index + direction;
        if (target < 0 || target >= pages.length) return;
        const temp = pages[index];
        pages[index] = pages[target];
        pages[target] = temp;
        renderPageCards();
    }

    function rotatePage(index) {
        if (!pages[index]) return;
        pages[index].rotation = ((pages[index].rotation || 0) + 90) % 360;
        renderPageCards();
    }

    function deletePage(index) {
        if (index < 0 || index >= pages.length) return;
        pages.splice(index, 1);
        if (pages.length === 0) {
            removeAllFiles();
        } else {
            renderPageCards();
        }
    }

    // Render Page Thumbnail Workbench Cards
    function renderPageCards() {
        if (pages.length === 0) {
            dropZone.classList.remove('hidden');
            fileListContainer.classList.add('hidden');
            if (fileInput) fileInput.value = '';
            return;
        }

        dropZone.classList.add('hidden');
        fileListContainer.classList.remove('hidden');

        if (pageCountBadge) {
            pageCountBadge.innerHTML = `<i class="fa-solid fa-file-lines"></i> ${pages.length} P`;
        }

        if (!pageThumbnailCanvas) return;
        pageThumbnailCanvas.innerHTML = '';

        pages.forEach((page, idx) => {
            const card = document.createElement('div');
            card.className = 'page-card';
            card.draggable = true;
            card.dataset.index = idx;

            const rot = page.rotation || 0;
            const thumbContent = page.thumbnail 
                ? `<img src="${page.thumbnail}" class="page-thumb-img" style="transform: rotate(${rot}deg);" alt="Page ${idx + 1}" />` 
                : `<i class="fa-solid fa-file-pdf" style="font-size:1.8rem; color:#e5322d;"></i>`;

            card.innerHTML = `
                <div class="page-card-header">
                    <span class="page-seq-badge">#${idx + 1}</span>
                    <button type="button" class="btn-page-delete" title="このページを削除"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="page-thumb-wrapper" title="クリックで拡大表示">
                    ${thumbContent}
                </div>
                <div class="page-card-footer">
                    <button type="button" class="btn-page-move-left" ${idx === 0 ? 'disabled style="opacity:0.35;cursor:default;"' : ''} title="左へ移動"><i class="fa-solid fa-chevron-left"></i></button>
                    <button type="button" class="btn-page-rotate" title="90度回転"><i class="fa-solid fa-rotate-right"></i></button>
                    <button type="button" class="btn-page-move-right" ${idx === pages.length - 1 ? 'disabled style="opacity:0.35;cursor:default;"' : ''} title="右へ移動"><i class="fa-solid fa-chevron-right"></i></button>
                </div>
                <div class="page-source-info" title="${page.source_name} (p.${page.page_index + 1})">
                    ${page.source_name}
                </div>
            `;

            // Delete listener
            card.querySelector('.btn-page-delete').addEventListener('click', (e) => {
                e.stopPropagation();
                deletePage(idx);
            });

            // Move Left
            const btnLeft = card.querySelector('.btn-page-move-left');
            if (idx > 0) {
                btnLeft.addEventListener('click', (e) => {
                    e.stopPropagation();
                    movePage(idx, -1);
                });
            }

            // Move Right
            const btnRight = card.querySelector('.btn-page-move-right');
            if (idx < pages.length - 1) {
                btnRight.addEventListener('click', (e) => {
                    e.stopPropagation();
                    movePage(idx, 1);
                });
            }

            // Rotate
            card.querySelector('.btn-page-rotate').addEventListener('click', (e) => {
                e.stopPropagation();
                rotatePage(idx);
            });

            // Thumbnail Zoom Modal
            card.querySelector('.page-thumb-wrapper').addEventListener('click', (e) => {
                e.stopPropagation();
                const matchedFile = files.find(f => f.id === page.file_id);
                if (matchedFile) {
                    openPreviewModalForPage(matchedFile, page.page_index + 1);
                }
            });

            // HTML5 Drag & Drop Card Reordering
            card.addEventListener('dragstart', (e) => {
                draggedPageIndex = idx;
                card.classList.add('dragging');
                e.dataTransfer.setData('text/plain', idx);
                e.dataTransfer.effectAllowed = 'move';
            });

            card.addEventListener('dragend', () => {
                card.classList.remove('dragging');
                draggedPageIndex = null;
                document.querySelectorAll('.page-card').forEach(c => c.classList.remove('drag-over'));
            });

            card.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                card.classList.add('drag-over');
            });

            card.addEventListener('dragleave', () => {
                card.classList.remove('drag-over');
            });

            card.addEventListener('drop', (e) => {
                e.preventDefault();
                card.classList.remove('drag-over');
                const fromIdx = draggedPageIndex !== null ? draggedPageIndex : parseInt(e.dataTransfer.getData('text/plain'), 10);
                const toIdx = idx;
                if (!isNaN(fromIdx) && fromIdx !== toIdx) {
                    const moved = pages.splice(fromIdx, 1)[0];
                    pages.splice(toIdx, 0, moved);
                    renderPageCards();
                }
            });

            pageThumbnailCanvas.appendChild(card);
        });

        // Add / Insert Page Card at End
        const addCard = document.createElement('div');
        addCard.className = 'page-card-add';
        addCard.id = 'card-insert-page';
        addCard.title = 'クリックまたはPDF/画像をドロップしてページを追加';
        addCard.innerHTML = `
            <div class="add-card-content">
                <i class="fa-solid fa-plus-circle add-card-icon"></i>
                <span class="add-card-text">ページ挿入</span>
            </div>
        `;

        addCard.addEventListener('click', () => {
            fileInput.click();
        });

        addCard.addEventListener('dragover', (e) => {
            e.preventDefault();
            addCard.classList.add('drag-over');
        });

        addCard.addEventListener('dragleave', () => {
            addCard.classList.remove('drag-over');
        });

        addCard.addEventListener('drop', (e) => {
            e.preventDefault();
            addCard.classList.remove('drag-over');
            captureDroppedFileHandle(e);
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                handleFiles(e.dataTransfer.files);
            }
        });

        pageThumbnailCanvas.appendChild(addCard);

        updateSuggestedFilename();
    }

    // Auto-suggested Filename
    async function updateSuggestedFilename() {
        if (!outputFilenameInput || pages.length === 0) {
            if (outputFilenameInput) outputFilenameInput.value = '';
            return;
        }
        try {
            const uniqueFileIds = [...new Set(pages.map(p => p.file_id))];
            const res = await fetch('/api/suggest-filename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_ids: uniqueFileIds,
                    operation: 'merge_compress'
                })
            });
            if (res.ok) {
                const data = await res.json();
                if (data.suggested_filename) {
                    outputFilenameInput.value = data.suggested_filename;
                    return;
                }
            }
        } catch (e) {
            console.warn("Failed to suggest filename", e);
        }

        const firstName = pages[0].source_name.replace(/\.[^/.]+$/, "");
        outputFilenameInput.value = `${firstName}_COMP.pdf`;
    }

    const suggestNameBtn = document.getElementById('suggest-name-btn');
    if (suggestNameBtn) {
        suggestNameBtn.addEventListener('click', updateSuggestedFilename);
    }

    function formatSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // High-Res Zoom Preview Modal Logic
    function openPreviewModalForPage(fileItem, pageNum) {
        previewFile = fileItem;
        previewCurrentPage = pageNum || 1;
        previewZoomLevel = 1.0;
        
        previewFilename.textContent = `${fileItem.original_name} (p.${previewCurrentPage})`;
        previewModal.classList.remove('hidden');
        renderPreviewPage();
    }

    function renderPreviewPage() {
        if (!previewFile) return;
        const pageCount = previewFile.page_count || 1;
        previewPageIndicator.textContent = `${previewCurrentPage} / ${pageCount} ページ`;
        previewPageNumber.textContent = `ページ ${previewCurrentPage}`;
        
        prevPageBtn.disabled = previewCurrentPage <= 1;
        nextPageBtn.disabled = previewCurrentPage >= pageCount;

        const pwd = filePasswords[previewFile.id] || '';
        const previewUrl = `/api/page-preview/${previewFile.id}/${previewCurrentPage}${pwd ? '?password=' + encodeURIComponent(pwd) : ''}`;
        
        previewImage.src = previewUrl;
        applyZoom();
    }

    function applyZoom() {
        previewImgContainer.style.transform = `scale(${previewZoomLevel})`;
        zoomLevelEl.textContent = `${Math.round(previewZoomLevel * 100)}%`;
    }

    zoomInBtn.addEventListener('click', () => {
        if (previewZoomLevel < 2.5) {
            previewZoomLevel += 0.25;
            applyZoom();
        }
    });

    zoomOutBtn.addEventListener('click', () => {
        if (previewZoomLevel > 0.5) {
            previewZoomLevel -= 0.25;
            applyZoom();
        }
    });

    prevPageBtn.addEventListener('click', () => {
        if (previewCurrentPage > 1) {
            previewCurrentPage--;
            renderPreviewPage();
        }
    });

    nextPageBtn.addEventListener('click', () => {
        const pageCount = previewFile ? (previewFile.page_count || 1) : 1;
        if (previewCurrentPage < pageCount) {
            previewCurrentPage++;
            renderPreviewPage();
        }
    });

    function closePreview() {
        previewModal.classList.add('hidden');
        previewFile = null;
        previewImage.src = '';
    }

    closePreviewBtn.addEventListener('click', closePreview);
    previewModal.addEventListener('click', (e) => {
        if (e.target === previewModal) closePreview();
    });

    // Keyboard Shortcuts for Preview Modal & Password Modal
    window.addEventListener('keydown', (e) => {
        if (!previewModal.classList.contains('hidden')) {
            if (e.key === 'ArrowLeft') {
                prevPageBtn.click();
            } else if (e.key === 'ArrowRight') {
                nextPageBtn.click();
            } else if (e.key === 'Escape') {
                closePreview();
            }
        }
    });

    function showLoading(show) {
        if (show) loadingOverlay.classList.remove('hidden');
        else loadingOverlay.classList.add('hidden');
    }

    function showPasswordModal(filename, fileId) {
        currentLockedFileId = fileId;
        passwordFilename.textContent = filename;
        modalPasswordInput.value = '';
        passwordModal.classList.remove('hidden');
        modalPasswordInput.focus();
    }

    function hidePasswordModal() {
        passwordModal.classList.add('hidden');
        currentLockedFileId = null;
        modalPasswordInput.value = '';
    }

    modalCancelBtn.addEventListener('click', () => {
        hidePasswordModal();
        showLoading(false);
    });

    modalSubmitBtn.addEventListener('click', () => {
        submitPassword();
    });

    modalPasswordInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            submitPassword();
        }
    });

    function submitPassword() {
        const pwd = modalPasswordInput.value.trim();
        if (pwd && currentLockedFileId) {
            filePasswords[currentLockedFileId] = pwd;
            hidePasswordModal();
            assemblePages('merge_compress');
        } else {
            alert("パスワードを入力してください");
        }
    }

    async function savePdfWithPicker(downloadUrl, filename) {
        try {
            if ('showSaveFilePicker' in window) {
                const options = {
                    suggestedName: filename,
                    types: [{
                        description: 'PDF ドキュメント (*.pdf)',
                        accept: {
                            'application/pdf': ['.pdf']
                        }
                    }]
                };
                if (lastDroppedFileHandle) {
                    options.startIn = lastDroppedFileHandle;
                }
                const fileHandle = await window.showSaveFilePicker(options);
                const writable = await fileHandle.createWritable();
                const res = await fetch(downloadUrl);
                const blob = await res.blob();
                await writable.write(blob);
                await writable.close();
                showToast(`✅ 保存完了: ${fileHandle.name}`, 'success', 5000);
                return true;
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                return false;
            }
            console.warn('showSaveFilePicker fallback:', err);
        }

        // Fallback for standard browsers
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        showToast(`✅ 保存開始: ${filename}`, 'success', 4000);
        return true;
    }

    // Assemble and Execute Action (Merge Compress, Merge, Print)
    async function assemblePages(operation = 'merge_compress') {
        if (pages.length === 0) {
            alert("ページがありません。ファイルを追加してください。");
            return;
        }

        showLoading(true);
        const printerName = printerSelect ? printerSelect.value : "";
        const copies = printCopiesInput ? parseInt(printCopiesInput.value, 10) || 1 : 1;
        const customFilename = outputFilenameInput ? outputFilenameInput.value.trim() : '';

        if (operation === 'print') {
            showToast(`🖨️ ${pages.length}ページを「${printerName || '既定プリンター'}」へ印刷送信中...`, "print", 4000);
        }

        try {
            const requestBody = {
                pages: pages.map(p => ({
                    file_id: p.file_id,
                    page_index: p.page_index,
                    rotation: p.rotation || 0
                })),
                operation: operation,
                output_filename: customFilename,
                printer_name: printerName,
                copies: copies,
                passwords: filePasswords
            };

            const res = await fetch('/api/assemble', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });

            if (res.status === 422) {
                const errorData = await res.json();
                const detail = errorData.detail;
                if (detail && detail.error_code === "PASSWORD_REQUIRED") {
                    showLoading(false);
                    const matchedFile = files.find(f => f.original_name === detail.filename);
                    const fileId = matchedFile ? matchedFile.id : pages[0].file_id;
                    showPasswordModal(detail.filename, fileId);
                    return;
                }
            }

            if (!res.ok) {
                const error = await res.json();
                let msg = "処理中にエラーが発生しました";
                if (typeof error.detail === 'string') msg = error.detail;
                else if (error.detail && error.detail.message) msg = error.detail.message;
                throw new Error(msg);
            }

            const data = await res.json();

            if (operation === 'print') {
                showToast(`✅ ${data.message}`, "success", 7000);
                hasProcessed = true;
            } else {
                showProcessResultCard(data);
                hasProcessed = true;
                if (data.saved_to_source) {
                    showToast(`✅ 元のフォルダに直接保存しました:\n${data.saved_to_source}`, "success", 8000);
                } else {
                    showToast(`✅ ${data.filename} を作成しました`, "success", 4000);
                    try {
                        await savePdfWithPicker(data.download_url, data.filename);
                    } catch (pe) {
                        console.debug('Save picker auto prompt:', pe);
                    }
                }
            }

        } catch (e) {
            console.error("Assemble failed", e);
            showToast(`❌ エラー: ${e.message}`, "error", 7000);
            alert(`処理エラー: ${e.message}`);
        } finally {
            showLoading(false);
        }
    }

    // Action button listeners
    if (btnAssembleCompress) {
        btnAssembleCompress.addEventListener('click', () => assemblePages('merge_compress'));
    }
    if (btnAssembleMerge) {
        btnAssembleMerge.addEventListener('click', () => assemblePages('merge'));
    }
    if (btnAssemblePrint) {
        btnAssemblePrint.addEventListener('click', () => assemblePages('print'));
    }
    if (clearAllBtn) {
        clearAllBtn.addEventListener('click', () => removeAllFiles());
    }
    if (btnResultBack) {
        btnResultBack.addEventListener('click', () => {
            const resultCard = document.getElementById('process-result-card');
            if (resultCard) resultCard.classList.add('hidden');
            if (fileListContainer) fileListContainer.classList.remove('hidden');
        });
    }

    // Display Result Card in the widget
    function showProcessResultCard(data) {
        const resultCard = document.getElementById('process-result-card');
        const filenameDisplay = document.getElementById('result-filename-display');
        const statsDisplay = document.getElementById('result-stats-display');
        const locationDisplay = document.getElementById('result-location-display');
        const reopenPreviewBtn = document.getElementById('btn-reopen-preview');
        const resultDownloadBtn = document.getElementById('btn-result-download');
        const resultNewBtn = document.getElementById('btn-result-new');

        if (!resultCard) return;

        fileListContainer.classList.add('hidden');
        dropZone.classList.add('hidden');
        resultCard.classList.remove('hidden');

        if (filenameDisplay) filenameDisplay.textContent = data.filename;
        if (statsDisplay) {
            const pageStr = data.page_count ? `${data.page_count} ページ • ` : '';
            statsDisplay.textContent = `${pageStr}${formatSize(data.size)}`;
        }
        if (locationDisplay) {
            if (data.saved_to_source) {
                locationDisplay.textContent = `📁 保存先: ${data.saved_to_source}`;
                locationDisplay.classList.remove('hidden');
            } else {
                locationDisplay.classList.add('hidden');
            }
        }

        if (reopenPreviewBtn) {
            reopenPreviewBtn.onclick = () => {
                if (data.preview_url) window.open(data.preview_url, '_blank');
            };
        }

        if (resultDownloadBtn) {
            resultDownloadBtn.onclick = () => {
                savePdfWithPicker(data.download_url, data.filename);
            };
        }

        if (resultNewBtn) {
            resultNewBtn.onclick = () => {
                resultCard.classList.add('hidden');
                removeAllFiles();
            };
        }
    }
});
