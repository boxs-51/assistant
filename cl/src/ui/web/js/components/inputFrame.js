import {
  hasFilesEncoding,
  getReadyPayloads,
  getFailedFilePaths,
  addFilesToQueue,
  clearAllFiles,
  backupFilesMap,
  restoreFilesMap,
  initFileEncoderCallbacks,
} from './inputFrame/fileManager.js';

import {
  isElementVisible,
  updateInputLayout,
  toggleExpand,
} from './inputFrame/inputLayout.js';

import { handleMentionDetection } from './inputFrame/mentionDetector.js';

let isSystemBusy = false;

/**
 * Cập nhật trạng thái nút Gửi
 */
export function updateSendButtonState() {
  const btnSend = document.getElementById('btn-send');
  if (!btnSend) return;

  if (isSystemBusy) {
    btnSend.disabled = true;
    btnSend.innerText = 'Gửi';
    return;
  }

  if (hasFilesEncoding()) {
    btnSend.disabled = true;
    btnSend.innerText = 'Đang xử lý...';
  } else {
    btnSend.disabled = false;
    btnSend.innerText = 'Gửi';
  }
}

/**
 * Khởi tạo khung nhập liệu và gắn các listener
 */
export function initInputFrame(onSubmit) {
  const tx = document.getElementById('user-input');
  const btnSend = document.getElementById('btn-send');
  const btnAttach = document.getElementById('btn-attach');
  const inputMainArea = document.getElementById('input-main-area');
  const btnExpand = document.getElementById('btn-expand-input');
  const container = document.getElementById('input-container');

  // Khởi tạo các callback lắng nghe từ Python truyền xuống
  initFileEncoderCallbacks(updateSendButtonState);

  const triggerLayoutUpdate = () => updateInputLayout(tx, inputMainArea, btnExpand);

  tx.addEventListener('input', triggerLayoutUpdate);

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) triggerLayoutUpdate();
        });
      },
      { threshold: 0.1 }
    );
    observer.observe(container);
  }

  btnExpand.addEventListener('click', () => {
    toggleExpand(container, btnExpand, tx, inputMainArea);
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
          addFilesToQueue(files, updateSendButtonState);
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
        addFilesToQueue(droppedPaths, updateSendButtonState);
      }
    }
  });
}

/**
 * Xử lý khi nhấn nút Gửi
 */
async function handleSend(onSubmit) {
  const tx = document.getElementById('user-input');
  const inputMainArea = document.getElementById('input-main-area');
  const btnExpand = document.getElementById('btn-expand-input');

  const text = tx.value.trim();

  if (hasFilesEncoding()) {
    alert('Vui lòng chờ các tệp hoàn tất xử lý!');
    return;
  }

  const filePayloads = getReadyPayloads();
  const failedFilePaths = getFailedFilePaths();

  if (failedFilePaths.length > 0) {
    const failedNames = failedFilePaths
      .map((path) => path.split(/[\\/]/).pop())
      .join(', ');
    const continueWithoutFailed = window.confirm(
      `Có ${failedFilePaths.length} tệp chưa tải thành công: ${failedNames}.\n\n` +
      'Chọn OK để tiếp tục chỉ với nội dung/tệp READY. Chọn Cancel để giữ hàng chờ và nhấn "Thử lại" trên tệp lỗi.'
    );
    if (!continueWithoutFailed) return;
  }

  if (!text && filePayloads.length === 0) return;

  const currentText = text;
  const filesBackup = backupFilesMap();

  // Reset UI
  tx.value = '';
  tx.style.height = 'auto';
  inputMainArea.classList.remove('multiline');
  btnExpand.classList.add('hidden');
  clearAllFiles();
  updateSendButtonState();

  try {
    await onSubmit(currentText, filePayloads);
  } catch (err) {
    console.error('Lỗi khi gửi dữ liệu:', err);
    // Phục hồi lại dữ liệu nếu gửi thất bại
    tx.value = currentText;
    restoreFilesMap(filesBackup, updateSendButtonState);
    tx.dispatchEvent(new Event('input'));
  }
}

/**
 * Khóa/Mở khóa ô nhập liệu dựa theo trạng thái xử lý của AI
 */
export function hasUnsentPayload() {
  const text = document.getElementById('user-input')?.value?.trim() || '';
  return Boolean(text)
    || hasFilesEncoding()
    || getReadyPayloads().length > 0
    || getFailedFilePaths().length > 0;
}

export function resetInputForIdentity(enabled = true) {
  const tx = document.getElementById('user-input');
  if (tx) {
    tx.value = '';
    tx.style.height = 'auto';
  }
  clearAllFiles();
  isSystemBusy = !enabled;
  const attach = document.getElementById('btn-attach');
  if (tx) tx.disabled = !enabled;
  if (attach) attach.disabled = !enabled;
  updateSendButtonState();
}

export function setInputState(enabled) {
  isSystemBusy = !enabled;
  const tx = document.getElementById('user-input');
  const attach = document.getElementById('btn-attach');
  if (tx) tx.disabled = !enabled;
  if (attach) attach.disabled = !enabled;
  updateSendButtonState();
}