import { marked } from '../config.js';
import { safeHttpUrl } from './security.js';

export function sanitizeHtml(html = '') {
  const parser = new DOMParser();
  const doc = parser.parseFromString(String(html), 'text/html');

  doc.querySelectorAll('script, iframe, object, embed, form, input, textarea, select, option, button, style, link, meta, base, video[srcdoc]').forEach(node => node.remove());

  doc.querySelectorAll('*').forEach(node => {
    [...node.attributes].forEach(attr => {
      const name = attr.name.toLowerCase();
      const value = attr.value || '';

      if (name.startsWith('on') || name === 'srcdoc') {
        node.removeAttribute(attr.name);
        return;
      }

      if (name === 'href') {
        const safe = safeHttpUrl(value);
        if (!safe && !/^mailto:/i.test(value)) {
          node.removeAttribute(attr.name);
        } else if (safe) {
          node.setAttribute(attr.name, safe);
        }
        return;
      }

      if (name === 'src' || name === 'xlink:href') {
        const safe = safeHttpUrl(value, {
          allowImageData: node.tagName.toLowerCase() === 'img',
        });
        if (!safe) {
          node.removeAttribute(attr.name);
        } else {
          node.setAttribute(attr.name, safe);
        }
      }
    });
  });

  return doc.body.innerHTML;
}

export function renderMarkdownSafe(text = '') {
  return sanitizeHtml(marked.parse(String(text || '')));
}