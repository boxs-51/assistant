const MULTILINE_LINE_THRESHOLD = 6;
let canvasCtx = null;
let isExpanded = false;

export function isElementVisible(elem) {
  return !!(elem && (elem.offsetWidth || elem.offsetHeight || elem.getClientRects().length));
}

export function measureTextWidth(text, tx) {
  if (!text) return 0;
  if (!canvasCtx) {
    const canvas = document.createElement('canvas');
    canvasCtx = canvas.getContext('2d');
  }
  const computed = window.getComputedStyle(tx);
  canvasCtx.font = `${computed.fontWeight || '400'} ${computed.fontSize || '14px'} ${computed.fontFamily || 'sans-serif'}`;
  return canvasCtx.measureText(text).width;
}

export function getAvailableInlineWidth(inputMainArea, tx) {
  const mainWidth = inputMainArea.clientWidth;
  if (!mainWidth) return 200;

  const actionLeft = inputMainArea.querySelector('.action-left');
  const actionRight = inputMainArea.querySelector('.action-right');
  const actionCenter = inputMainArea.querySelector('.action-center');

  const leftW = actionLeft ? actionLeft.offsetWidth : 0;
  const rightW = actionRight ? actionRight.offsetWidth : 0;
  const centerW = actionCenter ? actionCenter.offsetWidth : 0;

  const computedTx = window.getComputedStyle(tx);
  const txPadding = (parseFloat(computedTx.paddingLeft) || 0) + (parseFloat(computedTx.paddingRight) || 0);

  return Math.max(50, mainWidth - leftW - rightW - centerW - txPadding - 24);
}

export function updateInputLayout(tx, inputMainArea, btnExpand) {
  if (isExpanded || !isElementVisible(tx)) return;

  const text = tx.value;
  const hasNewline = text.includes('\n');
  const textWidth = measureTextWidth(text, tx);
  const availableInlineW = getAvailableInlineWidth(inputMainArea, tx);

  const shouldSplitLayout = hasNewline || textWidth >= availableInlineW - 10;

  if (shouldSplitLayout) {
    inputMainArea.classList.add('multiline');
  } else {
    inputMainArea.classList.remove('multiline');
  }

  const computed = window.getComputedStyle(tx);
  const paddingTop = parseFloat(computed.paddingTop) || 0;
  const paddingBottom = parseFloat(computed.paddingBottom) || 0;
  let lineHeight = parseFloat(computed.lineHeight);
  if (isNaN(lineHeight) || lineHeight === 0) {
    lineHeight = (parseFloat(computed.fontSize) || 14) * 1.5;
  }

  const paddingTotal = paddingTop + paddingBottom;
  const maxNaturalHeight = Math.ceil(lineHeight * MULTILINE_LINE_THRESHOLD + paddingTotal);

  const savedScrollTop = tx.scrollTop;
  tx.style.height = 'auto';
  const currentScrollHeight = tx.scrollHeight;

  if (currentScrollHeight > maxNaturalHeight) {
    tx.style.height = `${maxNaturalHeight}px`;
    tx.style.overflowY = 'auto';
    tx.scrollTop = savedScrollTop;
    btnExpand.classList.remove('hidden');
  } else {
    tx.style.height = `${currentScrollHeight}px`;
    tx.style.overflowY = 'hidden';
    btnExpand.classList.add('hidden');
  }
}

export function toggleExpand(container, btnExpand, tx, inputMainArea) {
  isExpanded = !isExpanded;
  if (isExpanded) {
    container.classList.add('expanded');
    btnExpand.innerText = '⤡';
    btnExpand.title = 'Thu gọn';
  } else {
    container.classList.remove('expanded');
    btnExpand.innerText = '⤢';
    btnExpand.title = 'Mở rộng khung nhập liệu';
    updateInputLayout(tx, inputMainArea, btnExpand);
  }
}