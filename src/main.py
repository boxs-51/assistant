from src.orchestrator import AgentOrchestrator

def main():
    agent = AgentOrchestrator()
    
    # Test thử một yêu cầu thông thường
    req = "Hãy tìm thông tin cập nhật giá Bitcoin hôm nay và tóm tắt lại."
    result = agent.run(req)
    
    print("\n=== TIN NHẮN ĐẦU RA ===")
    print(result)

if __name__ == "__main__":
    main()