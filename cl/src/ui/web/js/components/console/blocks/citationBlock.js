import { escapeHtml, safeHttpUrl } from '../../../utils/security.js';
import { renderMarkdownSafe } from '../../../utils/sanitizer.js';
import { enhanceCodeBlocks } from './textBlock.js';

/**
 * Tự động chèn marker [n] vào văn bản thô dựa trên end_index/start_index/text_segment
 */
export function prepareTextWithCitations(text = '', citations = []) {
  if (!text || !Array.isArray(citations) || citations.length === 0) return text;

  const hasExistingMarkers = citations.some(cit => {
    const idx = cit.index || 1;
    return text.includes(`[${idx}]`);
  });

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

/**
 * Gom nhóm các marker [1][2] hoặc [1], [2] đứng liền kề và chuyển thành Citation Chip
 */
export function appendCitationsToBlock(blockElem, citations = []) {
  if (!blockElem || !Array.isArray(citations) || citations.length === 0) return;

  const bodyElem = blockElem.querySelector('.msg-body');
  if (!bodyElem) return;

  const citationMap = new Map();
  citations.forEach((cit, idx) => {
    const key = cit.index || (idx + 1);
    citationMap.set(Number(key), cit);
  });

  // Regex phát hiện các chuỗi marker đứng kề nhau, ví dụ: [1][2] hoặc [1], [2]
  const groupRegex = /((?:\[\d+\][\s,]*)+)/g;

  bodyElem.innerHTML = bodyElem.innerHTML.replace(groupRegex, (match) => {
    const indices = [...match.matchAll(/\[(\d+)\]/g)].map(m => Number(m[1]));
    const matchedCits = indices.map(i => citationMap.get(i)).filter(Boolean);

    if (matchedCits.length === 0) return match;

    if (matchedCits.length === 1) {
      return renderSingleChip(matchedCits[0], indices[0]);
    }

    return renderGroupedChip(matchedCits, indices);
  });

  bindChipClickEvents(blockElem);
}

/**
 * Sinh HTML cho 1 Citation đơn
 */
function renderSingleChip(cit, citIndex) {
  const title = escapeHtml(cit.title || 'Nguồn tham khảo');
  const url = safeHttpUrl(cit.url || '');
  const snippet = escapeHtml(cit.snippet || cit.quote || '');
  const sourceType = cit.source_type || (url ? 'web' : 'file');
  const icon = sourceType === 'file' ? '📄' : '🌐';
  const tooltip = `[${citIndex}] ${title}${snippet ? '\n\n"' + snippet + '"' : ''}`;

  if (url) {
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="citation-chip citation-${sourceType}" title="${tooltip}"><span class="cit-icon">${icon}</span><span class="cit-badge">[${citIndex}]</span><span class="cit-title">${title}</span></a>`;
  }

  return `<span class="citation-chip citation-${sourceType}" title="${tooltip}"><span class="cit-icon">${icon}</span><span class="cit-badge">[${citIndex}]</span><span class="cit-title">${title}</span></span>`;
}

/**
 * Sinh HTML cho Chip Gộp (Multiple Citations) có Dropdown danh sách
 */
function renderGroupedChip(cits, indices) {
  const firstCit = cits[0];
  const firstTitle = escapeHtml(firstCit.title || 'Nguồn tham khảo');
  const primaryType = firstCit.source_type || (firstCit.url ? 'web' : 'file');
  const icon = primaryType === 'file' ? '📄' : '🌐';
  const badgeText = indices.join(',');

  const dropdownItemsHtml = cits.map((cit, i) => {
    const idx = indices[i];
    const t = escapeHtml(cit.title || 'Nguồn tham khảo');
    const u = safeHttpUrl(cit.url || '');
    const st = cit.source_type || (u ? 'web' : 'file');
    const ic = st === 'file' ? '📄' : '🌐';

    if (u) {
      return `<a href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer" class="cit-sub-item"><span class="cit-icon">${ic}</span><span class="cit-badge">[${idx}]</span><span class="cit-sub-title">${t}</span></a>`;
    }
    return `<span class="cit-sub-item"><span class="cit-icon">${ic}</span><span class="cit-badge">[${idx}]</span><span class="cit-sub-title">${t}</span></span>`;
  }).join('');

  return `
    <span class="citation-chip citation-${primaryType} has-multiple">
      <span class="cit-icon">${icon}</span>
      <span class="cit-badge">[${badgeText}]</span>
      <span class="cit-title">${firstTitle}</span>
      <span class="cit-arrow">▼</span>
      <span class="cit-dropdown">${dropdownItemsHtml}</span>
    </span>
  `;
}

/**
 * Gắn sự kiện Click để bật/tắt Popup danh sách nguồn cho Chip gộp
 */
function bindChipClickEvents(blockElem) {
  if (blockElem.dataset.citBound) return;

  blockElem.addEventListener('click', (e) => {
    const multipleChip = e.target.closest('.citation-chip.has-multiple');

    if (multipleChip) {
      if (!e.target.closest('.cit-sub-item')) {
        e.preventDefault();
        e.stopPropagation();

        document.querySelectorAll('.citation-chip.is-expanded').forEach(c => {
          if (c !== multipleChip) c.classList.remove('is-expanded');
        });

        multipleChip.classList.toggle('is-expanded');
      }
    } else {
      document.querySelectorAll('.citation-chip.is-expanded').forEach(c => c.classList.remove('is-expanded'));
    }
  });

  blockElem.dataset.citBound = 'true';
}

/**
 * Cập nhật trích dẫn cho khối text đã render xong ở chunk cuối
 */
export function updateBlockWithCitations(blockElem, citations = []) {
  if (!blockElem || !Array.isArray(citations) || citations.length === 0) return null;

  const rawText = blockElem.dataset.rawText || '';
  const bodyElem = blockElem.querySelector('.msg-body');
  if (!bodyElem || !rawText) return null;

  const processedText = prepareTextWithCitations(rawText, citations);
  blockElem.dataset.rawText = processedText;

  bodyElem.innerHTML = renderMarkdownSafe(processedText);
  appendCitationsToBlock(blockElem, citations);
  
  // Tải lại header và tô màu khối code sau khi render lại innerHTML
  enhanceCodeBlocks(blockElem);

  return processedText;
}