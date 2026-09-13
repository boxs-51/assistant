// mediaBlock.js
import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';

export function createMediaBlock(role, mediaType, mediaData) {
  if (!mediaData || !['audio', 'video'].includes(mediaType)) return null;
  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-media-part`;

  const rawSrc = mediaData.uri || (mediaData.base64_data ? `data:${mediaData.mime_type || 'application/octet-stream'};base64,${mediaData.base64_data}` : '');
  const mediaSrc = safeHttpUrl(rawSrc, { allowImageData: false });
  if (!mediaSrc) return null;

  block.innerHTML = `<div class="media-wrapper"><${mediaType} controls src="${escapeHtml(mediaSrc)}" class="custom-${mediaType}-player"></${mediaType}></div>`;
  return block;
}