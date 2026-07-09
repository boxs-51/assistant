export const CONFIG = {
    BASE_GATEWAY_URL: "http://localhost:8000/v1",
    INITIAL_SYSTEM_PROMPT: "Bạn là một trợ lý chuyên nghiệp. Khi người dùng yêu cầu xuất bảng biểu hoặc dữ liệu so sánh, bạn BẮT BUỘC phải sử dụng định dạng bảng Markdown (| Col 1 | Col 2 |). Không được giải thích bằng văn bản thô nếu có thể dùng bảng."
};

// Trạng thái bộ nhớ ứng dụng (App State)
export const AppState = {
    conversationHistory: [
        { "role": "system", "content": CONFIG.INITIAL_SYSTEM_PROMPT }
    ],
    activeContextFiles: {}, // Lưu trữ file đang active: { file_id: filename }
    
    // Helper lấy thông tin real-time từ DOM cấu hình
    getHeaders() {
        const apiKey = document.getElementById('api-key').value || 'change-me';
        return { "Authorization": `Bearer ${apiKey}` };
    },
    getProvider() { return document.getElementById('provider-select').value; },
    getModel() { return document.getElementById('model-id').value; },
    
    clearHistory() {
        this.conversationHistory = [{ "role": "system", "content": CONFIG.INITIAL_SYSTEM_PROMPT }];
        this.activeContextFiles = {};
    }
};