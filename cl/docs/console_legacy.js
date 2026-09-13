// js/components/console.js

import { marked } from '../config.js';
import { openFileInEditor } from './editor.js';
import { getFileIcon } from '../utils/fileIcons.js';

let isStreaming = false;
let currentStreamBlock = null;
let currentStreamElem = null;
let currentStreamText = '';
let currentStreamThoughtText = '';


// -----------------------------------------------------------------
// P0 SECURITY HELPERS
// -----------------------------------------------------------------

function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}


function safeHttpUrl(
  value,
  {
    allowImageData = false,
  } = {}
) {
  const raw =
    String(value || '').trim();

  if (!raw) {
    return null;
  }


  if (
    allowImageData &&
    /^data:image\/(?:png|jpe?g|gif|webp|bmp|svg\+xml);base64,/i.test(raw)
  ) {
    return raw;
  }


  if (
    /^blob:/i.test(raw)
  ) {
    return raw;
  }


  try {
    const url =
      new URL(
        raw,
        window.location.href
      );


    if (
      url.protocol === 'http:' ||
      url.protocol === 'https:'
    ) {
      return url.href;
    }


    return null;

  } catch {
    return null;
  }
}


// -----------------------------------------------------------------
// MARKDOWN SANITIZATION
// -----------------------------------------------------------------

function sanitizeHtml(
  html = ''
) {
  const parser =
    new DOMParser();

  const doc =
    parser.parseFromString(
      String(html),
      'text/html'
    );


  doc.querySelectorAll(
    [
      'script',
      'iframe',
      'object',
      'embed',
      'form',
      'input',
      'textarea',
      'select',
      'option',
      'button',
      'style',
      'link',
      'meta',
      'base',
      'video[srcdoc]',
    ].join(',')
  ).forEach(
    (node) => node.remove()
  );


  doc.querySelectorAll(
    '*'
  ).forEach(
    (node) => {

      [
        ...node.attributes
      ].forEach(
        (attr) => {

          const name =
            attr.name.toLowerCase();

          const value =
            attr.value || '';


          // Event handlers
          if (
            name.startsWith('on') ||
            name === 'srcdoc'
          ) {
            node.removeAttribute(
              attr.name
            );

            return;
          }


          // HREF
          if (
            name === 'href'
          ) {
            const safe =
              safeHttpUrl(
                value
              );


            if (
              !safe &&
              !/^mailto:/i.test(
                value
              )
            ) {
              node.removeAttribute(
                attr.name
              );

            } else if (safe) {

              node.setAttribute(
                attr.name,
                safe
              );
            }

            return;
          }


          // SRC
          if (
            name === 'src' ||
            name === 'xlink:href'
          ) {

            const safe =
              safeHttpUrl(
                value,
                {
                  allowImageData:
                    node.tagName.toLowerCase()
                    === 'img',
                }
              );


            if (!safe) {
              node.removeAttribute(
                attr.name
              );

            } else {

              node.setAttribute(
                attr.name,
                safe
              );
            }
          }
        }
      );
    }
  );


  return doc.body.innerHTML;
}


function renderMarkdownSafe(
  text = ''
) {
  return sanitizeHtml(
    marked.parse(
      String(text || '')
    )
  );
}


// -----------------------------------------------------------------
// CALLBACKS
// -----------------------------------------------------------------

let onBlockActionCb = null;


export function setBlockActionCallback(
  cb
) {
  onBlockActionCb = cb;
}


function triggerBlockCallback(
  blockType,
  actionType,
  payload
) {
  if (
    typeof onBlockActionCb
    === 'function'
  ) {
    onBlockActionCb(
      blockType,
      actionType,
      payload
    );
  }
}


// =================================================================
// 1. PUBLIC APIS & STREAM MANAGEMENT
// =================================================================

export function showPendingIndicator() {

  const consoleElem =
    document.getElementById(
      'console'
    );


  removePendingIndicator();


  const pendingBlock =
    document.createElement(
      'div'
    );


  pendingBlock.id =
    'ai-pending-indicator';


  pendingBlock.className =
    'msg-block assistant is-pending';


  pendingBlock.innerHTML = `
    <div class="typing-indicator">
      <span></span>
      <span></span>
      <span></span>
    </div>
  `;


  consoleElem.appendChild(
    pendingBlock
  );


  consoleElem.scrollTop =
    consoleElem.scrollHeight;
}


export function removePendingIndicator() {

  const pending =
    document.getElementById(
      'ai-pending-indicator'
    );


  if (pending) {
    pending.remove();
  }
}


export function flushStream() {

  if (
    isStreaming &&
    currentStreamElem
  ) {

    currentStreamElem.innerHTML =
      renderMarkdownSafe(
        currentStreamText
      );


    currentStreamElem.classList.remove(
      'streaming-cursor'
    );


    if (currentStreamBlock) {

      currentStreamBlock.classList.remove(
        'is-streaming'
      );
    }


    addCopyButtons(
      currentStreamElem
    );


    finishThoughtBlock(
      currentStreamBlock
    );


    isStreaming = false;

    currentStreamBlock = null;

    currentStreamElem = null;

    currentStreamText = '';

    currentStreamThoughtText = '';
  }
}


/**
 * Hàm render block chính
 */
export function renderBlock(
  data
) {

  const consoleElem =
    document.getElementById(
      'console'
    );


  removePendingIndicator();


  // -------------------------------------------------------------
  // TIME DIVIDER
  // -------------------------------------------------------------

  if (
    data.type === 'time_divider'
  ) {

    const timeEl =
      document.createElement(
        'div'
      );


    timeEl.className =
      'msg-time-divider';


    timeEl.innerText =
      data.text ||
      data.content;


    consoleElem.appendChild(
      timeEl
    );


    return;
  }


  // -------------------------------------------------------------
  // STREAM END
  // -------------------------------------------------------------

  if (
    data.type === 'stream_end' &&
    data.role === 'assistant'
  ) {

    flushStream();

    return;
  }


  // -------------------------------------------------------------
  // ASSISTANT STREAM
  // -------------------------------------------------------------

  if (
    (
      data.type === 'stream_content' ||
      data.type === 'thought_stream'
    ) &&
    data.role === 'assistant'
  ) {

    handleStreamChunk(
      data,
      consoleElem
    );


    consoleElem.scrollTop =
      consoleElem.scrollHeight;


    return;
  }


  // -------------------------------------------------------------
  // NORMAL BLOCK
  // -------------------------------------------------------------

  flushStream();


  const role =
    data.role;


  const parts =
    normalizeToContentParts(
      data
    );


  parts.forEach(
    (part) => {

      const blockElem =
        createPartBlock(
          part,
          role
        );


      if (blockElem) {

        consoleElem.appendChild(
          blockElem
        );
      }
    }
  );


  consoleElem.scrollTop =
    consoleElem.scrollHeight;
}


// =================================================================
// 2. DATA NORMALIZER
// =================================================================

function normalizeToContentParts(
  data
) {

  if (
    Array.isArray(
      data.parts
    )
  ) {
    return data.parts;
  }


  if (
    Array.isArray(
      data.content
    )
  ) {
    return data.content;
  }


  const parts = [];


  // -------------------------------------------------------------
  // STRING CONTENT
  // -------------------------------------------------------------

  if (
    typeof data.content
    === 'string' &&
    data.content.trim()
  ) {

    parts.push({
      type: 'text',
      text: data.content,
    });


  } else if (
    typeof data.text
    === 'string' &&
    data.text.trim()
  ) {

    parts.push({
      type: 'text',
      text: data.text,
      thought:
        data.thought ||
        null,
    });
  }


  // -------------------------------------------------------------
  // ATTACHMENTS
  // -------------------------------------------------------------

  if (
    Array.isArray(
      data.files
    )
  ) {

    data.files.forEach(
      (f) => {

        parts.push({
          type: 'attachment',

          data: {

            filename:
              f.filename ||
              (
                f.path
                  ? f.path.split(
                    /[\\\/]/
                  ).pop()
                  : 'Attachment'
              ),

            uri:
              f.path ||
              null,

            base64_data:
              f.b64_data ||
              f.content ||
              null,

            mime_type:
              f.mime_type ||
              'application/octet-stream',

            size:
              f.size ||
              0,
          },
        });
      }
    );
  }


  return parts;
}


// =================================================================
// 3. PART BLOCK FACTORY
// =================================================================

function createPartBlock(
  part,
  role
) {

  const pType =
    part.type;


  switch (pType) {

    case 'text':

      return createTextBlock(
        role,
        part.text ||
        (
          part.data
            ? part.data.data
            : ''
        ),
        part.thought
      );


    case 'url':

      return createUrlBlock(
        role,
        part.data ||
        {
          url: part.text,
        }
      );


    case 'document':

    case 'attachment':

      return createFileBlock(
        role,
        part.data?.attachment ||
        part.data
      );


    case 'image':

      return createImageBlock(
        role,
        part.data?.attachment ||
        part.data
      );


    case 'audio':

    case 'video':

      return createMediaBlock(
        role,
        pType,
        part.data?.attachment ||
        part.data
      );


    default:

      if (part.text) {

        return createTextBlock(
          role,
          part.text
        );
      }


      return null;
  }
}


// =================================================================
// 4. TEXT BLOCK
// =================================================================

function createTextBlock(
  role,
  text = '',
  thought = null,
  allowEmpty = false
) {

  if (
    !text &&
    !thought &&
    !allowEmpty
  ) {
    return null;
  }


  const block =
    document.createElement(
      'div'
    );


  block.className =
    `msg-block ${role} msg-text-part`;


  block.dataset.role =
    role;


  block.dataset.rawText =
    text;


  // -------------------------------------------------------------
  // SYSTEM
  // -------------------------------------------------------------

  if (
    role === 'system'
  ) {

    block.innerHTML = `
      <div class="msg-system-content">
        <span class="sys-icon">⚙️</span>
        <div class="msg-body">
          ${renderMarkdownSafe(text)}
        </div>
      </div>
    `;


    return block;
  }


  // -------------------------------------------------------------
  // ASSISTANT / USER
  // -------------------------------------------------------------

  const htmlContent =
    text
      ? renderMarkdownSafe(text)
      : '';


  block.innerHTML = `
    <div class="msg-bubble">

      <div
        class="msg-thought-placeholder">
      </div>

      <div class="msg-body">
        ${htmlContent}
      </div>

    </div>

    <div class="msg-options">

      <button
        class="opt-btn btn-copy"
        data-tooltip="Sao chép văn bản">
        📋
      </button>

      <button
        class="opt-btn btn-quote"
        data-tooltip="Trích dẫn">
        💬
      </button>

      <div class="more-dropdown-wrap">

        <button
          class="opt-btn btn-more"
          data-tooltip="Tùy chọn khác">
          ⋮
        </button>

        <div class="download-menu more-menu hidden">

          <button
            class="dl-option"
            data-type="md">
            📝 Tải về (.md)
          </button>

          <button
            class="dl-option"
            data-type="txt">
            📄 Tải về (.txt)
          </button>

        </div>

      </div>

    </div>
  `;


  if (thought) {

    appendThoughtToBlock(
      block,
      thought
    );
  }


  addCopyButtons(
    block
  );


  bindTextOptionsEvents(
    block
  );


  return block;
}


// =================================================================
// 5. FILE BLOCK
// =================================================================

function createFileBlock(
  role,
  attachment
) {

  if (!attachment) {
    return null;
  }


  const block =
    document.createElement(
      'div'
    );


  block.className =
    `msg-block ${role} msg-file-part`;


  const fileName =
    attachment.filename ||
    (
      attachment.uri
        ? attachment.uri
          .split(/[\\\/]/)
          .pop()
        : 'File_Attachment'
    );


  const safeFileName =
    escapeHtml(
      fileName
    );


  const sizeText =
    attachment.size
      ? ` (${(
        attachment.size /
        1024
      ).toFixed(1)} KB)`
      : '';


  const icon =
    getFileIcon(
      fileName
    );


  block.innerHTML = `
    <div
      class="msg-file-chip"
      title="${safeFileName}${escapeHtml(sizeText)}">

      <span class="file-icon">
        ${icon}
      </span>

      <span class="file-name">
        ${safeFileName}
      </span>

      <div class="file-actions">

        <span
          class="file-action-btn btn-view-file"
          title="Xem nhanh (Read-only)">
          👁️
        </span>

        <span
          class="file-action-btn btn-download-file"
          title="Tải tệp về">
          ⬇️
        </span>

      </div>

    </div>
  `;


  block.querySelector(
    '.btn-view-file'
  ).addEventListener(
    'click',
    (e) => {

      e.stopPropagation();


      triggerBlockCallback(
        'file',
        'view',
        attachment
      );


      openFileInEditor(
        attachment.uri ||
        null,

        fileName,

        true,

        attachment.base64_data ||
        null
      );
    }
  );


  block.querySelector(
    '.btn-download-file'
  ).addEventListener(
    'click',
    (e) => {

      e.stopPropagation();


      triggerBlockCallback(
        'file',
        'download',
        attachment
      );


      triggerFileDownload(
        fileName,

        attachment.base64_data
          ? `base64:${attachment.base64_data}`
          : (
            attachment.uri ||
            ''
          ),

        attachment.mime_type ||
        'application/octet-stream'
      );
    }
  );


  return block;
}


// =================================================================
// 6. URL BLOCK
// =================================================================

function createUrlBlock(
  role,
  urlData
) {

  const block =
    document.createElement(
      'div'
    );


  block.className =
    `msg-block ${role} msg-url-part`;


  const rawUrl =
    typeof urlData === 'string'
      ? urlData
      : (
        urlData?.url ||
        ''
      );


  const targetUrl =
    safeHttpUrl(
      rawUrl
    );


  if (!targetUrl) {
    return null;
  }


  const title =
    typeof urlData === 'string'
      ? urlData
      : (
        urlData?.title ||
        targetUrl
      );


  let domain =
    targetUrl;


  try {

    domain =
      new URL(
        targetUrl
      ).hostname;

  } catch (_) { }


  block.innerHTML = `
    <div class="url-card">

      <div class="url-card-icon">
        🌐
      </div>

      <div class="url-card-info">

        <a
          href="${escapeHtml(targetUrl)}"
          target="_blank"
          rel="noopener noreferrer"
          class="url-card-title">
          ${escapeHtml(title)}
        </a>

        <span class="url-card-domain">
          ${escapeHtml(domain)}
        </span>

      </div>

      <div class="url-card-actions">

        <button
          class="opt-btn btn-open-url"
          title="Mở trang web">
          🔗
        </button>

        <button
          class="opt-btn btn-copy-url"
          title="Sao chép liên kết">
          📋
        </button>

      </div>

    </div>
  `;


  block.querySelector(
    '.btn-open-url'
  ).addEventListener(
    'click',
    () => {

      triggerBlockCallback(
        'url',
        'open',
        urlData
      );


      window.open(
        targetUrl,
        '_blank',
        'noopener,noreferrer'
      );
    }
  );


  block.querySelector(
    '.btn-copy-url'
  ).addEventListener(
    'click',
    (e) => {

      navigator.clipboard.writeText(
        targetUrl
      );


      triggerBlockCallback(
        'url',
        'copy',
        urlData
      );


      e.target.innerText =
        '✔';


      setTimeout(
        () => {
          e.target.innerText =
            '📋';
        },
        1500
      );
    }
  );


  return block;
}


// =================================================================
// 7. IMAGE BLOCK
// =================================================================

function createImageBlock(
  role,
  imageData
) {

  if (!imageData) {
    return null;
  }


  const block =
    document.createElement(
      'div'
    );


  block.className =
    `msg-block ${role} msg-image-part`;


  const rawSrc =
    imageData.uri ||
    (
      imageData.base64_data
        ? `data:${imageData.mime_type ||
        'image/png'
        };base64,${imageData.base64_data
        }`
        : ''
    );


  const imgSrc =
    safeHttpUrl(
      rawSrc,
      {
        allowImageData: true,
      }
    );


  if (!imgSrc) {
    return null;
  }


  const fileName =
    imageData.filename ||
    'Image';


  block.innerHTML = `
    <div class="msg-image-container">

      <img
        src="${escapeHtml(imgSrc)}"
        alt="${escapeHtml(fileName)}"
        class="msg-image-preview" />

      <div
        class="image-overlay-actions">

        <button
          class="opt-btn btn-view-image"
          title="Xem ảnh lớn">
          🔍
        </button>

        <button
          class="opt-btn btn-download-image"
          title="Tải ảnh">
          ⬇️
        </button>

      </div>

    </div>
  `;


  block.querySelector(
    '.btn-view-image'
  ).addEventListener(
    'click',
    () => {

      triggerBlockCallback(
        'image',
        'view',
        imageData
      );


      window.open(
        imgSrc,
        '_blank',
        'noopener,noreferrer'
      );
    }
  );


  block.querySelector(
    '.btn-download-image'
  ).addEventListener(
    'click',
    () => {

      triggerBlockCallback(
        'image',
        'download',
        imageData
      );


      triggerFileDownload(
        fileName,
        imgSrc,
        imageData.mime_type ||
        'image/png'
      );
    }
  );


  return block;
}


// =================================================================
// 8. MEDIA BLOCK
// =================================================================

function createMediaBlock(
  role,
  mediaType,
  mediaData
) {

  if (
    !mediaData ||
    ![
      'audio',
      'video'
    ].includes(mediaType)
  ) {
    return null;
  }


  const block =
    document.createElement(
      'div'
    );


  block.className =
    `msg-block ${role} msg-media-part`;


  const rawSrc =
    mediaData.uri ||
    (
      mediaData.base64_data
        ? `data:${mediaData.mime_type ||
        'application/octet-stream'
        };base64,${mediaData.base64_data
        }`
        : ''
    );


  const mediaSrc =
    safeHttpUrl(
      rawSrc,
      {
        allowImageData: false,
      }
    );


  if (!mediaSrc) {
    return null;
  }


  block.innerHTML = `
    <div class="media-wrapper">

      <${mediaType}
        controls
        src="${escapeHtml(mediaSrc)}"
        class="custom-${mediaType}-player">
      </${mediaType}>

    </div>
  `;


  return block;
}


// =================================================================
// 9. STREAM HANDLER
// =================================================================

function handleStreamChunk(
  data,
  consoleElem
) {

  if (!isStreaming) {

    const parts =
      normalizeToContentParts(
        data
      );


    parts.forEach(
      (p) => {

        if (
          p.type !== 'text'
        ) {

          const b =
            createPartBlock(
              p,
              'assistant'
            );


          if (b) {
            consoleElem.appendChild(
              b
            );
          }
        }
      }
    );


    currentStreamBlock =
      createTextBlock(
        'assistant',
        '',
        null,
        true
      );


    if (currentStreamBlock) {

      currentStreamBlock.classList.add(
        'is-streaming'
      );


      consoleElem.appendChild(
        currentStreamBlock
      );


      currentStreamElem =
        currentStreamBlock.querySelector(
          '.msg-body'
        );


      currentStreamElem.classList.add(
        'streaming-cursor'
      );
    }


    currentStreamText = '';

    currentStreamThoughtText = '';

    isStreaming = true;
  }


  // -------------------------------------------------------------
  // THOUGHT
  // -------------------------------------------------------------

  const thoughtChunk =
    data.type === 'thought_stream'
      ? data.text
      : null;


  if (
    thoughtChunk &&
    currentStreamBlock
  ) {

    currentStreamThoughtText +=
      thoughtChunk;


    appendThoughtToBlock(
      currentStreamBlock,
      currentStreamThoughtText
    );
  }


  // -------------------------------------------------------------
  // ANSWER
  // -------------------------------------------------------------

  const textChunk =
    data.type === 'stream_content'
      ? data.text
      : null;


  if (textChunk) {

    finishThoughtBlock(
      currentStreamBlock
    );


    currentStreamText +=
      textChunk;


    if (currentStreamElem) {

      currentStreamElem.innerHTML =
        renderMarkdownSafe(
          currentStreamText
        );
    }
  }
}


// =================================================================
// 10. TEXT EVENTS
// =================================================================

function bindTextOptionsEvents(
  block
) {

  const btnCopy =
    block.querySelector(
      '.btn-copy'
    );


  if (btnCopy) {

    btnCopy.addEventListener(
      'click',
      () => {

        const text =
          block
            .querySelector(
              '.msg-body'
            )
            .innerText;


        navigator.clipboard.writeText(
          text
        );


        btnCopy.innerText =
          '✔';


        setTimeout(
          () => {
            btnCopy.innerText =
              '📋';
          },
          2000
        );
      }
    );
  }


  const btnQuote =
    block.querySelector(
      '.btn-quote'
    );


  if (btnQuote) {

    btnQuote.addEventListener(
      'click',
      () => {

        const text =
          block
            .querySelector(
              '.msg-body'
            )
            .innerText;


        const tx =
          document.getElementById(
            'user-input'
          );


        if (tx) {

          tx.value =
            `> ${text
              .split('\n')
              .join('\n> ')
            }\n\n` +
            tx.value;


          tx.focus();
        }
      }
    );
  }


  const btnMore =
    block.querySelector(
      '.btn-more'
    );


  const moreMenu =
    block.querySelector(
      '.more-menu'
    );


  if (
    btnMore &&
    moreMenu
  ) {

    btnMore.addEventListener(
      'click',
      (e) => {

        e.stopPropagation();


        document
          .querySelectorAll(
            '.download-menu'
          )
          .forEach(
            (m) => {

              if (
                m !== moreMenu
              ) {
                m.classList.add(
                  'hidden'
                );
              }
            }
          );


        moreMenu.classList.toggle(
          'hidden'
        );
      }
    );


    moreMenu
      .querySelectorAll(
        '.dl-option'
      )
      .forEach(
        (btn) => {

          btn.addEventListener(
            'click',
            (e) => {

              e.stopPropagation();


              const format =
                btn.dataset.type;


              const msgBody =
                block.querySelector(
                  '.msg-body'
                );


              const contentToSave =
                format === 'md'
                  ? (
                    block.dataset.rawText ||
                    msgBody.innerText
                  )
                  : msgBody.innerText;


              const timestamp =
                new Date()
                  .toISOString()
                  .replace(
                    /[:.]/g,
                    '-'
                  )
                  .slice(
                    0,
                    19
                  );


              const filename =
                `response_${timestamp}.${format}`;


              triggerBlockCallback(
                'text',
                'download',
                {
                  format,
                  filename,
                }
              );


              triggerFileDownload(
                filename,
                contentToSave,
                format === 'md'
                  ? 'text/markdown;charset=utf-8'
                  : 'text/plain;charset=utf-8'
              );


              moreMenu.classList.add(
                'hidden'
              );
            }
          );
        }
      );
  }


  block.addEventListener(
    'mouseleave',
    () => {

      if (moreMenu) {
        moreMenu.classList.add(
          'hidden'
        );
      }
    }
  );


  // -------------------------------------------------------------
  // USER LONG MESSAGE COLLAPSE
  // -------------------------------------------------------------

  if (
    block.classList.contains(
      'user'
    )
  ) {

    const msgBody =
      block.querySelector(
        '.msg-body'
      );


    if (msgBody) {

      const lineCount =
        msgBody.innerText
          .split('\n')
          .length;


      const maxHeight20Lines =
        448;


      if (
        lineCount > 20 ||
        msgBody.scrollHeight >
        maxHeight20Lines
      ) {

        msgBody.classList.add(
          'collapsed-user-msg'
        );


        const bubbleElem =
          block.querySelector(
            '.msg-bubble'
          ) ||
          block;


        let toggleBtn =
          bubbleElem.querySelector(
            '.btn-toggle-user-msg'
          );


        if (!toggleBtn) {

          toggleBtn =
            document.createElement(
              'button'
            );


          toggleBtn.className =
            'btn-toggle-user-msg';


          toggleBtn.innerHTML = `
            <span>
              Xem thêm
            </span>

            <svg
              class="toggle-icon"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round">

              <polyline
                points="6 9 12 15 18 9">
              </polyline>

            </svg>
          `;


          bubbleElem.appendChild(
            toggleBtn
          );
        }


        toggleBtn.addEventListener(
          'click',
          () => {

            const isCollapsed =
              msgBody.classList.toggle(
                'collapsed-user-msg'
              );


            toggleBtn.classList.toggle(
              'expanded',
              !isCollapsed
            );


            toggleBtn
              .querySelector(
                'span'
              )
              .textContent =
              isCollapsed
                ? 'Xem thêm'
                : 'Thu gọn';
          }
        );
      }
    }
  }
}


// =================================================================
// 11. GLOBAL MENU DISMISS
// =================================================================

document.addEventListener(
  'click',
  () => {

    document
      .querySelectorAll(
        '.download-menu'
      )
      .forEach(
        (m) => {

          m.classList.add(
            'hidden'
          );
        }
      );
  }
);


// =================================================================
// 12. FILE DOWNLOAD
// =================================================================

function triggerFileDownload(
  filename,
  content = '',
  mimeType = ''
) {

  const value =
    String(content || '');


  let url =
    value;


  let isCreatedUrl =
    false;


  try {

    // -----------------------------------------------------------
    // DATA URI
    // -----------------------------------------------------------

    if (
      value.startsWith(
        'data:'
      )
    ) {

      url =
        value;
    }


    // -----------------------------------------------------------
    // BASE64
    // -----------------------------------------------------------

    else if (
      /^base64:/i.test(
        value
      )
    ) {

      const base64 =
        value.slice(
          value.indexOf(':') + 1
        );


      const binary =
        atob(base64);


      const bytes =
        Uint8Array.from(
          binary,
          (char) =>
            char.charCodeAt(0)
        );


      const blob =
        new Blob(
          [bytes],
          {
            type:
              mimeType ||
              'application/octet-stream',
          }
        );


      url =
        URL.createObjectURL(
          blob
        );


      isCreatedUrl =
        true;
    }


    // -----------------------------------------------------------
    // HTTP/BLOB
    // -----------------------------------------------------------

    else if (
      /^https?:/i.test(value) ||
      /^blob:/i.test(value)
    ) {

      url =
        value;
    }


    // -----------------------------------------------------------
    // TEXT
    // -----------------------------------------------------------

    else {

      const blob =
        new Blob(
          [value],
          {
            type:
              mimeType ||
              'text/plain;charset=utf-8',
          }
        );


      url =
        URL.createObjectURL(
          blob
        );


      isCreatedUrl =
        true;
    }


    const a =
      document.createElement(
        'a'
      );


    a.href =
      url;


    a.download =
      filename;


    document.body.appendChild(
      a
    );


    a.click();


    document.body.removeChild(
      a
    );

  } finally {

    if (
      isCreatedUrl
    ) {

      URL.revokeObjectURL(
        url
      );
    }
  }
}


// =================================================================
// 13. THOUGHT TITLE
// =================================================================

function extractLatestStepTitle(
  thoughtText
) {

  if (!thoughtText) {
    return 'Đang khởi tạo...';
  }


  const lines =
    thoughtText
      .split('\n')
      .map(
        (l) => l.trim()
      )
      .filter(
        Boolean
      );


  for (
    let i = lines.length - 1;
    i >= 0;
    i--
  ) {

    const line =
      lines[i];


    const headingMatch =
      line.match(
        /^(?:#{1,6}\s*|\*{2})(.*?)(?:\*{2})?$/
      );


    if (
      headingMatch &&
      headingMatch[1].trim()
    ) {

      return headingMatch[1]
        .replace(
          /[*#]/g,
          ''
        )
        .trim();
    }
  }


  const lastLine =
    lines[
    lines.length - 1
    ] ||
    '';


  const cleanText =
    lastLine
      .replace(
        /<[^>]*>?/gm,
        ''
      )
      .replace(
        /[*_~`]/g,
        ''
      );


  return cleanText.length > 45
    ? cleanText.substring(
      0,
      45
    ) + '...'
    : (
      cleanText ||
      'Đang xử lý...'
    );
}


// =================================================================
// 14. THOUGHT RENDERER
// =================================================================

function appendThoughtToBlock(
  blockElem,
  thoughtText
) {

  const bubbleElem =
    blockElem.querySelector(
      '.msg-bubble'
    ) ||
    blockElem;


  let thoughtContainer =
    bubbleElem.querySelector(
      '.thought-container'
    );


  if (!thoughtContainer) {

    const placeholder =
      bubbleElem.querySelector(
        '.msg-thought-placeholder'
      );


    thoughtContainer =
      document.createElement(
        'details'
      );


    thoughtContainer.className =
      'thought-container';


    thoughtContainer.innerHTML = `
      <summary class="thought-summary">

        <div class="thought-summary-left">

          <span
            class="thought-badge is-thinking">

            <span
              class="thought-pulse-dot">
            </span>

            <span
              class="badge-text">
              Suy nghĩ
            </span>

          </span>


          <span
            class="current-step-title">
            Đang khởi tạo...
          </span>

        </div>


        <svg
          class="thought-chevron"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round">

          <polyline
            points="6 9 12 15 18 9">
          </polyline>

        </svg>

      </summary>


      <div
        class="thought-content-wrapper">

        <div
          class="thought-content">
        </div>

      </div>
    `;


    if (placeholder) {

      placeholder.replaceWith(
        thoughtContainer
      );

    } else {

      bubbleElem.prepend(
        thoughtContainer
      );
    }
  }


  const badgeElem =
    thoughtContainer.querySelector(
      '.thought-badge'
    );


  if (
    badgeElem &&
    !badgeElem.classList.contains(
      'is-thinking'
    )
  ) {

    badgeElem.classList.add(
      'is-thinking'
    );
  }


  const stepTitleElem =
    thoughtContainer.querySelector(
      '.current-step-title'
    );


  const newTitle =
    extractLatestStepTitle(
      thoughtText
    );


  if (
    stepTitleElem &&
    stepTitleElem.textContent !==
    newTitle
  ) {

    stepTitleElem.classList.remove(
      'step-swap-anim'
    );


    void stepTitleElem.offsetWidth;


    stepTitleElem.textContent =
      newTitle;


    stepTitleElem.classList.add(
      'step-swap-anim'
    );
  }


  const contentElem =
    thoughtContainer.querySelector(
      '.thought-content'
    );


  if (contentElem) {

    contentElem.innerHTML =
      renderMarkdownSafe(
        thoughtText
      );
  }
}


// =================================================================
// 15. FINISH THOUGHT
// =================================================================

export function finishThoughtBlock(
  blockElem
) {

  if (!blockElem) {
    return;
  }


  const badgeElem =
    blockElem.querySelector(
      '.thought-badge'
    );


  if (badgeElem) {

    badgeElem.classList.remove(
      'is-thinking'
    );
  }
}


// =================================================================
// 16. CODE COPY BUTTONS
// =================================================================

function addCopyButtons(
  container
) {

  container
    .querySelectorAll(
      'pre'
    )
    .forEach(
      (pre) => {

        if (
          pre.querySelector(
            '.copy-btn'
          )
        ) {
          return;
        }


        const btn =
          document.createElement(
            'button'
          );


        btn.className =
          'copy-btn';


        btn.innerText =
          '📋 Copy';


        btn.onclick =
          () => {

            const code =
              pre.querySelector(
                'code'
              )
                ? pre
                  .querySelector(
                    'code'
                  )
                  .innerText
                : pre.innerText;


            navigator.clipboard.writeText(
              code
            );


            btn.innerText =
              '✔ Copied!';


            setTimeout(
              () => {

                btn.innerText =
                  '📋 Copy';

              },
              2000
            );
          };


        pre.appendChild(
          btn
        );
      }
    );
}