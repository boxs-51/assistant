// js/app.js

import { renderBlock, showPendingIndicator, removePendingIndicator } from './components/console.js';
import { initApprovalBar, showApprovalBar, hideApprovalBar } from './components/approvalBar.js';
import { initInputFrame, setInputState } from './components/inputFrame.js';
import { initSidebar } from './components/sidebar.js';
import { initEditor } from './components/editor.js';
import { initResizers } from './components/resizer.js';
import { initGatewayPanel } from './components/gatewayPanel.js';

async function resolveCanonicalAssetContent(attachment, options = {}) {
  const assetId = attachment?.asset_id;
  if (!assetId) return null;
  if (!window.pywebview?.api?.read_asset_content) {
    throw new Error('Canonical asset content resolver is unavailable.');
  }
  const maxBytes = options?.maxBytes ?? null;
  const result = await window.pywebview.api.read_asset_content(
    assetId,
    null,
    maxBytes,
  );
  if (!result?.success) {
    throw new Error(result?.error || 'Unable to read canonical asset content.');
  }
  return result.data;
}

function base64ToObjectUrl(base64Data, mimeType) {
  const binary = atob(base64Data || '');
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return URL.createObjectURL(new Blob([bytes], {
    type: mimeType || 'application/octet-stream',
  }));
}

async function createCanonicalAssetObjectUrl(attachment, options = {}) {
  const content = await resolveCanonicalAssetContent(attachment, options);
  if (!content) return null;
  return {
    url: base64ToObjectUrl(content.base64_data, content.mime_type),
    content,
  };
}

function setupApp() {
  initSidebar();
  initEditor();
  initResizers();
  initApprovalBar();
  initGatewayPanel();

  initInputFrame(async (text, files) => {
    if (window.pywebview?.api?.submit_prompt) {
      // Bật ngay hiệu ứng 3 chấm ở phía client
      showPendingIndicator();
      return window.pywebview.api.submit_prompt(text, files);
    }
    throw new Error('Gateway submit API is unavailable.');
  });

  // Đăng ký các hàm toàn cục
  window.renderBlock = renderBlock;
  window.resolveCanonicalAssetContent = resolveCanonicalAssetContent;
  window.createCanonicalAssetObjectUrl = createCanonicalAssetObjectUrl;
  window.showPendingIndicator = showPendingIndicator;
  window.removePendingIndicator = removePendingIndicator;
  window.showApprovalBar = showApprovalBar;
  window.hideApprovalBar = hideApprovalBar;
  window.setInputState = setInputState;
}

if (window.pywebview) {
  setupApp();
} else {
  window.addEventListener('pywebviewready', setupApp);
}