from typing import Dict, Any

class CredentialManager:
    """Quản lý và giải quyết (Resolve) thông tin xác thực của User."""
    
    @staticmethod
    def get_mcp_credentials(server_name: str, user_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Tự động map đúng Token dựa trên yêu cầu của từng MCP Server."""
        credentials = {}
        
        if server_name == "gdrive":
            token = user_metadata.get("google_access_token")
            if token:
                credentials["access_token"] = token
                
        elif server_name == "github":
            token = user_metadata.get("github_token")
            if token:
                credentials["auth_token"] = token
                
        return credentials