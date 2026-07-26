from typing import Dict, Any

class RequestEmbeddings():
    def adapt_embeddings_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Sửa lỗi: Thêm bọc models/ vào trước tên model cho đúng quy định Gemini."""
        input_text = request.get("input")
        model_name = request.get("model", "embedding-001")
        # Đảm bảo có prefix models/
        full_model_path = model_name if model_name.startswith("models/") else f"models/{model_name}"

        if isinstance(input_text, str):
            return {"model": full_model_path, "content": {"parts": [{"text": input_text}]}}
        elif isinstance(input_text, list):
            return {
                "requests": [
                    {"model": full_model_path, "content": {"parts": [{"text": text}]}}
                    for text in input_text
                ]
            }
        return {}