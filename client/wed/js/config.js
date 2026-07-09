export const CONFIG = {
    BASE_URL: "http://localhost:8000",
    get V1_API_URL() { return `${this.BASE_URL}/v1` },
    get AUTH_API_URL() { return `${this.BASE_URL}/auth` },
    INITIAL_SYSTEM_PROMPT: "Bạn là một trợ lý chuyên nghiệp. Khi người dùng yêu cầu xuất bảng biểu hoặc dữ liệu so sánh, bạn BẮT BUỘC phải sử dụng định dạng bảng Markdown (| Col 1 | Col 2 |). Không được giải thích bằng văn bản thô nếu có thể dùng bảng."
};

// Trạng thái bộ nhớ ứng dụng (App State)
export const AppState = {
    isAuthenticated: false,
    accessToken: null,
    refreshToken: null,
    userEmail: null,
    conversationHistory: [
        { "role": "system", "content": CONFIG.INITIAL_SYSTEM_PROMPT }
    ],
    activeContextFiles: {}, // Lưu trữ file đang active: { file_id: filename }
    
    // Lấy header xác thực
    getHeaders() {
        if (!this.accessToken) return {};
        return { "Authorization": `Bearer ${this.accessToken}` };
    },

    // Lấy thông tin từ DOM
    getProvider() { return document.getElementById('provider-select').value; },
    getModel() { return document.getElementById('model-id').value; },
    
    // Quản lý phiên đăng nhập
    saveTokens(access, refresh, email) {
        this.accessToken = access;
        this.refreshToken = refresh;
        this.userEmail = email;
        this.isAuthenticated = true;
        localStorage.setItem('accessToken', access);
        localStorage.setItem('refreshToken', refresh);
        localStorage.setItem('userEmail', email);
    },

    loadTokensFromStorage() {
        this.accessToken = localStorage.getItem('accessToken');
        this.refreshToken = localStorage.getItem('refreshToken');
        this.userEmail = localStorage.getItem('userEmail');
        this.isAuthenticated = !!this.accessToken;
    },

    clearHistory() {
        this.conversationHistory = [{ "role": "system", "content": CONFIG.INITIAL_SYSTEM_PROMPT }];
        this.activeContextFiles = {};
    },

    logout() {
        this.isAuthenticated = false;
        this.accessToken = null;
        this.refreshToken = null;
        this.userEmail = null;
        localStorage.clear();
    }
};