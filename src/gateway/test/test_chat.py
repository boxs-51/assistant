import base64
from pathlib import Path

import httpx
import asyncio
import os
import json
import time
from dotenv import load_dotenv
from ..routing.providers.gemini import FileHelper
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

GATEWAY_URL = "http://localhost:8000/v1/chat/completions"
GATEWAY_API_KEY = os.getenv("GEMINI_API_KEY", "change-me")

def print_usage_info(usage_data: dict):
    """Hàm bổ trợ để in chi tiết Token Usage."""
    if not usage_data:
        return
    print("\n📊 [Token Usage Info]")
    print(f"  └─ Prompt Tokens:      {usage_data.get('prompt_tokens', 0)}")
    print(f"  └─ Completion Tokens:  {usage_data.get('completion_tokens', 0)}")
    print(f"  └─ Total Tokens:       {usage_data.get('total_tokens', 0)}")



async def print_smooth_text(text: str, delay: float = 0.01):
    """
    Nhận vào một chuỗi văn bản và in ra từng ký tự với độ trễ cố định
    để tạo hiệu ứng typewriter (máy đánh chữ) siêu mượt.
    """
    for char in text:
        print(char, end="", flush=True)
        await asyncio.sleep(delay)

async def test_gemini_provider():
    """
    Test 1: Gửi một request non-streaming kèm theo SYSTEM PROMPT.
    """
    print("\n--- 🚀 [Test 1] Testing Gemini (Non-streaming với System Prompt) ---")
    payload = {
        "model": "default",
        "provider": "gemini",
        "messages": [
            # === THÊM SYSTEM PROMPT TẠI ĐÂY ===
            {
                "role": "system", 
                "content": "Bạn là một trợ lý ảo hài hước, luôn trả lời bằng thơ và kết thúc câu bằng từ 'bạn hiền'."
            },
            {
                "role": "user", 
                "content": "Giải thích ngắn gọn AI Gateway là gì?"
            }
        ],
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {GATEWAY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(GATEWAY_URL, json=payload, headers=headers)
            response.raise_for_status()
            res = response.json()
            
            print("✅ Success! Response Metadata:")
            print(f"  🌐 Provider:     {res.get('provider')}")
            print(f"  🤖 Actual Model: {res.get('model')}")
            print("---------------------------------------------")
            content = res.get("choices")[0].get("message").get("content")
            await print_smooth_text(content)
            print("---------------------------------------------")
            print_usage_info(res.get("usage"))

    except httpx.HTTPStatusError as e:
        print(f"\n❌ Error: Request failed with status {e.response.status_code}\nResponse: {e.response.text}")
    except httpx.RequestError as e:
        print(f"❌ Error: Could not connect to AI Gateway. Details: {e}")


async def test_gemini_streaming():
    """
    Test 2 (Nâng cấp): Gửi một request streaming kèm theo SYSTEM PROMPT,
    đính kèm 1 file ảnh (hoặc tài liệu) dưới dạng Multimodal Schema.
    """
    print("\n--- 🚀 [Test 2] Testing Gemini (Multimodal Streaming) ---")
    
    # 📝 ĐƯỜNG DẪN ĐẾN FILE TEST CỦA BẠN (Thay đổi cho đúng file thực tế của bạn)
    # Ví dụ: "test_image.png" hoặc "document.pdf"
    path_to_test_file = "D:\\OIP.jfif" 
    
    try:
        # 1. Chuyển đổi file test thành Base64
        file_path = Path(path_to_test_file)
        mime_type = FileHelper.detect_mime_type(file_path)
        with open(file_path, "rb") as f:
            base64_data = base64.b64encode(f.read()).decode('utf-8')
        print(f"📦 Đã load file thành công. Loại: {mime_type} ({len(base64_data)} bytes)")
        print("  └─ Đang xây dựng payload Multimodal cho request streaming...")
        # Xác định type payload dựa vào mime_type để mapping đúng Schema
        is_image = mime_type.startswith("image/")
        media_type = "image" if is_image else "file"
        
        # 2. Xây dựng cấu trúc payload Multimodal hoàn chỉnh
        payload = {
            "model": "gemini-2.5-flash",  # Đảm bảo dùng model multimodal thế hệ mới
            "messages": [
                {
                    "role": "system", 
                    "content": "Bạn là một chuyên gia phân tích dữ liệu và hình ảnh chuyên nghiệp."
                },
                {
                    "role": "user", 
                    # Content bây giờ là List[MessageContentPart]
                    "content": [
                        {
                            "type": "text",
                            "text": "Hãy đọc và phân tích kỹ nội dung từ tệp đính kèm này giúp tôi."
                        },
                        {
                            # Trường này khớp với loại: part.image hoặc part.file
                            "type": media_type, 
                            media_type: {
                                # Nếu là "image", nó sẽ map vào class ImageContent
                                # Bên trong ImageContent bắt buộc phải có object "attachment"
                                "attachment": {
                                    "mime_type": mime_type,
                                    "base64_data": base64_data,
                                    "filename": "OIP.jfif"
                                },
                                "detail": "auto" # Thuộc tính tùy chọn của ImageContent
                            } if media_type == "image" else {
                                # Nếu là "file", nó map thẳng vào class GatewayAttachment
                                "mime_type": mime_type,
                                "base64_data": base64_data,
                                "filename": "document.pdf"
                            }
                        }
                    ]
                }
            ],
            "stream": True
        }
        
    except FileNotFoundError:
        print(f"⚠️ Cảnh báo: Không tìm thấy file test tại '{path_to_test_file}'. Sẽ fallback về test TEXT thuần túy.")
        # Fallback về text nếu bạn lười tạo file ảnh test
        payload = {
            "model": "gemini-2.5-flash",
            "messages": [
                {"role": "system", "content": "Bạn là trợ lý ảo."},
                {"role": "user", "content": "Kể một câu chuyện cười ngắn khoảng 3 câu."}
            ],
            "stream": True
        }

    headers = {
        "Authorization": f"Bearer {GATEWAY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", GATEWAY_URL, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    print(f"\n❌ Error {response.status_code}: {body.decode()}")
                    return

                print("✅ Stream Connection Established. Content:")
                print("---------------------------------------------")
                
                last_chunk_metadata = {}
                
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    
                    if line.startswith("data:"):
                        data_content = line[5:].strip()
                        if data_content == "[DONE]":
                            break
                        
                        try:
                            chunk_json = json.loads(data_content)
                            last_chunk_metadata["provider"] = chunk_json.get("provider")
                            last_chunk_metadata["model"] = chunk_json.get("model")
                            if chunk_json.get("usage"):
                                last_chunk_metadata["usage"] = chunk_json.get("usage")
                            
                            choices = chunk_json.get("choices", [])
                            if choices:
                                content = choices[0].get("delta", {}).get("content", "")
                                if content:
                                    # Chạy hiệu ứng chữ mượt mà
                                    await print_smooth_text(content, delay=0.005)
                                    
                        except json.JSONDecodeError:
                            continue
                            
                print("\n---------------------------------------------")
                print("🏁 Stream Finished.")
                print(f"  🌐 Provider:     {last_chunk_metadata.get('provider')}")
                print(f"  🤖 Actual Model: {last_chunk_metadata.get('model')}")
                # Giả định hàm in usage_info của bạn:
                if last_chunk_metadata.get("usage"):
                    print(f"  📊 Usage: {last_chunk_metadata.get('usage')}")

    except httpx.RequestError as e:
        print(f"❌ Error: Could not connect to AI Gateway. Details: {e}")

async def main():
    await test_gemini_provider()
    await test_gemini_streaming()

if __name__ == "__main__":
    asyncio.run(main())