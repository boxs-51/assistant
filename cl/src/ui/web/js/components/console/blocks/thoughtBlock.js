import { renderMarkdownSafe } from '../../../utils/sanitizer.js';

export function extractLatestStepTitle(thoughtText) {
  if (!thoughtText) return { title: 'Đang khởi tạo...', isHeader: false };

  const lines = thoughtText.split('\n').map(l => l.trim()).filter(Boolean);

  // Tìm tiêu đề dạng Header (# hoặc **)
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    const headingMatch = line.match(/^(?:#{1,6}\s*|\*{2})(.*?)(?:\*{2})?$/);
    if (headingMatch && headingMatch[1].trim()) {
      const cleanHeader = headingMatch[1].replace(/[*#]/g, '').trim();
      return { title: cleanHeader, isHeader: true };
    }
  }

  // Fallback nếu chưa có Header: lấy dòng cuối cùng
  const lastLine = lines[lines.length - 1] || '';
  const cleanText = lastLine.replace(/<[^>]*>?/gm, '').replace(/[*_~`]/g, '');
  const title = cleanText.length > 45 ? cleanText.substring(0, 45) + '...' : (cleanText || 'Đang xử lý...');
  
  return { title, isHeader: false };
}

export function appendThoughtToBlock(blockElem, thoughtText) {
  const bubbleElem = blockElem.querySelector('.msg-bubble') || blockElem;
  let thoughtContainer = bubbleElem.querySelector('.thought-container');

  if (!thoughtContainer) {
    const placeholder = bubbleElem.querySelector('.msg-thought-placeholder');
    thoughtContainer = document.createElement('details');
    thoughtContainer.className = 'thought-container';
    thoughtContainer.innerHTML = `
      <summary class="thought-summary">
        <div class="thought-summary-left">
          <span class="thought-badge is-thinking">
            <span class="thought-pulse-dot"></span>
            <span class="badge-text">Suy nghĩ</span>
          </span>
          <span class="current-step-title">Đang khởi tạo...</span>
        </div>
        <svg class="thought-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
      </summary>
      <div class="thought-content-wrapper"><div class="thought-content"></div></div>
    `;

    if (placeholder) {
      placeholder.replaceWith(thoughtContainer);
    } else {
      bubbleElem.prepend(thoughtContainer);
    }
  }

  const badgeElem = thoughtContainer.querySelector('.thought-badge');
  if (badgeElem && !badgeElem.classList.contains('is-thinking')) {
    badgeElem.classList.add('is-thinking');
  }

  const stepTitleElem = thoughtContainer.querySelector('.current-step-title');
  const { title: newTitle, isHeader } = extractLatestStepTitle(thoughtText);

  if (stepTitleElem && stepTitleElem.textContent !== newTitle) {
    // Chỉ kích hoạt animation mờ/trượt khi xuất hiện Header bước mới
    const isNewHeaderStep = isHeader && stepTitleElem.dataset.lastHeader !== newTitle;

    if (isNewHeaderStep) {
      stepTitleElem.dataset.lastHeader = newTitle;
      stepTitleElem.classList.remove('step-swap-anim');
      void stepTitleElem.offsetWidth; // Force Reflow
      stepTitleElem.classList.add('step-swap-anim');
    }

    stepTitleElem.textContent = newTitle;
  }

  const contentElem = thoughtContainer.querySelector('.thought-content');
  if (contentElem) {
    contentElem.innerHTML = renderMarkdownSafe(thoughtText);
  }
}

export function finishThoughtBlock(blockElem) {
  if (!blockElem) return;
  const badgeElem = blockElem.querySelector('.thought-badge');
  if (badgeElem) badgeElem.classList.remove('is-thinking');
}

/**
 * Dựng block chỉ chứa suy nghĩ (Dành cho case đứng độc lập, KHÔNG chứa msg-options)
 */
export function createThoughtBlock(role, thoughtText) {
  if (!thoughtText) return null;

  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-thought-only-part`;

  const bubbleElem = document.createElement('div');
  bubbleElem.className = 'msg-bubble';
  block.appendChild(bubbleElem);

  appendThoughtToBlock(block, thoughtText);
  finishThoughtBlock(block);

  return block;
}