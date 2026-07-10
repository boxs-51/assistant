import { CONFIG } from './config.js';
import { AppState } from './state.js';

export const GatewayAPI = {
    // --- AUTH ENDPOINTS ---
    getAuthBaseUrl() {
        return CONFIG.AUTH_API_URL;
    },
    
    async initiateRegistration(email, password) {
        return await fetch(`${CONFIG.AUTH_API_URL}/register/initiate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
    },

    async verifyOtp(email, otp) {
        return await fetch(`${CONFIG.AUTH_API_URL}/register/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, otp })
        });
    },

    async login(email, password) {
        return await fetch(`${CONFIG.AUTH_API_URL}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
    },

    async listApiKeys() {
        return await fetch(`${CONFIG.AUTH_API_URL}/api-keys`, {
            headers: AppState.getHeaders()
        });
    },

    async createApiKey(name) {
        return await fetch(`${CONFIG.AUTH_API_URL}/api-keys`, {
            method: 'POST',
            headers: { ...AppState.getHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
    },

    async revokeApiKey(keyId) {
        return await fetch(`${CONFIG.AUTH_API_URL}/api-keys/${keyId}`, {
            method: 'DELETE',
            headers: AppState.getHeaders()
        });
    },

    async getMe() {
        return await fetch(`${CONFIG.AUTH_API_URL}/me`, {
            headers: AppState.getHeaders()
        });
    },

    // --- V1 API ENDPOINTS ---
    // 1. Get Model chi tiết
    async getModelDetails(modelId, provider) {
        const res = await fetch(`${CONFIG.V1_API_URL}/models/${modelId}?provider_name=${provider}`, {
            headers: AppState.getHeaders()
        });
        return await res.json();
    },

    // 2. Upload file nhị phân
    async uploadFile(file, provider) {
        const formData = new FormData();
        formData.append('file', file);

        return await fetch(`${CONFIG.V1_API_URL}/files?provider_name=${provider}`, {
            method: 'POST',
            headers: { ...AppState.getHeaders() }, // Bỏ Content-Type để browser tự set
            body: formData
        });
    },

    // 3. Fetch danh sách file có sẵn
    async listFiles(provider) {
        return await fetch(`${CONFIG.V1_API_URL}/files?provider_name=${provider}`, {
            headers: AppState.getHeaders()
        });
    },

    // 4. Yêu cầu xóa file
    async deleteFile(fileId, provider) {
        return await fetch(`${CONFIG.V1_API_URL}/files/${fileId}?provider_name=${provider}`, {
            method: 'DELETE',
            headers: AppState.getHeaders()
        });
    },

    // 5. Kết nối Stream Chat qua Fetch API Reader
    async postChatCompletion(payload) {
        return await fetch(`${CONFIG.V1_API_URL}/chat/completions`, {
            method: 'POST',
            headers: {
                ...AppState.getHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
    }
};