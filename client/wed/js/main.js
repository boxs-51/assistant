import { AuthManager } from './auth.js';
import { setupEventHandlers, handleOauthCallback } from './handlers.js';

document.addEventListener('DOMContentLoaded', async () => {
    // Xử lý callback từ OAuth nếu có
    handleOauthCallback();
    // Kiểm tra trạng thái đăng nhập từ localStorage
    await AuthManager.checkLoginStatus();
    // Gắn các event handler cho các nút bấm
    setupEventHandlers();
});