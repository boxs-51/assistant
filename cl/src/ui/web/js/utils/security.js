export function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function safeHttpUrl(value, {
  allowImageData = false,
  allowBlob = true,
} = {}) {
  const raw = String(value || '').trim();
  if (!raw) return null;

  if (allowImageData && /^data:image\/(?:png|jpe?g|gif|webp|bmp|svg\+xml);base64,/i.test(raw)) {
    return raw;
  }
  if (allowBlob && /^blob:/i.test(raw)) return raw;

  try {
    const base = globalThis.window?.location?.href || 'https://local.invalid/';
    const url = new URL(raw, base);
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return url.href;
    }
    return null;
  } catch {
    return null;
  }
}

export function safeExternalUrl(value) {
  return safeHttpUrl(value, { allowImageData: false, allowBlob: false });
}

export function hardenExternalAnchor(anchor, value) {
  const safe = safeExternalUrl(value);
  if (!anchor) return null;
  if (!safe) {
    anchor.removeAttribute('href');
    anchor.removeAttribute('target');
    anchor.removeAttribute('rel');
    return null;
  }
  anchor.setAttribute('href', safe);
  anchor.setAttribute('target', '_blank');
  anchor.setAttribute('rel', 'noopener noreferrer');
  return safe;
}

export function safeEnum(value, allowed, fallback) {
  const normalized = String(value || '').toLowerCase();
  return allowed.includes(normalized) ? normalized : fallback;
}
