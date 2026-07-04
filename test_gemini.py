import os
import json
from google import genai
from google.genai import types

# 1. Khởi tạo Client (Tự động lấy API key từ biến môi trường $env:GEMINI_API_KEY)
# Hoặc bạn có thể điền trực tiếp: client = genai.Client(api_key="AIzaSy...")
client = genai.Client()

# Định nghĩa model mặc định (Dùng gemini-2.5-flash cho tốc độ nhanh, tiết kiệm)
MODEL_NAME = "gemini-2.5-flash"

def print_separator(title):
    print("\n" + "="*50)
    print(f" TESTING: {title}")
    print("="*50)

# --- 1. ENDPOINT: GENERATE CONTENT (Văn bản thông thường) ---
def test_generate_content():
    print_separator("Generate Content (Text Only)")
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents="Hãy viết một câu thơ ngắn về lập trình bằng tiếng Việt.",
    )
    print(response.text)

# --- 2. ENDPOINT: STREAM GENERATE CONTENT (Sinh văn bản dạng chuỗi) ---
def test_stream_generate_content():
    print_separator("Stream Generate Content (Streaming)")
    
    response = client.models.generate_content_stream(
        model=MODEL_NAME,
        contents="Viết một bài văn siêu ngắn (khoảng 3 câu) tả con mèo."
    )
    for chunk in response:
        print(chunk.text, end="", flush=True)
    print()

# --- 3. ENDPOINT: CHAT (Cuộc trò chuyện nhiều lượt) ---
def test_chat_multi_turn():
    print_separator("Chat (Multi-turn Conversation)")
    
    # Khởi tạo một phiên chat để SDK tự quản lý lịch sử (history)
    chat = client.chats.create(model=MODEL_NAME)
    
    # Lượt 1
    print("User: Chào bạn, mình nuôi một chú chó tên là Lu.")
    response1 = chat.send_message("Chào bạn, mình nuôi một chú chó tên là Lu.")
    print(f"Gemini: {response1.text}\n")
    
    # Lượt 2 (Hỏi câu hỏi dựa trên ngữ cảnh lượt 1)
    print("User: Tên chú chó mình vừa nhắc tới là gì ấy nhỉ?")
    response2 = chat.send_message("Tên chú chó mình vừa nhắc tới là gì ấy nhỉ?")
    print(f"Gemini: {response2.text}")

# --- 4. ENDPOINT: SYSTEM INSTRUCTION (Cấu hình hệ thống / Vai trò) ---
def test_system_instruction():
    print_separator("System Instruction (Vai trò hệ thống)")
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents="Hôm nay thời tiết thế nào?",
        config=types.GenerateContentConfig(
            system_instruction="Bạn là một trợ lý vui tính và CHỈ được nói bằng thơ lục bát."
        )
    )
    print(response.text)

# --- 5. ENDPOINT: STRUCTURED OUTPUT (Ép đầu ra dạng JSON Object) ---
def test_structured_json_output():
    print_separator("Structured JSON Output (Trả về JSON chuẩn)")
    
    prompt = "Hãy liệt kê 3 ngôn ngữ lập trình phổ biến nhất kèm năm ra đời."
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            # Ép model trả về JSON thuần túy
            response_mime_type="application/json",
            # Định nghĩa cấu trúc JSON mong muốn (Optional nhưng nên có để chính xác)
            response_schema={
                "type": "OBJECT",
                "properties": {
                    "languages": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "name": {"type": "STRING"},
                                "year": {"type": "INTEGER"}
                            },
                            "required": ["name", "year"]
                        }
                    }
                }
            }
        )
    )
    # In ra và format JSON cho đẹp
    parsed_json = json.loads(response.text)
    print(json.dumps(parsed_json, indent=2, ensure_ascii=False))

# --- 6. ENDPOINT: COUNT TOKENS (Đếm số lượng Token) ---
def test_count_tokens():
    print_separator("Count Tokens (Đếm số lượng Token)")
    
    text_to_count = "Đoạn văn bản này sẽ chiếm bao nhiêu tokens trong API của Gemini vậy nhỉ?"
    response = client.models.count_tokens(
        model=MODEL_NAME,
        contents=text_to_count
    )
    print(f"Văn bản: '{text_to_count}'")
    print(f"-> Số lượng Tokens: {response.total_tokens}")

# --- 7. ENDPOINT: LIST MODELS (Lấy danh sách các Model khả dụng) ---
def test_list_models():
    print_separator("List Models (Danh sách các Model)")
    
    print("Đang lấy danh sách các model...")
    # Lấy danh sách và lọc ra các model thuộc thế hệ mới
    for model in client.models.list():
        if "gemini" in model.name:
            print(f"- Model Name: {model.name} (Supported actions: {model.supported_actions})")


# --- HÀM MAIN ĐỂ CHẠY TẤT CẢ CÁC TEST ---
if __name__ == "__main__":
    # Kiểm tra xem bạn đã set biến môi trường chưa
    if not os.environ.get("GEMINI_API_KEY"):
        print("CẢNH BÁO: Bạn chưa thiết lập biến môi trường GEMINI_API_KEY!")
        print("Vui lòng chạy lệnh này ngoài Terminal trước khi chạy file python:")
        print('$env:GEMINI_API_KEY="KEY_CỦA_BẠN"')
        print("-" * 50)
    
    # Chạy lần lượt các hàm test endpoint
    test_generate_content()
    test_stream_generate_content()
    test_chat_multi_turn()
    test_system_instruction()
    test_structured_json_output()
    test_count_tokens()
    test_list_models()