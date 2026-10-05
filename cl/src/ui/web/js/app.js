// js/app.js

import {
  renderBlock,
  showPendingIndicator,
  removePendingIndicator,
  clearConversationConsole,
  replaceConversationHistory,
} from './components/console.js';
import { initApprovalBar, showApprovalBar, hideApprovalBar } from './components/approvalBar.js';
import {
  initInputFrame,
  setInputState,
  hasUnsentPayload,
  resetInputForIdentity,
} from './components/inputFrame.js';
import { initSidebar, SessionsPage } from './components/sidebar.js';
import { initEditor } from './components/editor.js';
import { initResizers } from './components/resizer.js';
import { initGatewayPanel } from './components/gatewayPanel.js';
import {
  beginConversationSelection,
  createNewConversation,
  getActiveConversationId,
  getActiveExecutionState,
  isCurrentSelection,
  markSelectionReady,
  markSelectionError,
  resetConversationState,
  setConversationExecutionState,
  shouldAcceptConversationEvent,
} from './state/conversationStore.js';

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

function notifyActiveConversationChanged() {
  if (typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new Event('clui:conversation-active-changed'));
  }
}

function syncActiveConversationExecutionUi() {
  const execution = getActiveExecutionState();
  if (execution?.state === 'BUSY') {
    setInputState(false);
    showPendingIndicator();
  } else {
    removePendingIndicator();
    setInputState(true);
  }
}

function canSwitchConversation(targetConversationId) {
  const active = getActiveConversationId();
  if (active === targetConversationId) return true;
  return !hasUnsentPayload();
}

async function selectConversation(sessionId) {
  if (!window.pywebview?.api?.get_session) {
    throw new Error('Session detail API is unavailable.');
  }

  const selection = beginConversationSelection(sessionId);
  clearConversationConsole();
  removePendingIndicator();
  setInputState(false);
  notifyActiveConversationChanged();

  try {
    const session = await window.pywebview.api.get_session(sessionId);
    if (!isCurrentSelection(sessionId, selection.generation)) return false;
    if (!session || String(session.session_id || '') !== sessionId) {
      throw new Error('Session identity mismatch.');
    }

    replaceConversationHistory(session.messages || []);
    markSelectionReady(sessionId, selection.generation);
    syncActiveConversationExecutionUi();
    notifyActiveConversationChanged();
    return true;
  } catch (error) {
    if (isCurrentSelection(sessionId, selection.generation)) {
      markSelectionError(sessionId, selection.generation, String(error?.message || error));
      clearConversationConsole();
      removePendingIndicator();
      setInputState(false);
    }
    throw error;
  }
}

function startNewConversation() {
  const selection = createNewConversation();
  clearConversationConsole();
  removePendingIndicator();
  setInputState(true);
  notifyActiveConversationChanged();
  return selection.conversationId;
}

function handleConversationBlock(payload) {
  if (!shouldAcceptConversationEvent(payload)) return false;
  renderBlock(payload);
  return true;
}

function handleConversationExecutionState(payload) {
  const applied = setConversationExecutionState(
    payload?.conversation_id,
    payload?.execution_id,
    payload?.state,
    payload?.auth_generation,
  );
  if (!applied) return false;
  if (payload?.conversation_id === getActiveConversationId()) {
    syncActiveConversationExecutionUi();
  }
  return true;
}

function handleConversationIdentityChanged(payload = {}) {
  resetConversationState(payload?.generation);
  clearConversationConsole();
  removePendingIndicator();
  resetInputForIdentity(Boolean(payload?.authenticated));
  notifyActiveConversationChanged();
  setTimeout(() => {
    SessionsPage.load();
  }, 0);
}

function setupApp() {
  window.onConversationBlock = handleConversationBlock;
  window.onConversationExecutionState = handleConversationExecutionState;
  window.onConversationIdentityChanged = handleConversationIdentityChanged;

  initSidebar({
    canSwitch: canSwitchConversation,
    isActive: (sessionId) => getActiveConversationId() === sessionId,
    selectSession: selectConversation,
    newChat: startNewConversation,
  });
  initEditor();
  initResizers();
  initApprovalBar();
  initGatewayPanel();

  window.addEventListener('clui:conversation-active-changed', syncActiveConversationExecutionUi);

  initInputFrame(async (text, files) => {
    if (!window.pywebview?.api?.submit_prompt) {
      throw new Error('Gateway submit API is unavailable.');
    }

    let conversationId = getActiveConversationId();
    if (!conversationId) {
      conversationId = createNewConversation().conversationId;
      clearConversationConsole();
      notifyActiveConversationChanged();
    }
    return window.pywebview.api.submit_prompt(text, files, conversationId);
  });

  // Legacy non-conversation-scoped callbacks remain available for bounded
  // non-chat UI surfaces. Primary chat lifecycle uses the scoped handlers above.
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
