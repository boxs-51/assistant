import { renderMarkdownSafe } from '../../utils/sanitizer.js';
import { normalizeToContentParts } from './normalizer.js';
import { createTextBlock, enhanceCodeBlocks } from './blocks/textBlock.js';
import { appendThoughtToBlock, finishThoughtBlock } from './blocks/thoughtBlock.js';
import { appendCitationsToBlock, updateBlockWithCitations } from './blocks/citationBlock.js';

export class StreamManager {
  constructor(createPartBlockFn) {
    this.isStreaming = false;
    this.currentStreamBlock = null;
    this.currentStreamElem = null;
    this.currentStreamText = '';
    this.currentStreamThoughtText = '';
    this.currentStreamCitations = null;
    this.createPartBlock = createPartBlockFn;
  }

  flushStream() {
    if (this.isStreaming && this.currentStreamElem) {
      // 1. Render Markdown với văn bản mới nhất (đã chèn [1], [2] nếu có)
      this.currentStreamElem.innerHTML = renderMarkdownSafe(this.currentStreamText);
      this.currentStreamElem.classList.remove('streaming-cursor');

      if (this.currentStreamBlock) {
        this.currentStreamBlock.classList.remove('is-streaming');

        // 2. Nếu có trích dẫn, gán lại citation chip sau khi render Markdown
        if (this.currentStreamCitations) {
          appendCitationsToBlock(this.currentStreamBlock, this.currentStreamCitations);
        }

        // 3. Nâng cấp các khối code (Header + Tên định dạng + Highlight.js + Copy button)
        enhanceCodeBlocks(this.currentStreamBlock);
      }

      finishThoughtBlock(this.currentStreamBlock);

      // Reset state
      this.isStreaming = false;
      this.currentStreamBlock = null;
      this.currentStreamElem = null;
      this.currentStreamText = '';
      this.currentStreamThoughtText = '';
      this.currentStreamCitations = null;
    }
  }

  handleStreamChunk(data, consoleElem, triggerCallback) {
    if (!this.isStreaming) {
      const parts = normalizeToContentParts(data);
      parts.forEach(p => {
        if (p.type !== 'text') {
          const b = this.createPartBlock(p, 'assistant');
          if (b) consoleElem.appendChild(b);
        }
      });

      this.currentStreamBlock = createTextBlock('assistant', '', null, null, true, triggerCallback);
      if (this.currentStreamBlock) {
        this.currentStreamBlock.classList.add('is-streaming');
        consoleElem.appendChild(this.currentStreamBlock);
        this.currentStreamElem = this.currentStreamBlock.querySelector('.msg-body');
        this.currentStreamElem.classList.add('streaming-cursor');
      }

      this.currentStreamText = '';
      this.currentStreamThoughtText = '';
      this.currentStreamCitations = null;
      this.isStreaming = true;
    }

    // 1. Xử lý Thought Chunk
    const thoughtChunk = data.data?.choices[0]?.delta?.reasoning_content || null;
    if (thoughtChunk && this.currentStreamBlock) {
      this.currentStreamThoughtText += thoughtChunk;
      appendThoughtToBlock(this.currentStreamBlock, this.currentStreamThoughtText);
    }

    // 2. Xử lý Main Content Chunk
    const textChunk = data.data?.choices[0]?.delta?.content || null;
    if (textChunk) {
      finishThoughtBlock(this.currentStreamBlock);
      this.currentStreamText += textChunk;
      this.currentStreamBlock.dataset.rawText = this.currentStreamText;
      if (this.currentStreamElem) {
        this.currentStreamElem.innerHTML = renderMarkdownSafe(this.currentStreamText);
      }
    }

    // 3. Xử lý Citations ở Chunk cuối cùng
    const citations = data.data?.choices[0]?.metadata?.citations || null;
    if (citations && this.currentStreamBlock) {
      this.currentStreamCitations = citations;
      const updatedText = updateBlockWithCitations(this.currentStreamBlock, citations);
      if (updatedText) {
        this.currentStreamText = updatedText; // Đồng bộ chuỗi text đã có marker [1], [2]
      }
    }
  }
}