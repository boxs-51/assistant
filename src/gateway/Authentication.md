
# Kế hoạch & Kiến trúc Module Authentication cho AI Gateway

Tài liệu này mô tả chi tiết kiến trúc, thiết kế và lộ trình triển khai cho module **Authentication & Authorization** của hệ thống AI Gateway. Mục tiêu là xây dựng một hệ thống multi-tenant, an toàn, và có khả năng mở rộng, gần với các API Gateway thương mại.

---

## Giai đoạn 1: Thiết kế Kiến trúc

### 1. Cấu trúc Module

Toàn bộ logic xác thực và phân quyền sẽ được đóng gói trong một module mới là `authentication` để đảm bảo tính độc lập và dễ quản lý, thay vì đưa tất cả vào một file `security.py`.

```
gateway/
└── authentication/
    ├── __init__.py
    ├── manager.py          # Class AuthenticationManager trung tâm
    ├── middleware.py       # Middleware xác thực cho mọi request
    ├── dependency.py       # FastAPI dependencies (get_identity, require_permission)
    ├── identity.py         # Định nghĩa Identity object
    ├── jwt.py              # Logic tạo và xác thực JSON Web Tokens
    ├── api_key.py          # Logic tạo và xác thực API Keys
    ├── password.py         # Logic hash và kiểm tra mật khẩu
    ├── permission.py       # Logic quản lý roles và permissions
    ├── quota.py            # Logic kiểm tra quota (sẽ tích hợp sau)
    ├── exceptions.py       # Các exception tùy chỉnh cho module
    │
    ├── repositories/       # Lớp truy cập dữ liệu (Data Access Layer)
    │   ├── __init__.py
    │   ├── users.py
    │   ├── api_keys.py
    │   └── organizations.py
    │
    ├── services/           # Lớp xử lý nghiệp vụ (Business Logic)
    │   ├── __init__.py
    │   ├── login.py
    │   ├── refresh.py
    │   └── register.py
    │
    └── schemas/            # Pydantic schemas cho request/response
        ├── __init__.py
        ├── login.py
        ├── register.py
        └── identity.py
```

### 2. Luồng Xử lý Request

Mọi request đi vào Gateway sẽ tuân theo một luồng xử lý chuẩn hóa, với Authentication là tầng đầu tiên.

```mermaid
graph TD
    A[HTTP Request] --> B{Authentication Middleware};
    B -- Xác thực thành công --> C[Tạo Identity Object];
    B -- Xác thực thất bại --> D[Trả về 401 Unauthorized];
    C --> E[Gắn Identity vào request.state];
    E --> F[Rate Limiter];
    F --> G[Input Guardrail];
    G --> H[Router];
    H --> I[Provider];
```

### 3. Identity Object

`Identity` là object quan trọng nhất, chứa toàn bộ thông tin về chủ thể đang thực hiện request sau khi đã xác thực thành công. Nó sẽ được gắn vào `request.state.identity` và được các module phía sau sử dụng.

**File: `gateway/authentication/schemas/identity.py`**
```python
from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class Identity(BaseModel):
    """
    Đối tượng chứa thông tin định danh của chủ thể sau khi xác thực.
    Đây là "Single Source of Truth" cho các tầng phía sau.
    """
    # ID chính
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    application_id: Optional[str] = None
    api_key_id: Optional[str] = None

    # Loại xác thực
    auth_type: Literal["jwt", "api_key"]

    # Thông tin về gói dịch vụ và quyền
    plan: str = "free"
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)

    class Config:
        frozen = True # Immutable
```

---

## Giai đoạn 2: Thiết kế Database

Sử dụng SQLAlchemy Core hoặc ORM để định nghĩa các bảng. Dưới đây là cấu trúc schema.

```sql
-- Bảng người dùng
CREATE TABLE users (
    id VARCHAR(255) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active', -- active, inactive, suspended
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Bảng tổ chức (khách hàng)
CREATE TABLE organizations (
    id VARCHAR(255) PRIMARY KEY,
    owner_id VARCHAR(255) NOT NULL REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    plan VARCHAR(50) NOT NULL DEFAULT 'free', -- free, pro, enterprise
    status VARCHAR(50) NOT NULL DEFAULT 'active', -- active, past_due, canceled
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Bảng thành viên trong tổ chức
CREATE TABLE members (
    organization_id VARCHAR(255) NOT NULL REFERENCES organizations(id),
    user_id VARCHAR(255) NOT NULL REFERENCES users(id),
    role VARCHAR(50) NOT NULL, -- admin, member, viewer
    PRIMARY KEY (organization_id, user_id)
);

-- Bảng ứng dụng (để nhóm các API key)
CREATE TABLE applications (
    id VARCHAR(255) PRIMARY KEY,
    organization_id VARCHAR(255) NOT NULL REFERENCES organizations(id),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Bảng API keys
CREATE TABLE api_keys (
    id VARCHAR(255) PRIMARY KEY,
    application_id VARCHAR(255) NOT NULL REFERENCES applications(id),
    prefix VARCHAR(10) NOT NULL UNIQUE, -- e.g., sk_live
    hashed_key VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active', -- active, revoked
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Bảng Refresh Tokens
CREATE TABLE refresh_tokens (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL REFERENCES users(id),
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

---

## Giai đoạn 3-5: Login, JWT và API Key

### Luồng Login (Dashboard)

1.  **Endpoint**: `POST /auth/login`
2.  **Request Body**:
    ```json
    {
        "email": "user@example.com",
        "password": "password123"
    }
    ```
3.  **Server Logic**:
    -   Tìm `user` bằng `email`.
    -   Dùng `password.py` để xác minh `password` với `password_hash`.
    -   Dùng `jwt.py` để tạo `access_token` (hết hạn ngắn, ví dụ 15 phút) và `refresh_token` (hết hạn dài, ví dụ 30 ngày).
    -   Lưu `refresh_token_hash` vào database.
4.  **Response Body**:
    ```json
    {
        "access_token": "...",
        "refresh_token": "..."
    }
    ```

### Cấu trúc JWT Payload

JWT chỉ dùng cho các tương tác từ giao diện Dashboard, không dùng cho các ứng dụng gọi API.

```json
{
    "sub": "user_id",         // Subject (User ID)
    "org_id": "org_id",       // Organization ID
    "role": "admin",          // Vai trò trong tổ chức
    "type": "access",         // Loại token
    "exp": 1678886400,        // Thời gian hết hạn
    "iat": 1678882800         // Thời gian tạo
}
```

### Luồng xác thực API Key

1.  **Request Header**: `Authorization: Bearer sk_live_xxxxxxxxxxxxxxxx`
2.  **Server Logic**:
    -   Tách `prefix` (e.g., `sk_live`) và `key_body`.
    -   Tìm `api_key` record trong DB bằng `prefix`.
    -   Dùng `api_key.py` để hash `key_body` và so sánh với `hashed_key` trong DB.
    -   **Bảo mật**: Sử dụng `hashlib.sha256` và so sánh bằng `hmac.compare_digest` để chống tấn công timing.

---

## Giai đoạn 6-7: Authentication Manager và Middleware

### AuthenticationManager

Đây là class điều phối chính, quyết định phương thức xác thực nào sẽ được sử dụng.

**File: `gateway/authentication/manager.py` (Pseudo-code)**
```python
class AuthenticationManager:
    def __init__(self, jwt_handler, api_key_handler, user_repo, org_repo):
        self.jwt_handler = jwt_handler
        self.api_key_handler = api_key_handler
        # ... inject repositories

    async def authenticate(self, request: Request) -> Identity:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthenticationError("Missing or malformed Authorization header")

        token = auth_header.split(" ")

        # Ưu tiên kiểm tra API Key trước vì nó phổ biến hơn cho machine-to-machine
        if token.startswith("sk_"):
            identity = await self.api_key_handler.verify(token)
            return identity

        # Nếu không phải API Key, thử xác thực bằng JWT
        try:
            identity = await self.jwt_handler.verify(token)
            return identity
        except JWTError:
            raise AuthenticationError("Invalid or expired JWT token")
```

### Middleware

Middleware sẽ tích hợp `AuthenticationManager` vào luồng request của FastAPI.

**File: `gateway/authentication/middleware.py` (Pseudo-code)**
```python
async def authentication_middleware(request: Request, call_next):
    # Bỏ qua xác thực cho các public endpoints (e.g., /auth/login, /health)
    if is_public_path(request.url.path):
        return await call_next(request)

    try:
        # Lấy auth_manager từ app.state hoặc dependency
        auth_manager = request.app.state.auth_manager
        identity = await auth_manager.authenticate(request)
        request.state.identity = identity
    except AuthenticationError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})

    response = await call_next(request)
    return response
```

---

## Giai đoạn 8-9: Permission và Dependency

### Hệ thống Role/Permission (RBAC)

-   **Roles**: Gán cho user trong một organization (e.g., `admin`, `member`, `viewer`).
-   **Permissions**: Các quyền hạn cụ thể, chi tiết (e.g., `model:gpt-4o:read`, `file:delete`, `admin:routing:reload`).
-   Một `Role` sẽ được map tới một tập hợp các `Permission`.

### Dependency cho FastAPI

Tạo các dependency để việc kiểm tra quyền trong các endpoint trở nên gọn gàng.

**File: `gateway/authentication/dependency.py` (Pseudo-code)**
```python
from .schemas.identity import Identity

def get_identity(request: Request) -> Identity:
    if not hasattr(request.state, "identity"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return request.state.identity

def require_permission(permission: str):
    def dependency(identity: Identity = Depends(get_identity)):
        if permission not in identity.permissions:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Required permission '{permission}' not found."
            )
        return identity
    return dependency

# Ví dụ sử dụng
require_admin_role = require_permission("role:admin")
```

---

## Giai đoạn 10-11: Tích hợp vào Gateway

### Cập nhật Endpoints

Thay thế `Depends(authenticate_client)` bằng dependency mới.

**Trước khi thay đổi:**
```python
@app.post("/v1/chat/completions")
async def chat_completions_proxy(
    request: Request,
    client_id: str = Depends(authenticate_client)
):
    # ...
```

**Sau khi thay đổi:**
```python
from gateway.authentication.dependency import get_identity
from gateway.authentication.schemas import Identity

@app.post("/v1/chat/completions")
async def chat_completions_proxy(
    request: Request,
    identity: Identity = Depends(get_identity)
):
    # Có thể dùng ngay thông tin từ identity
    org_id = identity.organization_id
    user_id = identity.user_id
    plan = identity.plan
    # ...
```

### Liên kết với các Module khác

| Module hiện có | Dữ liệu sử dụng từ `Identity` | Ghi chú |
| :--- | :--- | :--- |
| **RateLimiter** | `api_key_id`, `organization_id`, `plan` | Áp dụng các rule limit khác nhau cho từng plan. |
| **SemanticCache** | `organization_id` | Phân tách cache theo từng tenant (tổ chức). |
| **Router** | `plan`, `permissions` | Định tuyến request tới model/provider phù hợp với plan và quyền. |
| **File API** | `application_id`, `permissions` | Kiểm tra quyền `file:read`, `file:write` trên file. |
| **Metrics** | `organization_id`, `user_id`, `api_key_id` | Gắn tag vào metrics để theo dõi chi tiết. |
| **Billing** | `organization_id`, `api_key_id` | Ghi nhận usage để tính cước. |
| **Admin API** | `roles`, `permissions` | Bảo vệ các endpoint quản trị. |

---

## Lộ trình Triển khai (Implementation Chapters)

Chúng ta sẽ triển khai theo từng chương để đảm bảo chất lượng và dễ dàng kiểm soát.

-   **Chương 1: Thiết kế Database và Entity**
    -   Tạo models SQLAlchemy.
    -   Viết migration scripts (nếu dùng Alembic).

-   **Chương 2: Repository Pattern**
    -   Xây dựng các lớp `UserRepository`, `APIKeyRepository`... để trừu tượng hóa việc truy cập DB.

-   **Chương 3: Password Hash và JWT**
    -   Triển khai `password.py` với `passlib`.
    -   Triển khai `jwt.py` với `python-jose`.

-   **Chương 4: API Key Service**
    -   Triển khai `api_key.py` với logic tạo, hash và xác thực API key.

-   **Chương 5: AuthenticationManager**
    -   Kết hợp các thành phần trên vào `AuthenticationManager`.

-   **Chương 6: Middleware và Dependency**
    -   Viết `authentication_middleware` và các dependency `get_identity`, `require_permission`.

-   **Chương 7: RBAC (Role-Based Access Control)**
    -   Xây dựng logic map `role` sang `permission`.

-   **Chương 8: Refresh Token và Session**
    -   Hoàn thiện luồng refresh token để duy trì đăng nhập cho Dashboard.

-   **Chương 9: Quota và Billing Integration**
    -   Tích hợp logic kiểm tra quota và ghi nhận usage.

-   **Chương 10: Tích hợp hoàn chỉnh vào AI Gateway**
    -   Thay thế toàn bộ `authenticate_client` cũ.
    -   Cập nhật các module liên quan để sử dụng `Identity`.
    -   Viết unit tests và integration tests.

---

Tài liệu này sẽ được cập nhật liên tục trong quá trình phát triển.
```

Kế hoạch đã sẵn sàng. Chúng ta có thể bắt đầu với **Chương 1: Thiết kế Database và Entity** trong phiên làm việc tiếp theo.

<!--
[PROMPT_SUGGESTION]Bắt đầu triển khai Chương 1: Thiết kế Database và Entity bằng SQLAlchemy và Pydantic.[/PROMPT_SUGGESTION]
[PROMPT_SUGGESTION]Viết pseudo-code chi tiết hơn cho lớp AuthenticationManager trong file `gateway/authentication/manager.py`.[/PROMPT_SUGGESTION]
