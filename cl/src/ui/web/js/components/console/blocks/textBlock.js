import { renderMarkdownSafe } from '../../../utils/sanitizer.js';
import { triggerFileDownload } from '../../../utils/download.js';
import { appendThoughtToBlock } from './thoughtBlock.js';
import { prepareTextWithCitations, appendCitationsToBlock } from './citationBlock.js';

export function createTextBlock(role, text = '', thought = null, citations = null, allowEmpty = false, triggerBlockCallback = null) {
  if (!text && !thought && !citations && !allowEmpty) return null;

  const block = document.createElement('div');
  block.className = `msg-block ${role} msg-text-part`;
  block.dataset.role = role;
  block.dataset.rawText = text;

  if (role === 'system') {
    block.innerHTML = `
      <div class="msg-system-content">
        <span class="sys-icon">⚙️</span>
        <div class="msg-body">${renderMarkdownSafe(text)}</div>
      </div>
    `;
    return block;
  }

  const processedText = (citations && text) ? prepareTextWithCitations(text, citations) : text;
  const htmlContent = processedText ? renderMarkdownSafe(processedText) : '';

  block.innerHTML = `
    <div class="msg-bubble">
      <div class="msg-thought-placeholder"></div>
      <div class="msg-body">${htmlContent}</div>
    </div>
    <div class="msg-options">
      <button class="opt-btn btn-copy" data-tooltip="Sao chép văn bản">📋</button>
      <button class="opt-btn btn-quote" data-tooltip="Trích dẫn">💬</button>
      <div class="more-dropdown-wrap">
        <button class="opt-btn btn-more" data-tooltip="Tùy chọn khác">⋮</button>
        <div class="download-menu more-menu hidden">
          <button class="dl-option" data-type="md">📝 Tải về (.md)</button>
          <button class="dl-option" data-type="txt">📄 Tải về (.txt)</button>
        </div>
      </div>
    </div>
  `;

  if (thought) appendThoughtToBlock(block, thought);
  if (citations) appendCitationsToBlock(block, citations);

  enhanceCodeBlocks(block);
  bindTextOptionsEvents(block, triggerBlockCallback);

  return block;
}

export function enhanceCodeBlocks(container) {
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.code-header')) return;

    const codeEl = pre.querySelector('code');
    if (!codeEl) return;

    // 1. Lấy mã nguồn gốc trước khi Highlight.js biến đổi DOM
    const rawCode = codeEl.textContent;

    // 2. Lấy tên ngôn ngữ từ class (Ví dụ: language-cpp -> CPP)
    let lang = 'CODE';
    const match = codeEl.className.match(/(?:language|lang)-([a-zA-Z0-9_+-]+)/);
    if (match) {
      lang = match[1].toUpperCase();
    }

    // 3. Thực hiện tô màu cú pháp bằng Highlight.js
    if (window.hljs) {
      window.hljs.highlightElement(codeEl);
    }

    // 4. Tạo Header chứa tên ngôn ngữ và nút Copy
    const header = document.createElement('div');
    header.className = 'code-header';

    const langSpan = document.createElement('span');
    langSpan.className = 'code-lang';
    langSpan.innerText = lang;

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.innerText = '📋 Copy';
    btn.onclick = () => {
      navigator.clipboard.writeText(rawCode);
      btn.innerText = '✔ Copied!';
      setTimeout(() => { btn.innerText = '📋 Copy'; }, 2000);
    };

    header.appendChild(langSpan);
    header.appendChild(btn);

    // 5. Chèn Header vào đầu khối <pre>
    pre.insertBefore(header, pre.firstChild);
  });
}

function bindTextOptionsEvents(block, triggerBlockCallback) {
  const btnCopy = block.querySelector('.btn-copy');
  if (btnCopy) {
    btnCopy.addEventListener('click', () => {
      const text = block.querySelector('.msg-body').innerText;
      navigator.clipboard.writeText(text);
      btnCopy.innerText = '✔';
      setTimeout(() => { btnCopy.innerText = '📋'; }, 2000);
    });
  }

  const btnQuote = block.querySelector('.btn-quote');
  if (btnQuote) {
    btnQuote.addEventListener('click', () => {
      const text = block.querySelector('.msg-body').innerText;
      const tx = document.getElementById('user-input');
      if (tx) {
        tx.value = `> ${text.split('\n').join('\n> ')}\n\n` + tx.value;
        tx.focus();
      }
    });
  }

  const btnMore = block.querySelector('.btn-more');
  const moreMenu = block.querySelector('.more-menu');
  if (btnMore && moreMenu) {
    btnMore.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.download-menu').forEach(m => { if (m !== moreMenu) m.classList.add('hidden'); });
      moreMenu.classList.toggle('hidden');
    });

    moreMenu.querySelectorAll('.dl-option').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const format = btn.dataset.type;
        const msgBody = block.querySelector('.msg-body');
        const contentToSave = format === 'md' ? (block.dataset.rawText || msgBody.innerText) : msgBody.innerText;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const filename = `response_${timestamp}.${format}`;

        if (triggerBlockCallback) triggerBlockCallback('text', 'download', { format, filename });
        triggerFileDownload(filename, contentToSave, format === 'md' ? 'text/markdown;charset=utf-8' : 'text/plain;charset=utf-8');
        moreMenu.classList.add('hidden');
      });
    });
  }

  block.addEventListener('mouseleave', () => {
    if (moreMenu) moreMenu.classList.add('hidden');
  });
}