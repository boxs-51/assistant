import { safeExternalUrl } from '../../../utils/security.js';
import { renderMarkdownSafe } from '../../../utils/sanitizer.js';
import { enhanceCodeBlocks } from './textBlock.js';

export function prepareTextWithCitations(text = '', citations = []) {
  if (!text || !Array.isArray(citations) || citations.length === 0) return text;
  const hasExistingMarkers = citations.some((cit) => text.includes(`[${cit.index || 1}]`));
  if (hasExistingMarkers) return text;

  let modifiedText = text;
  const sortedCitations = [...citations].sort((a, b) => {
    const posA = a.end_index ?? a.start_index ?? 0;
    const posB = b.end_index ?? b.start_index ?? 0;
    return posB - posA;
  });

  sortedCitations.forEach((cit, idx) => {
    const citIndex = cit.index || (citations.length - idx);
    const marker = ` [${citIndex}]`;
    if (typeof cit.end_index === 'number' && cit.end_index <= modifiedText.length && cit.end_index >= 0) {
      modifiedText = modifiedText.slice(0, cit.end_index) + marker + modifiedText.slice(cit.end_index);
    } else if (typeof cit.start_index === 'number' && cit.start_index <= modifiedText.length && cit.start_index >= 0) {
      modifiedText = modifiedText.slice(0, cit.start_index) + marker + modifiedText.slice(cit.start_index);
    } else if (cit.text_segment && modifiedText.includes(cit.text_segment)) {
      const segPos = modifiedText.indexOf(cit.text_segment) + cit.text_segment.length;
      modifiedText = modifiedText.slice(0, segPos) + marker + modifiedText.slice(segPos);
    }
  });

  return modifiedText;
}

export function normalizeCitationSourceType(value, hasUrl = false) {
  return String(value || '').toLowerCase() === 'file' ? 'file' : (hasUrl ? 'web' : 'file');
}

function appendText(parent, className, value) {
  const span = document.createElement('span');
  span.className = className;
  span.textContent = String(value);
  parent.appendChild(span);
  return span;
}

function citationTitle(cit, index) {
  const title = String(cit?.title || 'Nguồn tham khảo');
  const snippet = String(cit?.snippet || cit?.quote || '');
  return `[${index}] ${title}${snippet ? `\n\n"${snippet}"` : ''}`;
}

function createCitationLink(cit, index, className) {
  const safeUrl = safeExternalUrl(cit?.url || '');
  const sourceType = normalizeCitationSourceType(cit?.source_type, Boolean(safeUrl));
  const element = document.createElement(safeUrl ? 'a' : 'span');
  element.className = `${className} citation-${sourceType}`;
  element.title = citationTitle(cit, index);

  if (safeUrl) {
    element.href = safeUrl;
    element.target = '_blank';
    element.rel = 'noopener noreferrer';
  }

  appendText(element, 'cit-icon', sourceType === 'file' ? '📄' : '🌐');
  appendText(element, 'cit-badge', `[${index}]`);
  appendText(element, 'cit-title', cit?.title || 'Nguồn tham khảo');
  return element;
}

function createGroupedChip(entries) {
  const outer = document.createElement('span');
  const primary = entries[0];
  const sourceType = normalizeCitationSourceType(primary.cit?.source_type, Boolean(safeExternalUrl(primary.cit?.url || '')));
  outer.className = `citation-chip citation-${sourceType} has-multiple`;
  appendText(outer, 'cit-icon', sourceType === 'file' ? '📄' : '🌐');
  appendText(outer, 'cit-badge', `[${entries.map(entry => entry.index).join(',')}]`);
  appendText(outer, 'cit-title', primary.cit?.title || 'Nguồn tham khảo');
  appendText(outer, 'cit-arrow', '▼');

  const dropdown = document.createElement('span');
  dropdown.className = 'cit-dropdown';
  entries.forEach(({ cit, index }) => {
    dropdown.appendChild(createCitationLink(cit, index, 'cit-sub-item'));
  });
  outer.appendChild(dropdown);
  return outer;
}

function replaceMarkersInTextNode(node, citationMap) {
  const text = node.nodeValue || '';
  const regex = /((?:\[\d+\][\s,]*)+)/g;
  let cursor = 0;
  let match;
  const fragment = document.createDocumentFragment();
  let changed = false;

  while ((match = regex.exec(text)) !== null) {
    const indices = [...match[0].matchAll(/\[(\d+)\]/g)].map(item => Number(item[1]));
    const entries = indices
      .map(index => ({ index, cit: citationMap.get(index) }))
      .filter(entry => entry.cit);
    if (!entries.length) continue;

    changed = true;
    fragment.appendChild(document.createTextNode(text.slice(cursor, match.index)));
    fragment.appendChild(entries.length === 1
      ? createCitationLink(entries[0].cit, entries[0].index, 'citation-chip')
      : createGroupedChip(entries));
    cursor = match.index + match[0].length;
  }

  if (!changed) return;
  fragment.appendChild(document.createTextNode(text.slice(cursor)));
  node.replaceWith(fragment);
}

function collectTextNodes(node, output) {
  [...node.childNodes].forEach((child) => {
    if (child.nodeType === 3) {
      output.push(child);
      return;
    }
    if (child.nodeType !== 1) return;
    if (child.matches('pre, code, a, .citation-chip')) return;
    collectTextNodes(child, output);
  });
}

export function appendCitationsToBlock(blockElem, citations = []) {
  if (!blockElem || !Array.isArray(citations) || citations.length === 0) return;
  const bodyElem = blockElem.querySelector('.msg-body');
  if (!bodyElem) return;

  const citationMap = new Map();
  citations.forEach((cit, idx) => citationMap.set(Number(cit.index || (idx + 1)), cit));

  const textNodes = [];
  collectTextNodes(bodyElem, textNodes);
  textNodes.forEach(node => replaceMarkersInTextNode(node, citationMap));
  bindChipClickEvents(blockElem);
}

function bindChipClickEvents(blockElem) {
  if (blockElem.dataset.citBound) return;
  blockElem.addEventListener('click', (e) => {
    const multipleChip = e.target.closest('.citation-chip.has-multiple');
    if (multipleChip) {
      if (!e.target.closest('.cit-sub-item')) {
        e.preventDefault();
        e.stopPropagation();
        document.querySelectorAll('.citation-chip.is-expanded').forEach((chip) => {
          if (chip !== multipleChip) chip.classList.remove('is-expanded');
        });
        multipleChip.classList.toggle('is-expanded');
      }
    } else {
      document.querySelectorAll('.citation-chip.is-expanded').forEach(chip => chip.classList.remove('is-expanded'));
    }
  });
  blockElem.dataset.citBound = 'true';
}

export function updateBlockWithCitations(blockElem, citations = []) {
  if (!blockElem || !Array.isArray(citations) || citations.length === 0) return null;
  const rawText = blockElem.dataset.rawText || '';
  const bodyElem = blockElem.querySelector('.msg-body');
  if (!bodyElem || !rawText) return null;

  const processedText = prepareTextWithCitations(rawText, citations);
  blockElem.dataset.rawText = processedText;
  bodyElem.innerHTML = renderMarkdownSafe(processedText);
  appendCitationsToBlock(blockElem, citations);
  enhanceCodeBlocks(blockElem);
  return processedText;
}
