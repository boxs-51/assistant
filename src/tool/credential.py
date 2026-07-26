from typing import Dict, Any

class CredentialManager:
    """Quản lý và giải quyết (Resolve) thông tin xác thực của User."""
    
    @staticmethod
    def get_mcp_credentials(server_name: str, scopes: set) -> Dict[str, Any]:
        """Tự động map đúng Token dựa trên yêu cầu của từng MCP Server."""
        credentials = {}
        
        if server_name == "gdrive":
            token = scopes.get("google_access_token") # Giả sử token được lưu trong scopes
            if token:
                credentials["access_token"] = token
                
        elif server_name == "github":
            token = scopes.get("github_token") # Giả sử token được lưu trong scopes
            if token:
                credentials["auth_token"] = token
                
        return credentials