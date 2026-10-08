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
    const loadingOverlay = document.getElementById('loading-overlay');
    const processBtn = document.getElementById('process-btn');
    const operationSelect = document.getElementById('operation-select');
    const outputFilenameInput = document.getElementById('output-filename-input');
    const outputFilenameBox = document.getElementById('output-filename-box');

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

    let files = []; // [{id, original_name, size, page_count, scan_time_str, timestamp, thumbnail_base64}]
    let filePasswords = {}; // { file_id: "password" }
    let currentLockedFileId = null;
    let pendingDirectPrintFiles = null; // Store files for retry if password needed during direct print
    let hasProcessed = false; // Tracks if previous operation completed (for auto-clearing next drop)

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
        if (isOn) {
            if (quickPrintPanel) quickPrintPanel.classList.add('active');
            if (printModeStatus) {
                printModeStatus.className = 'badge-status badge-on';
                printModeStatus.textContent = 'ON';
            }
            if (dropZone) dropZone.classList.add('print-mode');
            if (dropIcon) dropIcon.className = 'fa-solid fa-print drop-icon';
            if (dropTitle) dropTitle.textContent = 'PDFをドロップして即時印刷';
            if (dropPrintBadge) dropPrintBadge.classList.remove('hidden');
        } else {
            if (quickPrintPanel) quickPrintPanel.classList.remove('active');
            if (printModeStatus) {
                printModeStatus.className = 'badge-status badge-off';
                printModeStatus.textContent = 'OFF';
            }
            if (dropZone) dropZone.classList.remove('print-mode');
            if (dropIcon) dropIcon.className = 'fa-solid fa-cloud-arrow-up drop-icon';
            if (dropTitle) dropTitle.textContent = 'ここにPDFファイルをドロップ';
            if (dropPrintBadge) dropPrintBadge.classList.add('hidden');
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
                showToast("⚡ ドロップ即時印刷モードが有効になりました。PDFを投げ込むと直ちに印刷されます。", "print", 4000);
            } else {
                showToast("ドロップ即時印刷モードをOFFにしました。", "info", 2500);
            }
        });
    }

    // Initial printer fetch
    fetchPrinters();

    // Navigation buttons handle
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const op = btn.getAttribute('data-op');
            if (op && operationSelect) {
                operationSelect.value = op;
                navBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                handleOperationChange();
            }
        });
    });

    function handleOperationChange() {
        const op = operationSelect ? operationSelect.value : 'merge_compress';
        // Sync active nav button
        navBtns.forEach(b => {
            if (b.getAttribute('data-op') === op) b.classList.add('active');
            else b.classList.remove('active');
        });

        if (op === 'print') {
            if (outputFilenameBox) outputFilenameBox.style.display = 'none';
            if (processBtn) processBtn.innerHTML = '🖨️ 印刷を実行 <i class="fa-solid fa-arrow-right"></i>';
        } else {
            if (outputFilenameBox) outputFilenameBox.style.display = '';
            if (processBtn) processBtn.innerHTML = '処理を開始 <i class="fa-solid fa-arrow-right"></i>';
            updateSuggestedFilename();
        }
    }

    if (operationSelect) {
        operationSelect.addEventListener('change', handleOperationChange);
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
                updateUI();
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
        // If previous job was completed, auto-clear old files for a clean slate
        if (hasProcessed) {
            files = [];
            filePasswords = {};
            hasProcessed = false;
        }

        const newlyUploaded = [];
        showLoading(true);
        for (let i = 0; i < newFiles.length; i++) {
            const f = newFiles[i];
            const nameLower = f.name.toLowerCase();
            if (f.type !== 'application/pdf' && !nameLower.endsWith('.pdf')) {
                alert(`PDFファイルのみ対応しています: ${f.name}`);
                continue;
            }
            const uploadedItem = await uploadFile(f);
            if (uploadedItem) newlyUploaded.push(uploadedItem);
        }
        showLoading(false);
        
        // Auto-print check: If autoPrintToggle is ON, immediately print!
        if (autoPrintToggle && autoPrintToggle.checked && newlyUploaded.length > 0) {
            await executeDirectPrint(newlyUploaded);
            return;
        }

        // Auto-sort by scan time on new batch arrival
        sortFilesByDate();
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

    async function executeDirectPrint(batchFiles) {
        if (!batchFiles || batchFiles.length === 0) return;
        pendingDirectPrintFiles = batchFiles;
        
        showLoading(true);
        const printerName = printerSelect ? printerSelect.value : "";
        const copies = printCopiesInput ? parseInt(printCopiesInput.value, 10) || 1 : 1;
        const mergeBeforePrint = printMergeCheck ? printMergeCheck.checked : true;

        showToast(`🖨️ ${batchFiles.length}件のPDFを「${printerName || '既定プリンター'}」へ印刷送信中...`, "print", 4000);

        try {
            const requestBody = {
                file_ids: batchFiles.map(f => f.id),
                printer_name: printerName,
                copies: copies,
                merge_before_print: mergeBeforePrint,
                passwords: filePasswords
            };

            const res = await fetch('/api/print', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });

            if (res.status === 422) {
                const errorData = await res.json();
                const detail = errorData.detail;
                if (detail && detail.error_code === "PASSWORD_REQUIRED") {
                    showLoading(false);
                    const matchedFile = batchFiles.find(f => f.original_name === detail.filename);
                    const fileId = matchedFile ? matchedFile.id : batchFiles[0].id;
                    showPasswordModal(detail.filename, fileId);
                    return;
                }
            }

            if (!res.ok) {
                const error = await res.json();
                let msg = "印刷中にエラーが発生しました";
                if (typeof error.detail === 'string') msg = error.detail;
                else if (error.detail && error.detail.message) msg = error.detail.message;
                throw new Error(msg);
            }

            const data = await res.json();
            showToast(`✅ ${data.message}`, "success", 7000);

            // Clear files to be ready for next drop
            files = [];
            filePasswords = {};
            pendingDirectPrintFiles = null;
            updateUI();

        } catch (e) {
            console.error("Direct print failed", e);
            showToast(`❌ 印刷失敗: ${e.message}`, "error", 7000);
            alert(`印刷エラー: ${e.message}`);
        } finally {
            showLoading(false);
        }
    }


    async function removeFile(id) {
        try {
            await fetch(`/api/files/${id}`, { method: 'DELETE' });
            files = files.filter(f => f.id !== id);
            delete filePasswords[id];
            updateUI();
        } catch (e) {
            console.error("Delete failed", e);
        }
    }

    function removeAllFiles() {
        files.forEach(f => {
            fetch(`/api/files/${f.id}`, { method: 'DELETE' }).catch(() => {});
        });
        files = [];
        filePasswords = {};
        hasProcessed = false;
        updateUI();
    }

    function moveFile(index, direction) {
        const newIndex = index + direction;
        if (newIndex < 0 || newIndex >= files.length) return;
        const temp = files[index];
        files[index] = files[newIndex];
        files[newIndex] = temp;
        updateUI();
    }

    // Smart Sort implementations
    function sortFilesByDate() {
        files.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
        updateUI();
    }

    function sortFilesByName() {
        files.sort((a, b) => a.original_name.localeCompare(b.original_name, undefined, { numeric: true, sensitivity: 'base' }));
        updateUI();
    }

    function reverseFiles() {
        files.reverse();
        updateUI();
    }

    if (sortDateBtn) sortDateBtn.addEventListener('click', sortFilesByDate);
    if (sortNameBtn) sortNameBtn.addEventListener('click', sortFilesByName);
    if (sortReverseBtn) sortReverseBtn.addEventListener('click', reverseFiles);

    const suggestNameBtn = document.getElementById('suggest-name-btn');

    async function updateSuggestedFilename() {
        if (!outputFilenameInput || files.length === 0) {
            if (outputFilenameInput) outputFilenameInput.value = '';
            return;
        }
        const currentOp = operationSelect ? operationSelect.value : 'merge';
        try {
            const res = await fetch('/api/suggest-filename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_ids: files.map(f => f.id),
                    operation: currentOp
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
            console.error("Failed to suggest filename from server", e);
        }
        outputFilenameInput.value = suggestOutputFilenameFallback();
    }

    if (suggestNameBtn) {
        suggestNameBtn.addEventListener('click', updateSuggestedFilename);
    }

    function suggestOutputFilenameFallback() {
        if (files.length === 0) return '';
        const names = files.map(f => f.original_name.replace(/\.[^/.]+$/, ""));
        let prefix = names[0];
        for (let i = 1; i < names.length; i++) {
            while (!names[i].startsWith(prefix) && prefix.length > 0) {
                prefix = prefix.substring(0, prefix.length - 1);
            }
        }
        prefix = prefix.trim().replace(/[_-]+$/, "");
        let base = prefix && prefix.length >= 2 ? prefix : names[0];
        base = base.replace(/_COMP$/i, "");
        let suffix = files.length > 1 ? "_結合" : "";
        const op = operationSelect ? operationSelect.value : 'merge';
        let compSuffix = (op === 'merge_compress' || op === 'compress') ? "_COMP" : "";
        const ext = (op === 'to_jpg' || op === 'to_png' || op === 'split') ? '.zip' : '.pdf';
        return base + suffix + compSuffix + ext;
    }

    operationSelect.addEventListener('change', updateSuggestedFilename);

    function formatSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function updateUI() {
        if (files.length > 0) {
            dropZone.classList.add('hidden');
            fileListContainer.classList.remove('hidden');
            
            fileCount.innerHTML = `${files.length} ファイル <button id="clear-all-btn" class="btn btn-sm btn-danger-outline" style="margin-left:8px;" title="全てのファイルを消去"><i class="fa-solid fa-trash-can"></i> 全消去</button>`;
            document.getElementById('clear-all-btn').addEventListener('click', removeAllFiles);

            fileList.innerHTML = '';
            
            files.forEach((f, idx) => {
                const card = document.createElement('div');
                card.className = 'file-card';
                card.draggable = true;
                card.dataset.index = idx;

                const hasPassword = !!filePasswords[f.id];
                const pageCountStr = f.page_count ? `全 ${f.page_count} ページ` : 'PDF';
                const scanTimeStr = f.scan_time_str ? `🕒 ${f.scan_time_str}` : '';

                const thumbContent = f.thumbnail_base64 
                    ? `<img src="${f.thumbnail_base64}" class="thumb-img" alt="Thumbnail" />` 
                    : `<i class="fa-solid ${hasPassword ? 'fa-file-circle-check' : 'fa-file-pdf'}" style="font-size:3rem; color:#e5322d;"></i>`;

                card.innerHTML = `
                    <button class="remove-file" data-id="${f.id}" title="削除"><i class="fa-solid fa-xmark"></i></button>
                    <div class="file-thumbnail-container" data-id="${f.id}">
                        ${thumbContent}
                        <div class="thumb-zoom-overlay"><i class="fa-solid fa-magnifying-glass-plus"></i> 拡大して確認</div>
                        <span class="page-badge">${pageCountStr}</span>
                    </div>
                    <div class="file-name" title="${f.original_name}">${f.original_name}</div>
                    <div class="file-size">${formatSize(f.size)} ${hasPassword ? '🔑PW済' : ''}</div>
                    ${scanTimeStr ? `<div class="scan-time-badge" title="スキャン/作成日時">${scanTimeStr}</div>` : ''}
                    <div class="card-controls">
                        <div class="reorder-btns">
                            <button class="btn-nano move-left-btn" title="左へ移動" ${idx === 0 ? 'disabled' : ''}><i class="fa-solid fa-chevron-left"></i></button>
                            <button class="btn-nano move-right-btn" title="右へ移動" ${idx === files.length - 1 ? 'disabled' : ''}><i class="fa-solid fa-chevron-right"></i></button>
                        </div>
                        <span style="font-size:0.75rem; color:#888;">#${idx + 1}</span>
                    </div>
                `;

                // Remove button listener
                card.querySelector('.remove-file').addEventListener('click', (e) => {
                    e.stopPropagation();
                    removeFile(f.id);
                });

                // Thumbnail click -> Open Zoom Modal
                card.querySelector('.file-thumbnail-container').addEventListener('click', (e) => {
                    e.stopPropagation();
                    openPreviewModal(f);
                });

                // Reorder button listeners
                card.querySelector('.move-left-btn').addEventListener('click', (e) => {
                    e.stopPropagation();
                    moveFile(idx, -1);
                });

                card.querySelector('.move-right-btn').addEventListener('click', (e) => {
                    e.stopPropagation();
                    moveFile(idx, 1);
                });

                // Drag & Drop Card Reordering
                card.addEventListener('dragstart', (e) => {
                    card.classList.add('dragging');
                    e.dataTransfer.setData('text/plain', idx);
                });

                card.addEventListener('dragend', () => {
                    card.classList.remove('dragging');
                });

                card.addEventListener('dragover', (e) => {
                    e.preventDefault();
                });

                card.addEventListener('drop', (e) => {
                    e.preventDefault();
                    const fromIdx = parseInt(e.dataTransfer.getData('text/plain'), 10);
                    const toIdx = idx;
                    if (!isNaN(fromIdx) && fromIdx !== toIdx) {
                        const moved = files.splice(fromIdx, 1)[0];
                        files.splice(toIdx, 0, moved);
                        updateUI();
                    }
                });

                fileList.appendChild(card);
            });

            updateSuggestedFilename();
        } else {
            dropZone.classList.remove('hidden');
            fileListContainer.classList.add('hidden');
            fileInput.value = '';
        }
    }

    // High-Res Zoom Preview Modal Logic
    function openPreviewModal(fileItem) {
        previewFile = fileItem;
        previewCurrentPage = 1;
        previewZoomLevel = 1.0;
        
        previewFilename.textContent = fileItem.original_name;
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
            updateUI();
            if (pendingDirectPrintFiles && pendingDirectPrintFiles.length > 0) {
                executeDirectPrint(pendingDirectPrintFiles);
            } else {
                executeProcess();
            }
        } else {
            alert("パスワードを入力してください");
        }
    }

    processBtn.addEventListener('click', () => {
        if (files.length === 0) {
            alert("ファイルを追加してください。");
            return;
        }
        
        const operation = operationSelect.value;
        if (operation === 'split' && files.length > 1) {
            alert("分割機能は1ファイルずつしか処理できません。");
            return;
        }
        
        executeProcess();
    });

    async function executeProcess() {
        showLoading(true);
        const operation = operationSelect.value;
        const customFilename = outputFilenameInput ? outputFilenameInput.value.trim() : '';

        // Handling Print operation directly
        if (operation === 'print') {
            const printerName = printerSelect ? printerSelect.value : "";
            const copies = printCopiesInput ? parseInt(printCopiesInput.value, 10) || 1 : 1;
            const mergeBeforePrint = printMergeCheck ? printMergeCheck.checked : true;

            showToast(`🖨️ ${files.length}件のPDFを「${printerName || '既定プリンター'}」へ印刷送信中...`, "print", 4000);

            try {
                const requestBody = {
                    file_ids: files.map(f => f.id),
                    printer_name: printerName,
                    copies: copies,
                    merge_before_print: mergeBeforePrint,
                    passwords: filePasswords
                };

                const res = await fetch('/api/print', {
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
                        const fileId = matchedFile ? matchedFile.id : files[0].id;
                        showPasswordModal(detail.filename, fileId);
                        return;
                    }
                }

                if (!res.ok) {
                    const error = await res.json();
                    let msg = "印刷中にエラーが発生しました";
                    if (typeof error.detail === 'string') msg = error.detail;
                    else if (error.detail && error.detail.message) msg = error.detail.message;
                    throw new Error(msg);
                }

                const data = await res.json();
                showToast(`✅ ${data.message}`, "success", 7000);
                hasProcessed = true;

            } catch (e) {
                console.error("Print execution failed", e);
                showToast(`❌ 印刷失敗: ${e.message}`, "error", 7000);
                alert(`印刷エラー: ${e.message}`);
            } finally {
                showLoading(false);
            }
            return;
        }
        
        try {
            const requestBody = {
                file_ids: files.map(f => f.id),
                operation: operation,
                output_filename: customFilename,
                passwords: filePasswords
            };
            
            const res = await fetch('/api/process', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });
            
            if (res.status === 422) {
                const errorData = await res.json();
                const detail = errorData.detail;
                if (detail && detail.error_code === "PASSWORD_REQUIRED") {
                    showLoading(false);
                    const matchedFile = files.find(f => f.original_name === detail.filename);
                    const fileId = matchedFile ? matchedFile.id : files[0].id;
                    showPasswordModal(detail.filename, fileId);
                    return;
                }
            }
            
            if (!res.ok) {
                const error = await res.json();
                let msg = "エラーが発生しました";
                if (typeof error.detail === 'string') {
                    msg = error.detail;
                } else if (error.detail && error.detail.message) {
                    msg = error.detail.message;
                }
                throw new Error(msg);
            }
            
            // Handle file download - Always prioritize Content-Disposition header filename!
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            
            const contentDisposition = res.headers.get('content-disposition');
            let filename = '';
            if (contentDisposition) {
                const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
                const normalMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
                if (utf8Match && utf8Match[1]) {
                    filename = decodeURIComponent(utf8Match[1]);
                } else if (normalMatch && normalMatch[1]) {
                    filename = decodeURIComponent(normalMatch[1]);
                }
            }
            if (!filename) {
                filename = customFilename || 'result.pdf';
            }
            
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            // Mark as processed so next drop auto-clears old list
            hasProcessed = true;
            
        } catch (e) {
            alert(`処理エラー: ${e.message}`);
        } finally {
            showLoading(false);
        }
    }
});
