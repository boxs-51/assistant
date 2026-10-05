const conversations = new Map();

let activeConversationId = null;
let selectionGeneration = 0;
let authGeneration = 0;

function normalizeId(value) {
  const normalized = String(value || '').trim();
  if (!normalized) throw new Error('conversation_id is required');
  if (normalized.length > 256) throw new Error('conversation_id is too long');
  return normalized;
}

function ensureConversation(conversationId) {
  const id = normalizeId(conversationId);
  if (!conversations.has(id)) {
    conversations.set(id, {
      conversationId: id,
      selectionGeneration: 0,
      selectionState: 'IDLE',
      selectionError: null,
      executionId: null,
      executionState: 'IDLE',
      draftText: '',
    });
  }
  return conversations.get(id);
}

function newConversationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();

  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'));
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10, 16).join(''),
  ].join('-');
}

export function beginConversationSelection(conversationId) {
  const id = normalizeId(conversationId);
  selectionGeneration += 1;
  activeConversationId = id;
  const record = ensureConversation(id);
  record.selectionGeneration = selectionGeneration;
  record.selectionState = 'LOADING_HISTORY';
  record.selectionError = null;
  return { conversationId: id, generation: selectionGeneration };
}

export function createNewConversation() {
  const id = newConversationId();
  selectionGeneration += 1;
  activeConversationId = id;
  const record = ensureConversation(id);
  record.selectionGeneration = selectionGeneration;
  record.selectionState = 'NEW_DRAFT';
  record.selectionError = null;
  return { conversationId: id, generation: selectionGeneration };
}

export function getActiveConversationId() {
  return activeConversationId;
}

export function setConversationDraftText(conversationId, text) {
  const record = ensureConversation(conversationId);
  record.draftText = String(text ?? '');
  return record.draftText;
}

export function getConversationDraftText(conversationId) {
  if (!conversationId) return '';
  try {
    const id = normalizeId(conversationId);
    return conversations.get(id)?.draftText || '';
  } catch {
    return '';
  }
}

export function isCurrentSelection(conversationId, generation) {
  return activeConversationId === String(conversationId || '')
    && selectionGeneration === generation;
}

export function markSelectionReady(conversationId, generation) {
  if (!isCurrentSelection(conversationId, generation)) return false;
  const record = ensureConversation(conversationId);
  record.selectionState = 'ACTIVE_READY';
  record.selectionError = null;
  return true;
}

export function markSelectionError(conversationId, generation, error) {
  if (!isCurrentSelection(conversationId, generation)) return false;
  const record = ensureConversation(conversationId);
  record.selectionState = 'LOAD_ERROR';
  record.selectionError = String(error || 'Session load failed');
  return true;
}

export function isCurrentAuthGeneration(generation) {
  if (generation === null || generation === undefined) return true;
  return Number(generation) === authGeneration;
}

export function setConversationExecutionState(conversationId, executionId, state, generation = null) {
  if (!isCurrentAuthGeneration(generation)) return false;
  let id;
  try {
    id = normalizeId(conversationId);
  } catch {
    return false;
  }
  const normalizedExecutionId = String(executionId || '').trim();
  if (!normalizedExecutionId) return false;

  const record = ensureConversation(id);
  const normalizedState = String(state || '').toUpperCase();
  if (normalizedState === 'BUSY') {
    record.executionId = normalizedExecutionId;
    record.executionState = 'BUSY';
    return true;
  }

  if (record.executionId && record.executionId !== normalizedExecutionId) {
    return false;
  }

  record.executionId = normalizedExecutionId;
  record.executionState = 'TERMINAL';
  return true;
}

export function getConversationExecutionState(conversationId) {
  if (!conversationId || !conversations.has(conversationId)) {
    return { executionId: null, state: 'IDLE' };
  }
  const record = conversations.get(conversationId);
  return {
    executionId: record.executionId,
    state: record.executionState,
  };
}

export function getActiveExecutionState() {
  return getConversationExecutionState(activeConversationId);
}

export function shouldAcceptConversationEvent(payload) {
  if (!isCurrentAuthGeneration(payload?.auth_generation)) return false;
  const conversationId = String(payload?.conversation_id || '').trim();
  if (!conversationId || conversationId !== activeConversationId) return false;

  const executionId = String(payload?.execution_id || '').trim();
  if (!executionId) return true;

  const record = ensureConversation(conversationId);
  return !record.executionId || record.executionId === executionId;
}

export function resetConversationState(nextAuthGeneration = null) {
  conversations.clear();
  activeConversationId = null;
  selectionGeneration += 1;
  const parsedGeneration = Number(nextAuthGeneration);
  if (Number.isInteger(parsedGeneration) && parsedGeneration >= 0) {
    authGeneration = parsedGeneration;
  } else {
    authGeneration += 1;
  }
  return authGeneration;
}

export function getAuthGeneration() {
  return authGeneration;
}
