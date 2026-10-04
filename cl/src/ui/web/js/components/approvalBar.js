let currentActionHandlers = { onApprove: null, onReject: null };
let activeApprovalId = null;
let decisionPending = false;

const RISK_LEVELS = new Set(['HIGH', 'CRITICAL', 'MEDIUM', 'LOW', 'INFO', 'SUCCESS']);
const MODES = new Set(['tool', 'diff', 'alert']);

function setDecisionPending(pending) {
  decisionPending = Boolean(pending);
  const approve = document.getElementById('btn-approve');
  const reject = document.getElementById('btn-reject');
  if (approve) approve.disabled = decisionPending;
  if (reject) reject.disabled = decisionPending;
}

export function initApprovalBar() {
  document.getElementById('btn-approve').addEventListener('click', () => {
    if (typeof currentActionHandlers.onApprove === 'function') {
      currentActionHandlers.onApprove();
    } else {
      void handleApproval(true);
    }
  });

  document.getElementById('btn-reject').addEventListener('click', () => {
    if (typeof currentActionHandlers.onReject === 'function') {
      currentActionHandlers.onReject();
    } else {
      void handleApproval(false);
    }
  });
}

function appendFileBadges(container, files) {
  (Array.isArray(files) ? files : []).forEach((fileName) => {
    const badge = document.createElement('span');
    badge.className = 'diff-file-badge';
    badge.textContent = `📄 ${String(fileName)}`;
    container.appendChild(badge);
  });
}

export function showApprovalBar(req = {}) {
  const bar = document.getElementById('approval-bar');
  const badge = document.getElementById('approval-badge');
  const title = document.getElementById('approval-title');
  const target = document.getElementById('approval-target');
  const customContent = document.getElementById('approval-custom-content');
  const message = document.getElementById('approval-message');
  const actionsWrap = document.getElementById('approval-actions');

  const requestedRisk = String(req.risk_level || 'HIGH').toUpperCase();
  const risk = RISK_LEVELS.has(requestedRisk) ? requestedRisk : 'HIGH';
  const requestedMode = req.mode || (req.diffText || req.files ? 'diff' : req.args ? 'tool' : 'alert');
  const mode = MODES.has(requestedMode) ? requestedMode : 'alert';

  activeApprovalId = typeof req.approval_id === 'string' && req.approval_id.trim()
    ? req.approval_id.trim()
    : null;
  currentActionHandlers.onApprove = req.onApprove || null;
  currentActionHandlers.onReject = req.onReject || null;
  setDecisionPending(false);

  bar.className = `approval-bar bar-${risk.toLowerCase()}`;
  badge.textContent = risk;
  badge.className = `approval-badge badge-${risk.toLowerCase()}`;
  actionsWrap.style.display = req.showActions !== false ? 'flex' : 'none';
  customContent.replaceChildren();

  if (mode === 'tool') {
    title.textContent = req.title || `Yêu cầu thực thi ${req.type || 'Tool'}`;
    target.textContent = `📌 Công cụ: ${req.name || 'N/A'}`;

    if (req.args && Object.keys(req.args).length > 0) {
      const pre = document.createElement('pre');
      pre.className = 'approval-args';
      pre.textContent = JSON.stringify(req.args, null, 2);
      customContent.appendChild(pre);
    }
  } else if (mode === 'diff') {
    const files = Array.isArray(req.files) ? req.files : [];
    title.textContent = req.title || 'Yêu cầu xác nhận thay đổi tập tin';
    target.textContent = `📝 Số file ảnh hưởng: ${files.length}`;

    if (files.length > 0) {
      const fileListDiv = document.createElement('div');
      fileListDiv.className = 'diff-file-list';
      appendFileBadges(fileListDiv, files);
      customContent.appendChild(fileListDiv);
    }

    if (req.diffText) {
      const diffContainer = document.createElement('div');
      diffContainer.className = 'diff-viewer-container';
      const pre = document.createElement('pre');
      pre.className = 'diff-text-fallback';
      pre.textContent = String(req.diffText);
      diffContainer.appendChild(pre);
      customContent.appendChild(diffContainer);
    }
  } else {
    title.textContent = req.title || 'Thông báo hệ thống';
    target.textContent = req.name ? `📌 Phạm vi: ${req.name}` : '';

    if (req.content) {
      const alertDiv = document.createElement('div');
      alertDiv.className = 'approval-alert-content';
      alertDiv.textContent = String(req.content);
      customContent.appendChild(alertDiv);
    }
  }

  message.textContent = req.reason || req.message || (risk === 'HIGH' || risk === 'CRITICAL'
    ? '⚠️ Thao tác này yêu cầu xác nhận rõ ràng trước khi tiếp tục.'
    : 'ℹ️ Vui lòng xem xét kỹ nội dung trước khi tiếp tục.');

  bar.classList.remove('hidden');
  return activeApprovalId;
}

export function hideApprovalBar(approvalId) {
  const requestedId = typeof approvalId === 'string' ? approvalId.trim() : null;
  if (activeApprovalId) {
    if (!requestedId || requestedId !== activeApprovalId) return false;
  }

  document.getElementById('approval-bar').classList.add('hidden');
  activeApprovalId = null;
  currentActionHandlers = { onApprove: null, onReject: null };
  setDecisionPending(false);
  return true;
}

async function handleApproval(choice) {
  if (decisionPending || !activeApprovalId) return false;
  const responder = window.pywebview?.api?.respond_approval;
  if (!responder) return false;

  setDecisionPending(true);
  try {
    const accepted = await responder(choice, activeApprovalId);
    if (accepted !== true) {
      setDecisionPending(false);
      return false;
    }
    return true;
  } catch (error) {
    console.error('Approval response failed:', error);
    setDecisionPending(false);
    return false;
  }
}
