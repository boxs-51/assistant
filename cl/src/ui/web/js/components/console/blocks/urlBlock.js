// urlBlock.js
import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';

export function createUrlBlock(role, urlData, triggerBlockCallback) {
  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-url-part`;
  const rawUrl = typeof urlData === 'string' ? urlData : (urlData?.url || '');
  const targetUrl = safeHttpUrl(rawUrl);
  if (!targetUrl) return null;

  const title = typeof urlData === 'string' ? urlData : (urlData?.title || targetUrl);
  let domain = targetUrl;
  try { domain = new URL(targetUrl).hostname; } catch (_) {}

  block.innerHTML = `
    <div class="url-card">
      <div class="url-card-icon">🌐</div>
      <div class="url-card-info">
        <a href="${escapeHtml(targetUrl)}" target="_blank" rel="noopener noreferrer" class="url-card-title">${escapeHtml(title)}</a>
        <span class="url-card-domain">${escapeHtml(domain)}</span>
      </div>
      <div class="url-card-actions">
        <button class="opt-btn btn-open-url" title="Mở trang web">🔗</button>
        <button class="opt-btn btn-copy-url" title="Sao chép liên kết">📋</button>
      </div>
    </div>
  `;

  block.querySelector('.btn-open-url').addEventListener('click', () => {
    if (triggerBlockCallback) triggerBlockCallback('url', 'open', urlData);
    window.open(targetUrl, '_blank', 'noopener,noreferrer');
  });

  block.querySelector('.btn-copy-url').addEventListener('click', (e) => {
    navigator.clipboard.writeText(targetUrl);
    if (triggerBlockCallback) triggerBlockCallback('url', 'copy', urlData);
    e.target.innerText = '✔';
    setTimeout(() => { e.target.innerText = '📋'; }, 1500);
  });

  return block;
}