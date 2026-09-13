let currentActionHandlers = { onApprove: null, onReject: null };

export function initApprovalBar() {
  document.getElementById('btn-approve').addEventListener('click', () => {
    if (typeof currentActionHandlers.onApprove === 'function') {
      currentActionHandlers.onApprove();
    } else {
      handleApproval(true);
    }
  });

  document.getElementById('btn-reject').addEventListener('click', () => {
    if (typeof currentActionHandlers.onReject === 'function') {
      currentActionHandlers.onReject();
    } else {
      handleApproval(false);
    }
  });
}

/**
 * Hàm hiển thị Thanh thông báo / Phê duyệt đa năng
 * @param {Object} req - Cấu hình truyền vào
 * @param {string} [req.mode] - 'tool' | 'diff' | 'alert'
 * @param {string} [req.risk_level] - 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO' | 'SUCCESS'
 * @param {string} [req.title] - Tiêu đề tùy chỉnh
 * @param {string} [req.name] - Tên tool hoặc đối tượng tác động
 * @param {Object} [req.args] - Tham số truyền vào (Dành cho tool)
 * @param {Array<string>} [req.files] - Danh sách file bị thay đổi (Dành cho diff)
 * @param {string} [req.diffText] - Nội dung Unified Diff (Dành cho diff)
 * @param {string} [req.content] - Mẫu HTML/Văn bản hiển thị tùy chỉnh (Dành cho alert)
 * @param {string} [req.message] - Dòng thông điệp hiển thị ở Footer
 * @param {boolean} [req.showActions=true] - Ẩn/Hiện nhóm nút bấm
 * @param {Function} [req.onApprove] - Callback khi bấm Duyệt
 * @param {Function} [req.onReject] - Callback khi bấm Từ chối
 */
export function showApprovalBar(req = {}) {
  const bar = document.getElementById('approval-bar');
  const badge = document.getElementById('approval-badge');
  const title = document.getElementById('approval-title');
  const target = document.getElementById('approval-target');
  const customContent = document.getElementById('approval-custom-content');
  const message = document.getElementById('approval-message');
  const actionsWrap = document.getElementById('approval-actions');

  // Lưu callback tùy chỉnh (nếu có)
  currentActionHandlers.onApprove = req.onApprove || null;
  currentActionHandlers.onReject = req.onReject || null;

  // 1. Xác định cấp độ rủi ro & Variant chủ đạo
  const risk = (req.risk_level || 'HIGH').toUpperCase();
  bar.className = `approval-bar bar-${risk.toLowerCase()}`;

  badge.textContent = risk;
  badge.className = `approval-badge badge-${risk.toLowerCase()}`;

  // 2. Tự động nhận diện Mode nếu không truyền
  const mode = req.mode || (req.diffText || req.files ? 'diff' : req.args ? 'tool' : 'alert');

  // 3. Render Header & Actions
  actionsWrap.style.display = req.showActions !== false ? 'flex' : 'none';

  // 4. Render Body theo Mode
  customContent.innerHTML = ''; // Reset nội dung cũ

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
    title.textContent = req.title || `Yêu cầu xác nhận thay đổi tập tin`;
    target.textContent = `📝 Số file ảnh hưởng: ${(req.files || []).length}`;

    // Render danh sách file
    if (req.files && req.files.length > 0) {
      const fileListDiv = document.createElement('div');
      fileListDiv.className = 'diff-file-list';
      fileListDiv.innerHTML = req.files.map(f => `<span class="diff-file-badge">📄 ${f}</span>`).join('');
      customContent.appendChild(fileListDiv);
    }

    // Render khối Diff
    if (req.diffText) {
      const diffContainer = document.createElement('div');
      diffContainer.className = 'diff-viewer-container';
      
      // Nếu có thư viện diff2html
      if (window.Diff2HtmlUI) {
        const ui = new Diff2HtmlUI(diffContainer, req.diffText, {
          outputFormat: 'side-by-side',
          drawFileList: false,
          synchronisedScroll: true,
          highlight: true
        });
        ui.draw();
      } else {
        // Fallback hiển thị text diff đơn giản
        diffContainer.innerHTML = `<pre class="diff-text-fallback">${escapeHtml(req.diffText)}</pre>`;
      }
      customContent.appendChild(diffContainer);
    }

  } else { // Mode 'alert' / 'info'
    title.textContent = req.title || `Thông báo hệ thống`;
    target.textContent = req.name ? `📌 Phạm vi: ${req.name}` : '';
    
    if (req.content) {
      const alertDiv = document.createElement('div');
      alertDiv.className = 'approval-alert-content';
      alertDiv.innerHTML = req.content;
      customContent.appendChild(alertDiv);
    }
  }

  // 5. Render Footer
  message.textContent = req.message || (risk === 'HIGH' 
    ? `⚠️ [Cảnh báo HIGH RISK]: Thao tác này có thể ảnh hưởng đến cấu trúc hệ thống.`
    : `ℹ️ Vui lòng xem xét kỹ nội dung trước khi tiếp tục.`);

  bar.classList.remove('hidden');
}

export function hideApprovalBar() {
  document.getElementById('approval-bar').classList.add('hidden');
}

function handleApproval(choice) {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.respond_approval(choice);
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}