export const CONFIG = {
    BASE_URL: "http://localhost:8000",
    get V1_API_URL() { return `${this.BASE_URL}/v1` },
    get AUTH_API_URL() { return `${this.BASE_URL}/auth` },
    INITIAL_SYSTEM_PROMPT: "Bạn là một trợ lý chuyên nghiệp. Khi người dùng yêu cầu xuất bảng biểu hoặc dữ liệu so sánh, bạn BẮT BUỘC phải sử dụng định dạng bảng Markdown (| Col 1 | Col 2 |). Không được giải thích bằng văn bản thô nếu có thể dùng bảng.",
};