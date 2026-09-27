// mediaBlock.js
import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';

const MAX_CANONICAL_MEDIA_INLINE_BYTES = 32 * 1024 * 1024;

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

  block.innerHTML = `<div class="media-wrapper">
    <${mediaType} controls ${mediaSrc ? `src="${escapeHtml(mediaSrc)}"` : ''} class="custom-${mediaType}-player"></${mediaType}>
    ${isCanonicalAsset ? '<button type="button" class="canonical-media-load">Tải media để phát</button>' : ''}
  </div>`;

  if (isCanonicalAsset) {
    const media = block.querySelector(`.custom-${mediaType}-player`);
    const loadButton = block.querySelector('.canonical-media-load');
    let loading = false;
    let loaded = false;
    let resolvedUrl = null;

    const revoke = () => {
      if (!resolvedUrl) return;
      URL.revokeObjectURL(resolvedUrl);
      resolvedUrl = null;
    };

    const resolveOnDemand = async () => {
      if (loading || loaded) return;
      loading = true;
      if (loadButton) {
        loadButton.disabled = true;
        loadButton.innerText = 'Đang tải media...';
      }

      try {
        const resolved = await window.createCanonicalAssetObjectUrl?.(
          mediaData,
          { maxBytes: MAX_CANONICAL_MEDIA_INLINE_BYTES },
        );
        if (!resolved?.url) {
          throw new Error('Canonical media content is unavailable.');
        }
        if (!media) {
          URL.revokeObjectURL(resolved.url);
          return;
        }

        resolvedUrl = resolved.url;
        media.src = resolvedUrl;
        loaded = true;
        if (typeof media.load === 'function') media.load();
        if (loadButton) loadButton.remove();

        media.addEventListener('error', revoke, { once: true });
        const observer = new MutationObserver(() => {
          if (!document.documentElement.contains(block)) {
            revoke();
            observer.disconnect();
          }
        });
        observer.observe(document.documentElement, { childList: true, subtree: true });
      } catch (error) {
        console.error('Unable to resolve canonical media:', error);
        if (loadButton) {
          loadButton.disabled = false;
          loadButton.innerText = 'Thử tải lại media';
        }
      } finally {
        loading = false;
      }
    };

    // Canonical media is resolved only after an explicit user action.
    // Avoid eager full-buffer bytes -> base64 -> WebView -> Blob materialization.
    loadButton?.addEventListener('click', resolveOnDemand);
  }

  return block;
}