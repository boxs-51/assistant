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
const seenAgentEvents = new Set();

function renderAgentEvent(event, consoleElem) {
  if (!event || event.object !== 'agent_stream_event' || seenAgentEvents.has(event.event_id)) return;
  seenAgentEvents.add(event.event_id);
  const detail = event.data || {};

  if (event.channel === 'tool') {
    const toolId = String(detail.tool_call_id || event.event_id);
    const executionId = String(event.execution_id || '');
    let block = Array.from(consoleElem.querySelectorAll('.agent-tool-activity')).find(
      item => item.dataset.toolCallId === toolId && item.dataset.executionId === executionId
    );
    if (!block) {
      block = document.createElement('div');
      block.className = 'agent-activity agent-tool-activity';
      block.dataset.toolCallId = toolId;
      block.dataset.executionId = executionId;
      const title = document.createElement('div');
      title.className = 'agent-activity-title';
      block.appendChild(title);
      const purpose = document.createElement('div');
      purpose.className = 'agent-activity-purpose';
      block.appendChild(purpose);
      const args = document.createElement('pre');
      args.className = 'agent-activity-arguments';
      block.appendChild(args);
      consoleElem.appendChild(block);
    }
    const status = String(detail.status || 'requested');
    const labels = { requested: 'Đã yêu cầu', started: 'Đang chạy', completed: 'Hoàn thành', failed: 'Thất bại' };
    block.dataset.status = status;
    block.querySelector('.agent-activity-title').textContent = `${labels[status] || status}: ${detail.name || 'tool'}`;
    if (detail.purpose) block.querySelector('.agent-activity-purpose').textContent = detail.purpose;
    if (detail.arguments) block.querySelector('.agent-activity-arguments').textContent = JSON.stringify(detail.arguments, null, 2);
    if (detail.error_code) block.querySelector('.agent-activity-purpose').textContent += ` (${detail.error_code})`;
    return;
  }

  if (event.channel === 'response' && detail.content) {
    const block = createTextBlock('assistant', detail.content, null, null, false, triggerBlockCallback);
    if (block) consoleElem.appendChild(block);
  }
}

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

export function clearConversationConsole() {
  flushStream();
  removePendingIndicator();
  seenAgentEvents.clear();
  const consoleElem = document.getElementById('console');
  if (!consoleElem) return;
  if (typeof consoleElem.replaceChildren === 'function') {
    consoleElem.replaceChildren();
  } else {
    consoleElem.innerHTML = '';
  }
}

export function replaceConversationHistory(messages = []) {
  clearConversationConsole();
  const ordered = (Array.isArray(messages) ? messages : [])
    .map((message, index) => ({ message, index }))
    .sort((left, right) => {
      const leftSequence = Number(left.message?.sequence);
      const rightSequence = Number(right.message?.sequence);
      const leftRank = Number.isFinite(leftSequence) ? leftSequence : Number.MAX_SAFE_INTEGER;
      const rightRank = Number.isFinite(rightSequence) ? rightSequence : Number.MAX_SAFE_INTEGER;
      return leftRank === rightRank ? left.index - right.index : leftRank - rightRank;
    })
    .map(({ message }) => message);

  ordered.forEach((message) => {
    const role = message?.role || 'assistant';
    const content = message?.content;
    if (typeof content === 'string') {
      renderBlock({ role, text: content });
      return;
    }
    if (content !== null && content !== undefined) {
      const parts = Array.isArray(content) ? content : [content];
      renderBlock({
        role,
        data: {
          response: {
            choices: [{ message: { content: parts } }],
          },
        },
      });
    }
  });
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

  if (data.type === 'agent_event') {
    renderAgentEvent(data.data, consoleElem);
    consoleElem.scrollTop = consoleElem.scrollHeight;
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
    case 'file':
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
