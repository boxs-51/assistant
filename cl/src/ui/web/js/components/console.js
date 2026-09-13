import { normalizeToContentParts } from './console/normalizer.js';
import { StreamManager } from './console/streamHandler.js';
import { createTextBlock } from './console/blocks/textBlock.js';
import { createFileBlock } from './console/blocks/fileBlock.js';
import { createUrlBlock } from './console/blocks/urlBlock.js';
import { createImageBlock } from './console/blocks/imageBlock.js';
import { createMediaBlock } from './console/blocks/mediaBlock.js';
import { finishThoughtBlock, createThoughtBlock } from './console/blocks/thoughtBlock.js';

let onBlockActionCb = null;

export function setBlockActionCallback(cb) {
  onBlockActionCb = cb;
}

function triggerBlockCallback(blockType, actionType, payload) {
  if (typeof onBlockActionCb === 'function') {
    onBlockActionCb(blockType, actionType, payload);
  }
}

const streamManager = new StreamManager(createPartBlock);

export function showPendingIndicator() {
  const consoleElem = document.getElementById('console');
  removePendingIndicator();

  const pendingBlock = document.createElement('div');
  pendingBlock.id = 'ai-pending-indicator';
  pendingBlock.className = 'msg-block assistant is-pending';
  pendingBlock.innerHTML = `
    <div class="typing-indicator">
      <span></span><span></span><span></span>
    </div>
  `;

  consoleElem.appendChild(pendingBlock);
  consoleElem.scrollTop = consoleElem.scrollHeight;
}

export function removePendingIndicator() {
  const pending = document.getElementById('ai-pending-indicator');
  if (pending) pending.remove();
}

export function flushStream() {
  streamManager.flushStream();
}

function mergeThoughtWithTextParts(parts) {
  const merged = [];
  
  for (let i = 0; i < parts.length; i++) {
    const current = parts[i];
    const next = parts[i + 1];

    if (current.type === 'thinking' && next && next.type === 'text') {
      merged.push({
        ...next,
        thought: current.text || current.thought
      });
      i++;
    } else {
      merged.push(current);
    }
  }

  return merged;
}

export function renderBlock(data) {
  const consoleElem = document.getElementById('console');
  removePendingIndicator();

  if (data.type === 'time_divider') {
    const timeEl = document.createElement('div');
    timeEl.className = 'msg-time-divider';
    timeEl.innerText = data.text || data.content;
    consoleElem.appendChild(timeEl);
    return;
  }

  if (data.type === 'stream_end' && data.role === 'assistant') {
    flushStream();
    return;
  }

  if (data.type === 'stream_content' && data.role === 'assistant') {
    streamManager.handleStreamChunk(data, consoleElem, triggerBlockCallback);
    consoleElem.scrollTop = consoleElem.scrollHeight;
    return;
  }

  flushStream();

  const role = data.role;
  const rawParts = normalizeToContentParts(data);
  const parts = mergeThoughtWithTextParts(rawParts);

  parts.forEach(part => {
    const blockElem = createPartBlock(part, role);
    if (blockElem) consoleElem.appendChild(blockElem);
  });

  consoleElem.scrollTop = consoleElem.scrollHeight;
}

function createPartBlock(part, role) {
  const pType = part.type;

  switch (pType) {
    case 'thinking': 
      return createThoughtBlock(role, part.text || part.thought);

    case 'text': {
      const blockElem = createTextBlock(
        role,
        part.text,
        part.thought || null,
        part.metadata?.citations || null,
        false,
        triggerBlockCallback
      );

      if (blockElem && part.thought) {
        finishThoughtBlock(blockElem);
      }
      return blockElem;
    }

    case 'url':
      return createUrlBlock(role, part.data || { url: part.text }, triggerBlockCallback);

    case 'document':
    case 'attachment':
      return createFileBlock(role, part.data?.attachment || part.data, triggerBlockCallback);

    case 'image':
      return createImageBlock(role, part.data?.attachment || part.data, triggerBlockCallback);

    case 'audio':
    case 'video':
      return createMediaBlock(role, pType, part.data?.attachment || part.data);

    default:
      if (part.text) {
        return createTextBlock(role, part.text, null, part.citations || null, false, triggerBlockCallback);
      }
      return null;
  }
}

document.addEventListener('click', () => {
  document.querySelectorAll('.download-menu').forEach(m => m.classList.add('hidden'));
});