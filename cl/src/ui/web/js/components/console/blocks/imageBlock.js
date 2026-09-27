// imageBlock.js
import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';
import { triggerFileDownload } from '../../../utils/download.js';

export function createImageBlock(role, imageData, triggerBlockCallback) {
  if (!imageData) return null;
  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-image-part`;

  const isCanonicalAsset = Boolean(imageData.asset_id);
  const rawSrc = isCanonicalAsset
    ? ''
    : (imageData.uri || (imageData.base64_data ? `data:${imageData.mime_type || 'image/png'};base64,${imageData.base64_data}` : ''));
  const imgSrc = rawSrc ? safeHttpUrl(rawSrc, { allowImageData: true }) : null;
  if (!isCanonicalAsset && !imgSrc) return null;

  const fileName = imageData.filename || 'Image';
  block.innerHTML = `
    <div class="msg-image-container">
      <img ${imgSrc ? `src="${escapeHtml(imgSrc)}"` : ''} alt="${escapeHtml(fileName)}" class="msg-image-preview" />
      <div class="image-overlay-actions">
        <button class="opt-btn btn-view-image" title="Xem ảnh lớn">🔍</button>
        <button class="opt-btn btn-download-image" title="Tải ảnh">⬇️</button>
      </div>
    </div>
  `;

  const resolveObjectUrl = async () => {
    if (!isCanonicalAsset) return { url: imgSrc, ephemeral: false };
    const resolved = await window.createCanonicalAssetObjectUrl?.(imageData);
    if (!resolved?.url) throw new Error('Canonical image content is unavailable.');
    return { url: resolved.url, ephemeral: true };
  };

  if (isCanonicalAsset) {
    resolveObjectUrl()
      .then(({ url }) => {
        const image = block.querySelector('.msg-image-preview');
        if (!image) {
          URL.revokeObjectURL(url);
          return;
        }
        image.src = url;
        image.addEventListener('load', () => URL.revokeObjectURL(url), { once: true });
        image.addEventListener('error', () => URL.revokeObjectURL(url), { once: true });
      })
      .catch((error) => console.error('Unable to preview canonical image:', error));
  }

  block.querySelector('.btn-view-image').addEventListener('click', async () => {
    if (triggerBlockCallback) triggerBlockCallback('image', 'view', imageData);
    try {
      const { url, ephemeral } = await resolveObjectUrl();
      window.open(url, '_blank', 'noopener,noreferrer');
      if (ephemeral) window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      console.error('Unable to view image attachment:', error);
    }
  });

  block.querySelector('.btn-download-image').addEventListener('click', async () => {
    if (triggerBlockCallback) triggerBlockCallback('image', 'download', imageData);
    try {
      const { url, ephemeral } = await resolveObjectUrl();
      triggerFileDownload(fileName, url, imageData.mime_type || 'image/png');
      if (ephemeral) window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      console.error('Unable to download image attachment:', error);
    }
  });

  return block;
}