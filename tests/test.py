import httpx
import asyncio
import os
from dotenv import load_dotenv

# Tải các biến môi trường từ file .env ở thư mục gốc của dự án
# Cần cài đặt: pip install python-dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

GATEWAY_URL = "http://localhost:8000/v1/chat/completions"
# Lấy API key từ biến môi trường, an toàn hơn hardcode
GATEWAY_API_KEY = os.getenv("GEMINI_API_KEY", "change-me")

async def test_gemini_provider():
    """
    Test 1: Gửi một request non-streaming đến AI Gateway, nhắm đến Gemini provider.
    """
    print("\n--- 🚀 [Test 1] Testing Gemini (Non-streaming) ---")
    payload = {
        "model": "gemini-1.5-pro",  # Chỉ định model của Gemini
        "provider": "gemini",
        "messages": [
            {"role": "user", "content": "Viết một bài thơ ngắn về AI Gateway."}
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
            response_data = response.json()
            
            print("✅ Success! Received response from Gateway:")
            print("---------------------------------------------")
            print(response_data["choices"][0]["message"]["content"])
            print("---------------------------------------------")

    except httpx.HTTPStatusError as e:
        print(f"\n❌ Error: Request failed with status {e.response.status_code}")
        print(f"   Response: {e.response.text}")
    except httpx.RequestError as e:
        print(f"❌ Error: Could not connect to the AI Gateway at {GATEWAY_URL}. Details: {e}")

async def test_gemini_streaming():
    """
    Test 2: Gửi một request streaming đến AI Gateway, nhắm đến Gemini provider.
    """
    print("\n--- 🚀 [Test 2] Testing Gemini (Streaming) ---")
    payload = {
        "model": "gemini-pro",
        "messages": [
            {"role": "user", "content": "Kể một câu chuyện cười ngắn."}
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
                response.raise_for_status()
                print("✅ Success! Receiving stream from Gateway:")
                print("---------------------------------------------", end="", flush=True)
                async for chunk in response.aiter_text():
                    print(chunk, end="", flush=True)
                print("\n---------------------------------------------")

    except httpx.HTTPStatusError as e:
        print(f"❌ Error: Request failed with status {e.response.status_code}")
        print(f"   Response: {e.response.text}")
    except httpx.RequestError as e:
        print(f"❌ Error: Could not connect to the AI Gateway at {GATEWAY_URL}. Details: {e}")

async def test_ollama_provider():
    """
    Test 3: Gửi một request đến AI Gateway, nhắm đến Ollama provider (local).
    """
    print("\n--- 🚀 [Test 3] Testing Ollama (Local) ---")
    payload = {
        "model": "llama3",  # Giả sử bạn có model llama3 trên Ollama
        "provider_preference": "local", # Yêu cầu ưu tiên provider local
        "messages": [
            {"role": "user", "content": "Viết một hàm Python để tính tổng hai số."}
        ],
    }

    headers = {
        "Authorization": f"Bearer {GATEWAY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(GATEWAY_URL, json=payload, headers=headers)
            response.raise_for_status()
            response_data = response.json()
            
            print("✅ Success! Received response from Gateway:")
            print("---------------------------------------------")
            print(response_data["choices"][0]["message"]["content"])
            print("---------------------------------------------")

    except httpx.HTTPStatusError as e:
        print(f"❌ Error: Request failed with status {e.response.status_code}")
        print(f"   Response: {e.response.text}")
    except httpx.RequestError as e:
        print(f"❌ Error: Could not connect to the AI Gateway at {GATEWAY_URL}. Details: {e}")
        print("   Is Ollama server running?")

async def main():
    await test_gemini_provider()
    await test_gemini_streaming()
    await test_ollama_provider()

if __name__ == "__main__":
    asyncio.run(main())
