import { CONFIG } from './config.js';

// Trạng thái bộ nhớ ứng dụng (App State)
export const AppState = {
    isAuthenticated: false,
    accessToken: null,
    refreshToken: null,
    userEmail: null,
    conversationHistory: [
        { "role": "system", "content": CONFIG.INITIAL_SYSTEM_PROMPT }
    ],
    attachedFiles: [], // Lưu trữ file base64: [{ name: string, type: string, data: string }]

    // Lấy header xác thực
    getHeaders() {
        if (!this.accessToken) return {};
        return { "Authorization": `Bearer ${this.accessToken}` };
    },

    // Lấy thông tin từ DOM
    getProvider() { return document.getElementById('provider-select').value; },
    getModel() { return document.getElementById('model-id').value; },

    // Quản lý phiên đăng nhập
    saveTokens(access, refresh) {
        this.accessToken = access;
        this.refreshToken = refresh;
        this.isAuthenticated = true;
        localStorage.setItem('accessToken', access);
        localStorage.setItem('refreshToken', refresh);
    },

    clearHistory() {
        this.conversationHistory = [{ "role": "system", "content": CONFIG.INITIAL_SYSTEM_PROMPT }];
        this.attachedFiles = [];
    },

    clearTokens() {
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
    }
};