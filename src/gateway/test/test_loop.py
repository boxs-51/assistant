import base64
from pathlib import Path
import httpx
import asyncio
import os
import sys
import json
from dotenv import load_dotenv

# Load cấu hình
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ĐỊNH NGHĨA LẠI URL CHUẨN ĐẾN CÁC PROXY ENDPOINT
BASE_GATEWAY_URL = "http://localhost:8000/v1"
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", os.getenv("GEMINI_API_KEY", "change-me"))

headers = {
    "Authorization": f"Bearer {GATEWAY_API_KEY}",
}

def print_usage_info(usage_data: dict):
    if not usage_data:
        return
    print("\n📊 [Token Usage Info]")
    print(f"  └─ Prompt Tokens:      {usage_data.get('prompt_tokens', 0)}")
    print(f"  └─ Completion Tokens:  {usage_data.get('completion_tokens', 0)}")
    print(f"  └─ Total Tokens:       {usage_data.get('total_tokens', 0)}")

async def print_smooth_text(text: str, delay: float = 0.005):
    for char in text:
        print(char, end="", flush=True)
        await asyncio.sleep(delay)

# --- 1. TEST XEM CHI TIẾT MODEL ---
async def test_get_model_details(model_id: str, provider: str):
    print(f"\n🔎 [Hành động] Đang lấy chi tiết model '{model_id}' từ provider '{provider}'...")
    url = f"{BASE_GATEWAY_URL}/models/{model_id}"
    params = {"provider_name": provider}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 200:
                print("✅ Thông tin Model:")
                print(json.dumps(response.json(), indent=2, ensure_ascii=False))
            else:
                print(f"❌ Thất bại ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"❌ Lỗi kết nối: {e}")

# --- 2. TEST UPLOAD FILE ---
async def test_upload_file(file_path_str: str, provider: str) -> str:
    path = Path(file_path_str)
    if not path.exists():
        print(f"⚠️ Không tìm thấy file tại đường dẫn: {file_path_str}")
        return ""
        
    print(f"\n📤 [Hành động] Đang upload file '{path.name}' lên '{provider}'...")
    url = f"{BASE_GATEWAY_URL}/files"
    params = {"provider_name": provider}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            with open(path, "rb") as f:
                files = {"file": (path.name, f, "application/octet-stream")}
                response = await client.post(url, headers=headers, params=params, files=files)
                
            if response.status_code in [200, 201]:
                res_data = response.json()
                print(f"✅ Upload thành công! File ID nhận được: {res_data.get('id')}")
                return res_data.get("id")
            else:
                print(f"❌ Upload thất bại ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"❌ Lỗi upload: {e}")
    return ""

# --- 3. TEST LIST FILES ---
async def test_list_files(provider: str):
    print(f"\n📂 [Hành động] Đang lấy danh sách file trên hệ thống của '{provider}'...")
    url = f"{BASE_GATEWAY_URL}/files"
    params = {"provider_name": provider}
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 200:
                files = response.json()
                print(f"✅ Tìm thấy {len(files)} files:")
                for f in files:
                    print(f" └─ ID: {f.get('id')} | Tên: {f.get('filename')} | Type: {f.get('mime_type')}")
            else:
                print(f"❌ Thất bại ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"❌ Lỗi: {e}")

# --- 4. TEST XEM METADATA HOẶC DOWNLOAD FILE ---
async def test_get_or_download_file(file_id: str, provider: str, action: str = "metadata"):
    print(f"\n📄 [Hành động] Đang thực hiện '{action}' cho File ID: {file_id}...")
    url = f"{BASE_GATEWAY_URL}/files/{file_id}"
    params = {"provider_name": provider, "action": action}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 200:
                if action == "metadata":
                    print("✅ Metadata của File:")
                    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
                else:
                    # Giả định download trả về binary content
                    content_len = len(response.content)
                    print(f"✅ Đã tải file về thành công. Kích thước nhận được: {content_len} bytes.")
            else:
                print(f"❌ Thất bại ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"❌ Lỗi: {e}")

# --- 5. TEST XÓA FILE ---
async def test_delete_file(file_id: str, provider: str):
    print(f"\n🗑️ [Hành động] Đang yêu cầu xóa File ID: {file_id}...")
    url = f"{BASE_GATEWAY_URL}/files/{file_id}"
    params = {"provider_name": provider}
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.delete(url, headers=headers, params=params)
            if response.status_code in [200, 204]:
                print("✅ Đã xóa file thành công khỏi Provider.")
            else:
                print(f"❌ Xóa thất bại ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"❌ Lỗi: {e}")

# --- 6. VÒNG LẶP CHAT ĐA PHƯƠNG TIỆN (HUMAN INTERACTIVE LOOP) ---
async def interactive_chat_loop(provider: str, default_model: str):
    print(f"\n{'='*20} 🤖 INTERACTIVE MULTIMODAL CHAT MODE {'='*20}")
    print(f"  👉 Provider: {provider} | Model mặc định: {default_model}")
    print("\n📚 Các lệnh quản lý tệp và ngữ cảnh (Context Commands):")
    print("  /upload <đường_dẫn_file>  : Upload tệp mới và tự động đưa vào Ngữ cảnh hội thoại")
    print("  /list                     : Xem toàn bộ danh sách tệp trên hệ thống của Provider")
    print("  /get <file_id>            : Xem chi tiết cấu trúc Metadata của một tệp")
    print("  /download <file_id>       : Tải nội dung nhị phân (binary) của tệp về máy")
    print("  /delete <file_id>         : Xóa vĩnh viễn tệp trên Provider và xóa khỏi Ngữ cảnh")
    print("  /context                  : Xem danh sách các file đang nằm trong Ngữ cảnh hiện tại")
    print("  /clear                    : Xóa sạch lịch sử chat và các file đã ghim (Làm mới hội thoại)")
    print("  exit                      : Thoát chương trình hoàn toàn\n")
    
    # 🧠 KHỞI TẠO BỘ NHỚ NGỮ CẢNH (CONTEXT MEMORY) TRONG VÒNG LẶP
    conversation_history = [
        {"role": "system", "content": "Bạn là một trợ lý chuyên nghiệp. Khi người dùng yêu cầu xuất bảng biểu hoặc dữ liệu so sánh, "
                                        "bạn BẮT BUỘC phải sử dụng định dạng bảng HTML (<table>, <tr>, <td>) hoặc bảng Markdown (| Col 1 | Col 2 |). "
                                        "Không được giải thích bằng văn bản thô nếu có thể dùng bảng."}
    ]
    
    # Lưu trữ các file đang hoạt động trong session chat này: Dict[file_id, filename]
    active_contexts = {}

    while True:
        try:
            user_input = input("\n👤 Bạn: ").strip()
            if not user_input:
                continue
                
            if user_input.lower() == 'exit':
                print("👋 Tạm biệt bạn hiền!")
                break
                
            # --- XỬ LÝ LỆNH: /clear ---
            if user_input.lower() == "/clear":
                conversation_history = [conversation_history[0]] # Giữ lại system prompt
                active_contexts.clear()
                print("🧹 [Hệ thống] Đã xóa sạch lịch sử chat và làm mới Ngữ cảnh thành công!")
                continue

            # --- XỬ LÝ LỆNH: /context ---
            elif user_input.lower() == "/context":
                print(f"🧠 [Ngữ cảnh hiện tại] Đang giữ {len(active_contexts)} tệp tin:")
                if not active_contexts:
                    print("  └─ (Trống) Hãy dùng lệnh `/upload` để thêm file.")
                for fid, fname in active_contexts.items():
                    print(f"  └─ 📁 ID: {fid} ({fname})")
                continue

            # --- XỬ LÝ LỆNH: /upload ---
            elif user_input.startswith("/upload "):
                f_path = user_input.replace("/upload ", "").strip()
                uploaded_id = await test_upload_file(f_path, provider)
                if uploaded_id:
                    filename = Path(f_path).name 
                    active_contexts[uploaded_id] = filename  # Thêm vào context hoạt động
                    print(f"📌 [Context] Đã nạp thành công '{filename}' vào bộ nhớ vòng lặp.")
                    print("👉 Bây giờ bạn có thể đặt bất kỳ câu hỏi nào liên quan đến tệp này.")
                continue
                
            # --- XỬ LÝ LỆNH: /list ---
            elif user_input.strip() == "/list":
                await test_list_files(provider)
                continue
                
            # --- XỬ LÝ LỆNH: /get (Xem Metadata) ---
            elif user_input.startswith("/get "):
                f_id = user_input.replace("/get ", "").strip()
                await test_get_or_download_file(f_id, provider, action="metadata")
                continue

            # --- XỬ LÝ LỆNH: /download ---
            elif user_input.startswith("/download "):
                f_id = user_input.replace("/download ", "").strip()
                # Tải file về thông qua proxy endpoint hành động 'download'
                await test_get_or_download_file(f_id, provider, action="download")
                continue
                
            # --- XỬ LÝ LỆNH: /delete ---
            elif user_input.startswith("/delete "):
                f_id = user_input.replace("/delete ", "").strip()
                await test_delete_file(f_id, provider)
                # Nếu file đang nằm trong ngữ cảnh chat, xóa nó đi
                if f_id in active_contexts:
                    del active_contexts[f_id]
                    print(f"➖ Đã loại bỏ file '{f_id}' ra khỏi Ngữ cảnh hoạt động.")
                continue

            # --- XÂY DỰNG PAYLOAD MULTIMODAL DỰA TRÊN NGỮ CẢNH TÍCH LŨY ---
            current_message = {"role": "user"}
            
            if active_contexts:
                # Nếu có tệp trong bộ nhớ ngữ cảnh, đóng gói theo mảng List[MessageContentPart]
                content_parts = [{"type": "text", "text": user_input}]
                
                # Duyệt qua toàn bộ các file đang ghim trong ngữ cảnh và đính kèm vào tin nhắn
                for file_id in active_contexts.keys():
                    content_parts.append({
                        "type": "file",
                        "file": {
                            "id": file_id,
                            "mime_type": "application/octet-stream" # Adapter phía sau tự động map
                        }
                    })
                current_message["content"] = content_parts
            else:
                # Nếu ngữ cảnh trống, gửi chuỗi văn bản thuần túy
                current_message["content"] = user_input

            conversation_history.append(current_message)

            # --- TIẾN HÀNH STREAM CHAT VỚI PROXY GATEWAY ---
            print("🤖 AI: ", end="", flush=True)
            
            chat_url = f"{BASE_GATEWAY_URL}/chat/completions" 
            chat_payload = {
                "model": default_model,
                "provider": provider,
                "messages": conversation_history,
                "stream": True
            }

            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", chat_url, json=chat_payload, headers=headers) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        print(f"\n❌ Lỗi Chat ({response.status_code}): {body.decode()}")
                        # Nếu lỗi, rút câu message vừa thêm ra khỏi lịch sử để tránh làm bẩn context
                        conversation_history.pop()
                        continue
                        
                    assistant_response_segments = []
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        
                        data_content = line[5:].strip()
                        if data_content == "[DONE]":
                            break
                            
                        try:
                            chunk_json = json.loads(data_content)
                            choices = chunk_json.get("choices", [])
                            if choices:
                                content = choices[0].get("delta", {}).get("content", "")
                                if content:
                                    await print_smooth_text(content)
                                    assistant_response_segments.append(content)
                        except:
                            continue
                            
                    # Lưu câu trả lời của AI vào history hội thoại để chat tiếp ngữ cảnh liên tục
                    full_assistant_reply = "".join(assistant_response_segments)
                    conversation_history.append({"role": "assistant", "content": full_assistant_reply})
                    print() # Xuống dòng khi kết thúc stream

        except KeyboardInterrupt:
            print("\n[Hệ thống] Nhập 'exit' để thoát.")
        except Exception as e:
            print(f"\n❌ Lỗi trong luồng xử lý tương tác: {e}")
            
# --- HÀM CHẠY CHÍNH ---
async def main():
    provider = "gemini"
    model = "gemini-2.5-flash"
    
    print("=== BẮT ĐẦU CHẠY KHỞI ĐỘNG KIỂM TRA TOÀN BỘ ENDPOINT ===")
    
    # 1. Test xem cấu hình Model
    await test_get_model_details(model, provider)
    
    # 2. Test liệt kê file đang có sẵn trước khi vào chat
    await test_list_files(provider)
    
    # 3. Kích hoạt chế độ Human-in-the-loop để bạn tự gõ lệnh test bằng tay
    await interactive_chat_loop(provider, model)

if __name__ == "__main__":
    # Fix lỗi loop event trên Windows nếu cần
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())