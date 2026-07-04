import httpx
import asyncio
import os
import json

# --- Cấu hình ---
GATEWAY_URL = "http://127.0.0.1:8000"
# Lấy API key từ biến môi trường hoặc điền trực tiếp
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "change-me")

async def test_get_model_details(provider: str, model_id: str):
    """
    Hàm để kiểm tra endpoint lấy thông tin chi tiết của một model.
    """
    print(f"\n--- 🧪 Testing GET /v1/models/{model_id} for provider: {provider} ---")
    
    url = f"{GATEWAY_URL}/v1/models/{model_id}?provider_name={provider}"
    headers = {
        "Authorization": f"Bearer {GATEWAY_API_KEY}",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()  # Ném lỗi nếu status code là 4xx hoặc 5xx
            
            response_data = response.json()
            
            print(f"✅ Success! Received response for {provider}/{model_id}:")
            print("---------------------------------------------")
            print(json.dumps(response_data, indent=2, ensure_ascii=False))
            print("---------------------------------------------")

    except httpx.HTTPStatusError as e:
        print(f"❌ Error: Request failed with status {e.response.status_code}")
        print(f"   Response: {e.response.text}")
    except httpx.RequestError as e:
        print(f"❌ Error: Could not connect to the AI Gateway. Details: {e}")
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")

async def main():
    """Hàm chính để chạy tất cả các bài test."""
    if GATEWAY_API_KEY == "your_default_api_key_here":
        print("⚠️ CẢNH BÁO: Vui lòng thiết lập biến môi trường GATEWAY_API_KEY hoặc thay đổi giá trị mặc định trong file.")
        return

    # Test với các provider và model khác nhau
    await test_get_model_details(provider="openai", model_id="gpt-4o")
    await test_get_model_details(provider="gemini", model_id="gemini-2.5-flash")
    await test_get_model_details(provider="ollama", model_id="llama3")

if __name__ == "__main__":
    asyncio.run(main())
