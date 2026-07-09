import { CONFIG, AppState } from './config.js';

export const GatewayAPI = {
    // 1. Get Model chi tiết
    async getModelDetails(modelId, provider) {
        const res = await fetch(`${CONFIG.BASE_GATEWAY_URL}/models/${modelId}?provider_name=${provider}`, {
            headers: AppState.getHeaders()
        });
        return await res.json();
    },

    // 2. Upload file nhị phân
    async uploadFile(fileBinary, provider) {
        const formData = new FormData();
        formData.append('file', fileBinary);

        return await fetch(`${CONFIG.BASE_GATEWAY_URL}/files?provider_name=${provider}`, {
            method: 'POST',
            headers: AppState.getHeaders(),
            body: formData
        });
    },

    // 3. Fetch danh sách file có sẵn
    async listFiles(provider) {
        return await fetch(`${CONFIG.BASE_GATEWAY_URL}/files?provider_name=${provider}`, {
            headers: AppState.getHeaders()
        });
    },

    // 4. Yêu cầu xóa file
    async deleteFile(fileId, provider) {
        return await fetch(`${CONFIG.BASE_GATEWAY_URL}/files/${fileId}?provider_name=${provider}`, {
            method: 'DELETE',
            headers: AppState.getHeaders()
        });
    },

    // 5. Kết nối Stream Chat qua Fetch API Reader
    async postChatCompletion(payload) {
        return await fetch(`${CONFIG.BASE_GATEWAY_URL}/chat/completions`, {
            method: 'POST',
            headers: {
                ...AppState.getHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
    }
};