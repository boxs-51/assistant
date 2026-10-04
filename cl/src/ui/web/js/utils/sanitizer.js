import { escapeHtml, hardenExternalAnchor, safeExternalUrl } from './security.js';

const ALLOWED_TAGS = new Set([
  'a', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'hr', 'li', 'ol', 'p', 'pre', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
]);

function normalizeCodeLanguage(value = '') {
  const match = String(value).match(/^(?:language-)?([a-zA-Z0-9_+-]{1,32})$/);
  return match ? `language-${match[1].toLowerCase()}` : '';
}

export function sanitizeHtml(html = '') {
  const parser = new DOMParser();
  const doc = parser.parseFromString(String(html), 'text/html');

  [...doc.body.querySelectorAll('*')].forEach((node) => {
    const tag = node.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      node.replaceWith(doc.createTextNode(node.textContent || ''));
      return;
    }

    const originalHref = tag === 'a' ? node.getAttribute('href') : null;
    const originalClass = tag === 'code' ? node.getAttribute('class') : null;
    [...node.attributes].forEach((attr) => {
      node.removeAttribute(attr.name);
    });

    if (tag === 'a') {
      hardenExternalAnchor(node, originalHref);
    } else if (tag === 'code') {
      const className = normalizeCodeLanguage(originalClass || '');
      if (className) node.setAttribute('class', className);
    }
  });

  return doc.body.innerHTML;
}

function renderDecorations(escaped) {
  return escaped
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
    .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
}

function renderInline(text = '') {
  const raw = String(text);
  const tokenRe = /(`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))/g;
  let cursor = 0;
  let output = '';
  let match;

  while ((match = tokenRe.exec(raw)) !== null) {
    output += renderDecorations(escapeHtml(raw.slice(cursor, match.index)));
    const token = match[0];

    if (token.startsWith('`')) {
      output += `<code>${escapeHtml(token.slice(1, -1))}</code>`;
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const label = renderDecorations(escapeHtml(linkMatch?.[1] || ''));
      const safe = safeExternalUrl(linkMatch?.[2] || '');
      output += safe
        ? `<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : label;
    }
    cursor = match.index + token.length;
  }

  output += renderDecorations(escapeHtml(raw.slice(cursor)));
  return output;
}

export function renderMarkdownSafe(text = '') {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  const output = [];
  let paragraph = [];
  let listType = null;
  let listItems = [];
  let inFence = false;
  let fenceLanguage = '';
  let fenceLines = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${paragraph.map(renderInline).join('<br>')}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!listType) return;
    output.push(`<${listType}>${listItems.map(item => `<li>${renderInline(item)}</li>`).join('')}</${listType}>`);
    listType = null;
    listItems = [];
  };

  for (const line of lines) {
    const fence = line.match(/^```\s*([a-zA-Z0-9_+-]{0,32})\s*$/);
    if (fence) {
      flushParagraph();
      flushList();
      if (!inFence) {
        inFence = true;
        fenceLanguage = fence[1] || '';
        fenceLines = [];
      } else {
        const className = normalizeCodeLanguage(fenceLanguage);
        output.push(`<pre><code${className ? ` class="${className}"` : ''}>${escapeHtml(fenceLines.join('\n'))}</code></pre>`);
        inFence = false;
        fenceLanguage = '';
        fenceLines = [];
      }
      continue;
    }

    if (inFence) {
      fenceLines.push(line);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushList();
      output.push(`<blockquote>${renderInline(quote[1])}</blockquote>`);
      continue;
    }

    const unordered = line.match(/^[-+*]\s+(.+)$/);
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? 'ul' : 'ol';
      if (listType && listType !== nextType) flushList();
      listType = nextType;
      listItems.push((unordered || ordered)[1]);
      continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      flushList();
      output.push('<hr>');
      continue;
    }

    paragraph.push(line);
  }

  if (inFence) {
    const className = normalizeCodeLanguage(fenceLanguage);
    output.push(`<pre><code${className ? ` class="${className}"` : ''}>${escapeHtml(fenceLines.join('\n'))}</code></pre>`);
  }
  flushParagraph();
  flushList();

  return output.join('');
}
