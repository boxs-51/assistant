import { escapeHtml } from '../../../utils/security.js';
import { triggerFileDownload } from '../../../utils/download.js';
import { getFileIcon } from '../../../utils/fileIcons.js';
import { openFileInEditor } from '../../editor.js';

function isTextualMimeType(mimeType) {
  const normalized = String(mimeType || '').split(';', 1)[0].trim().toLowerCase();
  return (
    normalized.startsWith('text/')
    || normalized === 'application/json'
    || normalized === 'application/xml'
    || normalized === 'application/javascript'
    || normalized === 'application/x-javascript'
    || normalized.endsWith('+json')
    || normalized.endsWith('+xml')
  );
}

function decodeBase64Text(base64Data, mimeType) {
  const binary = atob(base64Data || '');
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  const charsetMatch = /charset\s*=\s*["']?([^;"'\s]+)/i.exec(mimeType || '');
  const charset = charsetMatch?.[1] || 'utf-8';
  try {
    return new TextDecoder(charset).decode(bytes);
  } catch (_error) {
    return new TextDecoder('utf-8').decode(bytes);
  }
}

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

  block.querySelector('.btn-view-file').addEventListener('click', async (e) => {
    e.stopPropagation();
    if (triggerBlockCallback) triggerBlockCallback('file', 'view', attachment);
    try {
      if (attachment.asset_id) {
        const content = await window.resolveCanonicalAssetContent?.(attachment);
        if (!content) throw new Error('Canonical asset content is unavailable.');
        const mimeType = content.mime_type || attachment.mime_type || 'application/octet-stream';
        const initialContent = isTextualMimeType(mimeType)
          ? decodeBase64Text(content.base64_data || '', mimeType)
          : (content.base64_data || null);
        openFileInEditor(null, fileName, true, initialContent);
        return;
      }
      openFileInEditor(attachment.uri || null, fileName, true, attachment.base64_data || null);
    } catch (error) {
      console.error('Unable to view file attachment:', error);
    }
  });

  block.querySelector('.btn-download-file').addEventListener('click', async (e) => {
    e.stopPropagation();
    if (triggerBlockCallback) triggerBlockCallback('file', 'download', attachment);
    try {
      if (attachment.asset_id) {
        const content = await window.resolveCanonicalAssetContent?.(attachment);
        if (!content) throw new Error('Canonical asset content is unavailable.');
        triggerFileDownload(
          fileName,
          `base64:${content.base64_data || ''}`,
          content.mime_type || attachment.mime_type || 'application/octet-stream'
        );
        return;
      }
      triggerFileDownload(
        fileName,
        attachment.base64_data ? `base64:${attachment.base64_data}` : (attachment.uri || ''),
        attachment.mime_type || 'application/octet-stream'
      );
    } catch (error) {
      console.error('Unable to download file attachment:', error);
    }
  });

  return block;
}