// mediaBlock.js
import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';

export function createMediaBlock(role, mediaType, mediaData) {
  if (!mediaData || !['audio', 'video'].includes(mediaType)) return null;
  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-media-part`;

  const isCanonicalAsset = Boolean(mediaData.asset_id);
  const rawSrc = isCanonicalAsset
    ? ''
    : (mediaData.uri || (mediaData.base64_data ? `data:${mediaData.mime_type || 'application/octet-stream'};base64,${mediaData.base64_data}` : ''));
  const mediaSrc = rawSrc ? safeHttpUrl(rawSrc, { allowImageData: false }) : null;
  if (!isCanonicalAsset && !mediaSrc) return null;

  block.innerHTML = `<div class="media-wrapper"><${mediaType} controls ${mediaSrc ? `src="${escapeHtml(mediaSrc)}"` : ''} class="custom-${mediaType}-player"></${mediaType}></div>`;

  if (isCanonicalAsset) {
    window.createCanonicalAssetObjectUrl?.(mediaData)
      .then((resolved) => {
        if (!resolved?.url) throw new Error('Canonical media content is unavailable.');
        const media = block.querySelector(`.custom-${mediaType}-player`);
        if (!media) {
          URL.revokeObjectURL(resolved.url);
          return;
        }
        media.src = resolved.url;
        const revoke = () => URL.revokeObjectURL(resolved.url);
        media.addEventListener('error', revoke, { once: true });
        const observer = new MutationObserver(() => {
          if (!document.documentElement.contains(block)) {
            revoke();
            observer.disconnect();
          }
        });
        observer.observe(document.documentElement, { childList: true, subtree: true });
      })
      .catch((error) => console.error('Unable to resolve canonical media:', error));
  }

  return block;
}