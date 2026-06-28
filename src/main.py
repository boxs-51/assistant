import asyncio
from orchestrator import AgentOrchestrator

async def main():
    agent = AgentOrchestrator()
    
    session_id = "test_session_123"
    # Test thử một yêu cầu thông thường
    req = "Hãy tìm thông tin cập nhật giá Bitcoin hôm nay và tóm tắt lại."
    result = await agent.run(session_id=session_id, user_request=req)
    
    print("\n=== TIN NHẮN ĐẦU RA ===")
    print(result)

if __name__ == "__main__":
    asyncio.run(main())