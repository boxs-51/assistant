# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** 1
- **Hash:** N/A
- **Depends On:** `..schemas.agent`, `structlog`
- **Scanned Files:** `registry.py`

# 📂 Thư Mục: `agent`

## 1. Architecture Decisions & Design Patterns
- **Patterns:** Registry
- **Decisions:** Sử dụng một dictionary đơn giản để quản lý các `AgentDefinition`. Đây là một cơ chế trung tâm để đăng ký và truy xuất các agent của hệ thống.

## 2. Dependency & Ownership Graph
### Dependency
`agent` → `schemas.agent`
`agent` → `structlog`

### Ownership & Lifetime
- `AgentRegistry` **owns** a dictionary of `AgentDefinition` objects.
- Vòng đời của `AgentRegistry` và các agent mà nó quản lý sẽ được quyết định bởi component khởi tạo nó.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn đồng bộ (synchronous). Class này không an toàn cho môi trường đa luồng (not thread-safe). Việc truy cập đồng thời vào `_agents` từ nhiều luồng có thể gây ra data race.
- **Event Flow:** Không có event flow phức tạp.
- **Data Flow:** `AgentDefinition` object được truyền vào `register()` để lưu trữ. `get()` được dùng để truy xuất `AgentDefinition` theo tên.

## 4. Public APIs & Configuration
- **APIs:**
  - `AgentRegistry.register(definition: AgentDefinition)`
  - `AgentRegistry.get(name: str) -> Optional[AgentDefinition]`
- **Configuration:** Không có cấu hình bên ngoài.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Memory:** Rủi ro thấp. Lượng bộ nhớ sử dụng phụ thuộc vào số lượng agent được đăng ký.
- **Thread:** **Rủi ro trung bình.** Class không được thiết kế để an toàn trong môi trường đa luồng. Nếu hệ thống sử dụng nhiều luồng, cần phải có cơ chế khóa (locking) khi truy cập registry để tránh data race.
- **Rendering:** Không áp dụng.
- **Exception:** Rủi ro thấp.
- **Performance / Complexity:** Rủi ro thấp. Độ phức tạp trung bình là O(1) cho các hoạt động chính.
- **Ownership:** Rủi ro thấp.

## 6. Technical Debt (TODO / FIXME / HACK)
- **TODO:** Cần xem xét thêm cơ chế thread-safe (ví dụ: sử dụng `threading.Lock`) nếu `AgentRegistry` được truy cập từ nhiều luồng đồng thời.
