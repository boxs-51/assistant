// imageBlock.js
import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';
import { triggerFileDownload } from '../../../utils/download.js';

export function createImageBlock(role, imageData, triggerBlockCallback) {
  if (!imageData) return null;
  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-image-part`;

  const rawSrc = imageData.uri || (imageData.base64_data ? `data:${imageData.mime_type || 'image/png'};base64,${imageData.base64_data}` : '');
  const imgSrc = safeHttpUrl(rawSrc, { allowImageData: true });
  if (!imgSrc) return null;

  const fileName = imageData.filename || 'Image';
  block.innerHTML = `
    <div class="msg-image-container">
      <img src="${escapeHtml(imgSrc)}" alt="${escapeHtml(fileName)}" class="msg-image-preview" />
      <div class="image-overlay-actions">
        <button class="opt-btn btn-view-image" title="Xem ảnh lớn">🔍</button>
        <button class="opt-btn btn-download-image" title="Tải ảnh">⬇️</button>
      </div>
    </div>
  `;

  block.querySelector('.btn-view-image').addEventListener('click', () => {
    if (triggerBlockCallback) triggerBlockCallback('image', 'view', imageData);
    window.open(imgSrc, '_blank', 'noopener,noreferrer');
  });

  block.querySelector('.btn-download-image').addEventListener('click', () => {
    if (triggerBlockCallback) triggerBlockCallback('image', 'download', imageData);
    triggerFileDownload(fileName, imgSrc, imageData.mime_type || 'image/png');
  });

  return block;
}