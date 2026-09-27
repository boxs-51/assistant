// Attachment queue: local path -> { path, filename, status, progress, payload, error, chipEl }
const attachedFilesMap = new Map();

function setChipState(item, status, label = null) {
  if (!item?.chipEl) return;
  item.chipEl.classList.remove('encoding', 'ready', 'error');
  item.chipEl.classList.add(status);

  const statusEl = item.chipEl.querySelector('.chip-status');
  const progressBar = item.chipEl.querySelector('.chip-progress-bar');
  const fillEl = item.chipEl.querySelector('.chip-progress-fill');

  if (status === 'encoding') {
    if (statusEl) statusEl.innerText = label || `${item.progress || 0}%`;
    if (progressBar) progressBar.style.display = '';
    if (fillEl) fillEl.style.width = `${item.progress || 0}%`;
  } else if (status === 'ready') {
    if (statusEl) statusEl.innerText = '✓';
    if (progressBar) progressBar.style.display = 'none';
  } else if (status === 'error') {
    if (statusEl) statusEl.innerText = label || '⚠️ Thử lại';
    if (progressBar) progressBar.style.display = 'none';
  }
}

function markPreparationUnavailable(paths, onStateChange) {
  paths.forEach((path) => {
    const item = attachedFilesMap.get(path);
    if (!item) return;
    item.status = 'error';
    item.error = 'File preparation API is unavailable.';
    setChipState(item, 'error');
  });
  if (onStateChange) onStateChange();
}

function preparePaths(paths, onStateChange) {
  if (!paths.length) return;
  if (window.pywebview?.api?.prepare_files_async) {
    Promise.resolve(window.pywebview.api.prepare_files_async(paths)).catch((error) => {
      paths.forEach((path) => {
        const item = attachedFilesMap.get(path);
        if (!item) return;
        item.status = 'error';
        item.error = String(error);
        setChipState(item, 'error');
      });
      if (onStateChange) onStateChange();
    });
    return;
  }
  // Fail closed. ONLINE must never fall back to the legacy base64 encoder.
  markPreparationUnavailable(paths, onStateChange);
}

export function hasFilesEncoding() {
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'encoding') return true;
  }
  return false;
}

export function hasFileFailures() {
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'error') return true;
  }
  return false;
}

export function getFailedFilePaths() {
  const paths = [];
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'error') paths.push(item.path);
  }
  return paths;
}

export function getReadyPayloads() {
  const payloads = [];
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'ready' && item.payload) payloads.push(item.payload);
  }
  return payloads;
}

export function retryFile(filePath, onStateChange) {
  const item = attachedFilesMap.get(filePath);
  if (!item || item.status !== 'error') return;

  item.status = 'encoding';
  item.progress = 0;
  item.payload = null;
  item.error = null;
  setChipState(item, 'encoding', '0%');
  if (onStateChange) onStateChange();
  preparePaths([filePath], onStateChange);
}

export function addFilesToQueue(filePaths, onStateChange) {
  if (!filePaths || !Array.isArray(filePaths)) return;

  const newPaths = [];
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

    chip.querySelector('.remove').addEventListener('click', (event) => {
      event.stopPropagation();
      removeFileFromQueue(path, onStateChange);
    });

    chip.querySelector('.chip-status').addEventListener('click', (event) => {
      event.stopPropagation();
      retryFile(path, onStateChange);
    });

    if (wrapper) wrapper.appendChild(chip);

    attachedFilesMap.set(path, {
      path,
      filename: fileName,
      status: 'encoding',
      progress: 0,
      payload: null,
      error: null,
      chipEl: chip,
    });
    newPaths.push(path);
  });

  if (onStateChange) onStateChange();
  preparePaths(newPaths, onStateChange);
}

export function removeFileFromQueue(filePath, onStateChange) {
  const item = attachedFilesMap.get(filePath);
  if (!item) return;
  if (item.chipEl?.parentNode) item.chipEl.remove();
  attachedFilesMap.delete(filePath);
  if (onStateChange) onStateChange();
}

export function clearAllFiles() {
  attachedFilesMap.clear();
  const wrapper = document.getElementById('chips-wrapper');
  if (wrapper) wrapper.innerHTML = '';
}

export function backupFilesMap() {
  return new Map(attachedFilesMap);
}

export function restoreFilesMap(backupMap, onStateChange) {
  attachedFilesMap.clear();
  const wrapper = document.getElementById('chips-wrapper');
  if (wrapper) wrapper.innerHTML = '';

  backupMap.forEach((value, key) => {
    attachedFilesMap.set(key, value);
    if (value.chipEl && wrapper) wrapper.appendChild(value.chipEl);
  });

  if (onStateChange) onStateChange();
}

export function initFileEncoderCallbacks(onStateChange) {
  const handleProgress = (data) => {
    const item = attachedFilesMap.get(data.path);
    if (!item) return;
    item.progress = data.progress;
    setChipState(item, 'encoding');
  };

  const handleComplete = (event, legacy = false) => {
    const path = event.path;
    const item = attachedFilesMap.get(path);
    if (!item) return;

    item.status = 'ready';
    item.progress = 100;
    // Canonical ONLINE events wrap the attachment so the local path is queue-only.
    // Legacy LOCAL_OFFLINE encoder events remain unchanged for compatibility.
    item.payload = legacy ? event : event.payload;
    item.error = null;
    setChipState(item, 'ready');
    if (onStateChange) onStateChange();
  };

  const handleError = (data) => {
    const item = attachedFilesMap.get(data.path);
    if (!item) return;
    item.status = 'error';
    item.payload = null;
    item.error = data.error;
    setChipState(item, 'error');
    if (onStateChange) onStateChange();
  };

  window.onFilePrepareProgress = handleProgress;
  window.onFilePrepareComplete = (event) => handleComplete(event, false);
  window.onFilePrepareError = handleError;

  // FileEncoder owns LOCAL_OFFLINE compatibility and still emits these callbacks.
  window.onFileEncodeProgress = handleProgress;
  window.onFileEncodeComplete = (event) => handleComplete(event, true);
  window.onFileEncodeError = handleError;
}
