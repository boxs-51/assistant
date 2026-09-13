export function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function safeHttpUrl(value, { allowImageData = false } = {}) {
  const raw = String(value || '').trim();
  if (!raw) return null;

  if (allowImageData && /^data:image\/(?:png|jpe?g|gif|webp|bmp|svg\+xml);base64,/i.test(raw)) {
    return raw;
  }
  if (/^blob:/i.test(raw)) return raw;

  try {
    const url = new URL(raw, window.location.href);
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return url.href;
    }
    return null;
  } catch {
    return null;
  }
}