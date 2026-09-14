// js/components/editor.js

import { getFileIcon } from '../utils/fileIcons.js';
import { addFilesToQueue } from '../components/inputFrame/fileManager.js';

let currentFilePath = null;
let isCurrentReadOnly = false;

export function initEditor() {
  const editorPanel = document.getElementById('editor-panel');
  const btnToggle = document.getElementById('btn-toggle-editor');
  const btnClose = document.getElementById('btn-close-editor');
  const btnSave = document.getElementById('btn-save-file');
  const editorContent = document.getElementById('editor-content');

  btnToggle.addEventListener('click', () => {
    editorPanel.classList.toggle('collapsed');
  });

  btnClose.addEventListener('click', () => {
    editorPanel.classList.add('collapsed');
  });

  editorContent.addEventListener('input', () => {
    // Chỉ bật nút Lưu nếu file không ở chế độ chỉ đọc
    if (currentFilePath && !isCurrentReadOnly) {
      btnSave.disabled = false;
    }
  });

  btnSave.addEventListener('click', async () => {
    if (!currentFilePath || isCurrentReadOnly) return;

    btnSave.innerText = 'Đang lưu...';
    btnSave.disabled = true;

    try {
      if (window.pywebview && window.pywebview.api) {
        const res = await window.pywebview.api.save_file_content(currentFilePath, editorContent.value);
        if (res.success) {
          btnSave.innerText = '✔ Đã lưu';
          setTimeout(() => (btnSave.innerText = '💾 Lưu'), 2000);
        } else {
          alert('Lỗi lưu file: ' + res.error);
          btnSave.disabled = false;
          btnSave.innerText = '💾 Lưu';
        }
      }
    } catch (err) {
      console.error('Lỗi lưu file:', err);
      btnSave.disabled = false;
      btnSave.innerText = '💾 Lưu';
    }
  });
}

/**
 * Mở file trong Editor
 * @param {string} filePath - Đường dẫn file
 * @param {string|null} fileName - Tên hiển thị (Tùy chọn)
 * @param {boolean} isReadOnly - Trạng thái chỉ đọc (Mặc định: false)
 * @param {string|null} initialContent - Nội dung trực tiếp (Tùy chọn, dùng nếu không gọi API)
 */
export async function openFileInEditor(filePath, fileName = null, isReadOnly = false, initialContent = null) {
  const editorPanel = document.getElementById('editor-panel');
  const editorFilename = document.getElementById('editor-filename');
  const editorContent = document.getElementById('editor-content');
  const btnSave = document.getElementById('btn-save-file');

  currentFilePath = filePath;
  isCurrentReadOnly = isReadOnly;

  const displayName = fileName || (filePath ? filePath.split(/[\\/]/).pop() : 'Untitled');
  const icon = getFileIcon(displayName);

  // Cập nhật giao diện tiêu đề & nút lưu
  editorFilename.innerText = `${icon} ${displayName}` + (isReadOnly ? ' (Chỉ đọc)' : '');
  editorPanel.classList.remove('collapsed');

  if (isReadOnly) {
    editorPanel.classList.add('is-readonly');
    editorContent.readOnly = true;
    btnSave.disabled = true;
    btnSave.innerText = '🔒 Chỉ đọc';
    btnSave.title = 'File ở chế độ chỉ đọc';
  } else {
    editorPanel.classList.remove('is-readonly');
    editorContent.readOnly = false;
    btnSave.disabled = true;
    btnSave.innerText = '💾 Lưu';
    btnSave.title = 'Lưu thay đổi';
  }

  // Nếu có sẵn nội dung truyền vào (ví dụ file đính kèm từ tin nhắn)
  if (initialContent !== null && initialContent !== undefined) {
    editorContent.value = initialContent;
    return;
  }

  // Nếu không có nội dung sẵn, đọc qua API pywebview
  editorContent.value = 'Đang tải nội dung...';
  try {
    if (window.pywebview && window.pywebview.api) {
      const res = await window.pywebview.api.read_file_content(filePath);
      if (res.success) {
        editorContent.value = res.content;
      } else {
        editorContent.value = `❌ Lỗi đọc file: ${res.error}`;
      }
    }
  } catch (err) {
    editorContent.value = `❌ Không thể mở file: ${err.message}`;
  }
}