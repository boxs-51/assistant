// js/components/inputFrame.js

const MULTILINE_LINE_THRESHOLD = 6;
const TRIGGERS = ['@', '#'];

let canvasCtx = null;
let isExpanded = false;

// Kho lưu trữ quản lý trạng thái file đính kèm: path -> { path, filename, status, progress, payload, chipEl }
const attachedFilesMap = new Map();

// Trạng thái hệ thống đang bận (AI đang phản hồi)
let isSystemBusy = false;

/**
 * Kiểm tra phần tử có đang hiển thị thực tế trên màn hình hay không
 */
function isElementVisible(elem) {
  return !!(elem && (elem.offsetWidth || elem.offsetHeight || elem.getClientRects().length));
}

/**
 * Đo độ rộng thực tế (px) của đoạn văn bản dựa trên font của Textarea
 */
function measureTextWidth(text, tx) {
  if (!text) return 0;
  if (!canvasCtx) {
    const canvas = document.createElement('canvas');
    canvasCtx = canvas.getContext('2d');
  }
  const computed = window.getComputedStyle(tx);
  canvasCtx.font = `${computed.fontWeight || '400'} ${computed.fontSize || '14px'} ${computed.fontFamily || 'sans-serif'}`;
  return canvasCtx.measureText(text).width;
}

/**
 * Tính toán chiều rộng tối đa mà ô Input có thể chiếm ở chế độ Inline
 */
function getAvailableInlineWidth(inputMainArea, tx) {
  const mainWidth = inputMainArea.clientWidth;
  if (!mainWidth) return 200;

  const actionLeft = inputMainArea.querySelector('.action-left');
  const actionRight = inputMainArea.querySelector('.action-right');
  const actionCenter = inputMainArea.querySelector('.action-center');

  const leftW = actionLeft ? actionLeft.offsetWidth : 0;
  const rightW = actionRight ? actionRight.offsetWidth : 0;
  const centerW = actionCenter ? actionCenter.offsetWidth : 0;

  const computedTx = window.getComputedStyle(tx);
  const txPadding = (parseFloat(computedTx.paddingLeft) || 0) + (parseFloat(computedTx.paddingRight) || 0);

  return Math.max(50, mainWidth - leftW - rightW - centerW - txPadding - 24);
}

/**
 * Cập nhật trạng thái nút Gửi dựa trên tiến trình mã hóa file & trạng thái AI
 */
function updateSendButtonState() {
  const btnSend = document.getElementById('btn-send');
  if (!btnSend) return;

  if (isSystemBusy) {
    btnSend.disabled = true;
    btnSend.innerText = 'Gửi';
    return;
  }

  // Kiểm tra xem có file nào đang trong trạng thái mã hóa không
  let isEncoding = false;
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'encoding') {
      isEncoding = true;
      break;
    }
  }

  if (isEncoding) {
    btnSend.disabled = true;
    btnSend.innerText = 'Đang xử lý...';
  } else {
    btnSend.disabled = false;
    btnSend.innerText = 'Gửi';
  }
}

/**
 * Thêm file chip và đăng ký mã hóa async với Python
 */
export function addFilesToQueue(filePaths) {
  if (!filePaths || !Array.isArray(filePaths)) return;

  const newPathsToEncode = [];

  filePaths.forEach((path) => {
    if (attachedFilesMap.has(path)) return; // Bỏ qua file trùng

    const fileName = path.split(/[\\/]/).pop();
    const wrapper = document.getElementById('chips-wrapper');

    // Tạo phần tử Chip UI
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

    // Sự kiện xóa file
    chip.querySelector('.remove').addEventListener('click', (e) => {
      e.stopPropagation();
      removeFileFromQueue(path);
    });

    wrapper.appendChild(chip);

    // Thêm vào Map lưu trữ
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

  updateSendButtonState();

  // Gọi API Python bắt đầu mã hóa bất đồng bộ các file mới
  if (newPathsToEncode.length > 0 && window.pywebview?.api?.encode_files_async) {
    window.pywebview.api.encode_files_async(newPathsToEncode);
  }
}

/**
 * Xóa file khỏi hàng chờ
 */
export function removeFileFromQueue(filePath) {
  const item = attachedFilesMap.get(filePath);
  if (item) {
    if (item.chipEl && item.chipEl.parentNode) {
      item.chipEl.remove();
    }
    attachedFilesMap.delete(filePath);
    updateSendButtonState();
  }
}

// -----------------------------------------------------------------
// DĂNG KÝ CALLBACK TỪ PYTHON (pywebview)
// -----------------------------------------------------------------
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

  updateSendButtonState();
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

  updateSendButtonState();
};

// -----------------------------------------------------------------
// KHỞI TẠO KHUNG INPUT
// -----------------------------------------------------------------
export function initInputFrame(onSubmit) {
  const tx = document.getElementById('user-input');
  const btnSend = document.getElementById('btn-send');
  const btnAttach = document.getElementById('btn-attach');
  const inputMainArea = document.getElementById('input-main-area');
  const btnExpand = document.getElementById('btn-expand-input');
  const container = document.getElementById('input-container');

  const updateInputLayout = () => {
    if (isExpanded || !isElementVisible(tx)) return;

    const text = tx.value;
    const hasNewline = text.includes('\n');
    const textWidth = measureTextWidth(text, tx);
    const availableInlineW = getAvailableInlineWidth(inputMainArea, tx);

    const shouldSplitLayout = hasNewline || textWidth >= availableInlineW - 10;

    if (shouldSplitLayout) {
      inputMainArea.classList.add('multiline');
    } else {
      inputMainArea.classList.remove('multiline');
    }

    const computed = window.getComputedStyle(tx);
    const paddingTop = parseFloat(computed.paddingTop) || 0;
    const paddingBottom = parseFloat(computed.paddingBottom) || 0;
    let lineHeight = parseFloat(computed.lineHeight);
    if (isNaN(lineHeight) || lineHeight === 0) {
      lineHeight = (parseFloat(computed.fontSize) || 14) * 1.5;
    }

    const paddingTotal = paddingTop + paddingBottom;
    const maxNaturalHeight = Math.ceil(lineHeight * MULTILINE_LINE_THRESHOLD + paddingTotal);

    const savedScrollTop = tx.scrollTop;
    tx.style.height = 'auto';
    const currentScrollHeight = tx.scrollHeight;

    if (currentScrollHeight > maxNaturalHeight) {
      tx.style.height = `${maxNaturalHeight}px`;
      tx.style.overflowY = 'auto';
      tx.scrollTop = savedScrollTop;
      btnExpand.classList.remove('hidden');
    } else {
      tx.style.height = `${currentScrollHeight}px`;
      tx.style.overflowY = 'hidden';
      btnExpand.classList.add('hidden');
    }
  };

  tx.addEventListener('input', updateInputLayout);

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            updateInputLayout();
          }
        });
      },
      { threshold: 0.1 }
    );
    observer.observe(container);
  }

  btnExpand.addEventListener('click', () => {
    isExpanded = !isExpanded;
    if (isExpanded) {
      container.classList.add('expanded');
      btnExpand.innerText = '⤡';
      btnExpand.title = 'Thu gọn';
    } else {
      container.classList.remove('expanded');
      btnExpand.innerText = '⤢';
      btnExpand.title = 'Mở rộng khung nhập liệu';
      updateInputLayout();
    }
  });

  tx.addEventListener('keyup', (e) => {
    if (!isElementVisible(tx)) return;
    handleMentionDetection(tx, e, (type, query) => {
      console.log(`[Mention Detected] Type: ${type}, Query: "${query}"`);
    });
  });

  tx.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(onSubmit);
    }
  });

  btnSend.addEventListener('click', () => handleSend(onSubmit));

  btnAttach.addEventListener('click', async () => {
    if (window.pywebview?.api?.open_file_picker) {
      try {
        const files = await window.pywebview.api.open_file_picker();
        if (files && files.length > 0) {
          addFilesToQueue(files);
        }
      } catch (err) {
        console.error('Lỗi mở File Picker:', err);
      }
    } else {
      alert('Tính năng chọn file chưa sẵn sàng hoặc đang chạy trên trình duyệt web!');
    }
  });

  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length > 0) {
      const droppedPaths = Array.from(e.dataTransfer.files)
        .map((f) => f.path)
        .filter(Boolean);
      if (droppedPaths.length > 0) {
        addFilesToQueue(droppedPaths);
      }
    }
  });
}

function handleMentionDetection(textarea, event, callback) {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter'].includes(event.key)) return;

  const cursorPosition = textarea.selectionStart;
  const textBeforeCursor = textarea.value.substring(0, cursorPosition);
  const words = textBeforeCursor.split(/\s+/);
  const currentWord = words[words.length - 1];

  if (!currentWord) return;

  for (const trigger of TRIGGERS) {
    if (currentWord.startsWith(trigger)) {
      callback(trigger, currentWord.substring(trigger.length));
      return;
    }
  }
}

async function handleSend(onSubmit) {
  const tx = document.getElementById('user-input');
  const inputMainArea = document.getElementById('input-main-area');
  const btnExpand = document.getElementById('btn-expand-input');

  const text = tx.value.trim();

  // Kiểm tra có file nào chưa hoàn tất mã hóa không
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'encoding') {
      alert('Vui lòng chờ các file hoàn tất mã hóa Base64!');
      return;
    }
  }

  // Thu thập danh sách payload đã encode xong
  const filePayloads = [];
  for (const item of attachedFilesMap.values()) {
    if (item.status === 'ready' && item.payload) {
      filePayloads.push(item.payload);
    }
  }

  if (!text && filePayloads.length === 0) return;

  const currentText = text;
  const savedFilesMapBackup = new Map(attachedFilesMap);

  // Reset UI
  tx.value = '';
  tx.style.height = 'auto';
  inputMainArea.classList.remove('multiline');
  btnExpand.classList.add('hidden');
  attachedFilesMap.clear();
  document.getElementById('chips-wrapper').innerHTML = '';
  updateSendButtonState();

  try {
    // Truyền dữ liệu payload sang submit_prompt
    await onSubmit(currentText, filePayloads);
  } catch (err) {
    console.error('Lỗi khi gửi dữ liệu:', err);
    // Phục hồi lại nếu lỗi
    tx.value = currentText;
    savedFilesMapBackup.forEach((val, key) => attachedFilesMap.set(key, val));
    // Redraw chips
    const wrapper = document.getElementById('chips-wrapper');
    attachedFilesMap.forEach((item) => {
      if (item.chipEl) wrapper.appendChild(item.chipEl);
    });
    updateSendButtonState();
    tx.dispatchEvent(new Event('input'));
  }
}

export function setInputState(enabled) {
  isSystemBusy = !enabled;
  document.getElementById('user-input').disabled = !enabled;
  document.getElementById('btn-attach').disabled = !enabled;
  updateSendButtonState();
}