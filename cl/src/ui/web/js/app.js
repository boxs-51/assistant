// js/app.js

import { renderBlock, showPendingIndicator, removePendingIndicator } from './components/console.js';
import { initApprovalBar, showApprovalBar, hideApprovalBar } from './components/approvalBar.js';
import { initInputFrame, setInputState, addFilesToQueue } from './components/inputFrame.js';
import { initSidebar } from './components/sidebar.js';
import { initEditor } from './components/editor.js';
import { initResizers } from './components/resizer.js';

function setupApp() {
  initSidebar();
  initEditor();
  initResizers();
  initApprovalBar();

  initInputFrame((text, files) => {
    if (window.pywebview?.api?.submit_prompt) {
      // Bật ngay hiệu ứng 3 chấm ở phía client
      showPendingIndicator();
      window.pywebview.api.submit_prompt(text, files);
    }
  });

  // Đăng ký các hàm toàn cục
  window.renderBlock = renderBlock;
  window.showPendingIndicator = showPendingIndicator;
  window.removePendingIndicator = removePendingIndicator;
  window.showApprovalBar = showApprovalBar;
  window.hideApprovalBar = hideApprovalBar;
  window.setInputState = setInputState;
  window.addFilesToQueue = addFilesToQueue;
}

if (window.pywebview) {
  setupApp();
} else {
  window.addEventListener('pywebviewready', setupApp);
}