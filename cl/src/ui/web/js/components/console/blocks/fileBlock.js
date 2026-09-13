import { escapeHtml } from '../../../utils/security.js';
import { triggerFileDownload } from '../../../utils/download.js';
import { getFileIcon } from '../../../utils/fileIcons.js';
import { openFileInEditor } from '../../editor.js';

export function createFileBlock(role, attachment, triggerBlockCallback) {
  if (!attachment) return null;

  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-file-part`;

  const fileName = attachment.filename || (attachment.uri ? attachment.uri.split(/[\\\/]/).pop() : 'File_Attachment');
  const safeFileName = escapeHtml(fileName);
  const sizeText = attachment.size ? ` (${(attachment.size / 1024).toFixed(1)} KB)` : '';
  const icon = getFileIcon(fileName);

  block.innerHTML = `
    <div class="msg-file-chip" title="${safeFileName}${escapeHtml(sizeText)}">
      <span class="file-icon">${icon}</span>
      <span class="file-name">${safeFileName}</span>
      <div class="file-actions">
        <span class="file-action-btn btn-view-file" title="Xem nhanh (Read-only)">👁️</span>
        <span class="file-action-btn btn-download-file" title="Tải tệp về">⬇️</span>
      </div>
    </div>
  `;

  block.querySelector('.btn-view-file').addEventListener('click', (e) => {
    e.stopPropagation();
    if (triggerBlockCallback) triggerBlockCallback('file', 'view', attachment);
    openFileInEditor(attachment.uri || null, fileName, true, attachment.base64_data || null);
  });

  block.querySelector('.btn-download-file').addEventListener('click', (e) => {
    e.stopPropagation();
    if (triggerBlockCallback) triggerBlockCallback('file', 'download', attachment);
    triggerFileDownload(fileName, attachment.base64_data ? `base64:${attachment.base64_data}` : (attachment.uri || ''), attachment.mime_type || 'application/octet-stream');
  });

  return block;
}