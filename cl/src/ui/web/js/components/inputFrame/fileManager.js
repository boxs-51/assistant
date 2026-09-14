// Kho lưu trữ quản lý trạng thái file đính kèm: path -> { path, filename, status, progress, payload, chipEl }
const attachedFilesMap = new Map();

/**
 * Kiểm tra xem có file nào đang mã hóa hay không
 */
export function hasFilesEncoding() {
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'encoding') return true;
  }
  return false;
}

/**
 * Lấy danh sách payload file đã encode thành công
 */
export function getReadyPayloads() {
  const payloads = [];
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'ready' && item.payload) {
      payloads.push(item.payload);
    }
  }
  return payloads;
}

/**
 * Thêm file chip và đăng ký mã hóa async với Python
 */
export function addFilesToQueue(filePaths, onStateChange) {
  if (!filePaths || !Array.isArray(filePaths)) return;

  const newPathsToEncode = [];
  const wrapper = document.getElementById('chips-wrapper');

  filePaths.forEach((path) => {
    if (attachedFilesMap.has(path)) return;

    const fileName = path.split(/[\\/]/).pop();

    const chip = document.createElement('div');
    chip.className = 'chip encoding';
    chip.dataset.path = path;
    chip.innerHTML = `
      <span class="chip-icon">📄</span>
      <span class="chip-name" title="${fileName}">${fileName}</span>
      <span class="chip-status">0%</span>
      <div class="chip-progress-bar"><div class="chip-progress-fill" style="width: 0%"></div></div>
      <span class="remove" title="Xóa">✕</span>
    `;

    chip.querySelector('.remove').addEventListener('click', (e) => {
      e.stopPropagation();
      removeFileFromQueue(path, onStateChange);
    });

    if (wrapper) wrapper.appendChild(chip);

    attachedFilesMap.set(path, {
      path,
      filename: fileName,
      status: 'encoding',
      progress: 0,
      payload: null,
      chipEl: chip,
    });

    newPathsToEncode.push(path);
  });

  if (onStateChange) onStateChange();

  if (newPathsToEncode.length > 0 && window.pywebview?.api?.encode_files_async) {
    window.pywebview.api.encode_files_async(newPathsToEncode);
  }
}

/**
 * Xóa file khỏi hàng chờ
 */
export function removeFileFromQueue(filePath, onStateChange) {
  const item = attachedFilesMap.get(filePath);
  if (item) {
    if (item.chipEl && item.chipEl.parentNode) {
      item.chipEl.remove();
    }
    attachedFilesMap.delete(filePath);
    if (onStateChange) onStateChange();
  }
}

/**
 * Xóa sạch danh sách file đính kèm
 */
export function clearAllFiles() {
  attachedFilesMap.clear();
  const wrapper = document.getElementById('chips-wrapper');
  if (wrapper) wrapper.innerHTML = '';
}

/**
 * Backup và Restore trạng thái khi gửi lỗi
 */
export function backupFilesMap() {
  return new Map(attachedFilesMap);
}

export function restoreFilesMap(backupMap, onStateChange) {
  attachedFilesMap.clear();
  const wrapper = document.getElementById('chips-wrapper');
  if (wrapper) wrapper.innerHTML = '';

  backupMap.forEach((val, key) => {
    attachedFilesMap.set(key, val);
    if (val.chipEl && wrapper) wrapper.appendChild(val.chipEl);
  });

  if (onStateChange) onStateChange();
}

/**
 * Đăng ký callback toàn cục từ Python Bridge
 */
export function initFileEncoderCallbacks(onStateChange) {
  window.onFileEncodeProgress = function (data) {
    const item = attachedFilesMap.get(data.path);
    if (!item) return;

    item.progress = data.progress;
    if (item.chipEl) {
      const statusEl = item.chipEl.querySelector('.chip-status');
      const fillEl = item.chipEl.querySelector('.chip-progress-fill');
      if (statusEl) statusEl.innerText = `${data.progress}%`;
      if (fillEl) fillEl.style.width = `${data.progress}%`;
    }
  };

  window.onFileEncodeComplete = function (payload) {
    const item = attachedFilesMap.get(payload.path);
    if (!item) return;

    item.status = 'ready';
    item.payload = payload;

    if (item.chipEl) {
      item.chipEl.classList.remove('encoding', 'error');
      item.chipEl.classList.add('ready');

      const statusEl = item.chipEl.querySelector('.chip-status');
      const progressBar = item.chipEl.querySelector('.chip-progress-bar');
      if (statusEl) statusEl.innerText = '✓';
      if (progressBar) progressBar.style.display = 'none';
    }

    if (onStateChange) onStateChange();
  };

  window.onFileEncodeError = function (data) {
    const item = attachedFilesMap.get(data.path);
    if (!item) return;

    item.status = 'error';
    item.error = data.error;

    if (item.chipEl) {
      item.chipEl.classList.remove('encoding', 'ready');
      item.chipEl.classList.add('error');

      const statusEl = item.chipEl.querySelector('.chip-status');
      if (statusEl) statusEl.innerText = '⚠️ Lỗi';
    }

    if (onStateChange) onStateChange();
  };
}