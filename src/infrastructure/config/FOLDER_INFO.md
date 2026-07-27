# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** 10
- **Hash:** N/A
- **Depends On:** `pydantic`, `pyyaml`, `python-dotenv`, `structlog`
- **Scanned Files:** `__init__.py`, `base.py`, `core.py`, `exceptions.py`, `merge.py`, `schemas.py`, `utils.py`, `sources/dotenv_loader.py`, `sources/env_loader.py`, `sources/yaml_loader.py`

# 📂 Thư Mục: `config`

## 1. Architecture Decisions & Design Patterns
- **Patterns:**
  - **Registry:** `ConfigurationRegistry` hoạt động như một kho lưu trữ trung tâm cho đối tượng cấu hình đã được xác thực.
  - **Strategy:** Việc sử dụng `BaseConfigSource` và các lớp loader (`YamlLoader`, `EnvLoader`) thể hiện pattern Strategy, cho phép kết hợp nhiều nguồn cấu hình khác nhau.
  - **Proxy (Lazy Initialization):** `_SettingsProxy` trong `__init__.py` trì hoãn việc tải cấu hình cho đến lần truy cập đầu tiên, giúp tránh các lỗi về circular import và tối ưu hóa khởi động.
  - **Facade:** Đối tượng `settings` cung cấp một giao diện đơn giản để truy cập vào một hệ thống con phức tạp (tải, hợp nhất, và xác thực).
- **Decisions:**
  - **Layered Configuration:** Hệ thống tải cấu hình từ nhiều lớp với thứ tự ưu tiên rõ ràng (YAML < .env < Biến môi trường), đây là một phương pháp rất linh hoạt và mạnh mẽ.
  - **Schema-based Validation:** Sử dụng Pydantic (`ConfigSchema`) để định nghĩa và xác thực cấu trúc cấu hình. Điều này giúp hệ thống trở nên tin cậy, tự ghi lại tài liệu và an toàn về kiểu dữ liệu.
  - **Lazy Loading:** Việc sử dụng proxy (`settings`) để trì hoãn truy cập cấu hình đảm bảo các thành phần khác trong ứng dụng luôn nhận được một đối tượng cấu hình đã được tải và xác thực hoàn chỉnh.

## 2. Dependency & Ownership Graph
### Dependency
- `config.core` → `config.schemas`, `config.sources`, `config.merge`
- `config.sources` → `config.base`, `config.utils`
- Module `config` phụ thuộc vào các thư viện bên ngoài: `pydantic`, `pyyaml`, `python-dotenv`, `structlog`.

### Ownership & Lifetime
- `ConfigurationRegistry` (singleton ở mức class) **sở hữu** instance duy nhất của `ConfigSchema` trong suốt vòng đời của ứng dụng sau khi nó được tải.
- `ConfigLoader` tạo và sử dụng các instance của `BaseConfigSource` trong quá trình thực thi `load_config()`.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Quá trình tải cấu hình là đồng bộ và được thiết kế để chạy một lần duy nhất khi ứng dụng khởi động. Việc đọc cấu hình thông qua `settings` là an toàn trong môi trường đa luồng vì đây là thao tác đọc trên một đối tượng đã được khởi tạo.
- **Data Flow:**
  1. Ứng dụng khởi động và gọi `ConfigLoader.load_config()`.
  2. Các loader (`YamlLoader`, `DotEnvLoader`, `EnvLoader`) được gọi tuần tự.
  3. Dữ liệu từ các nguồn được hợp nhất (deep merge) thành một dictionary duy nhất.
  4. Dictionary này được xác thực bằng `ConfigSchema.model_validate()`, tạo ra một đối tượng `ConfigSchema`.
  5. Đối tượng đã xác thực được lưu vào `ConfigurationRegistry`.
  6. Các module khác truy cập cấu hình thông qua proxy `settings`.

## 4. Public APIs & Configuration
- **Public API:** Giao diện chính cho toàn bộ ứng dụng là đối tượng `settings` được import từ package `config` (`from src.config import settings`).
- **Configuration:** Toàn bộ module này là hệ thống cấu hình. Nó được cấu hình bởi sự hiện diện của file `config/default.yaml`, file `.env`, và các biến môi trường.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Memory:** Rủi ro thấp. Đối tượng cấu hình thường nhỏ.
- **Thread:** Rủi ro thấp. Được thiết kế để tải một lần khi khởi động.
- **Exception:** Rủi ro thấp. Việc `ConfigValidationError` được ném ra khi khởi động là một tính năng, giúp ngăn ứng dụng chạy với cấu hình không hợp lệ.
- **Complexity:** Logic proxy và lazy loading có thể hơi phức tạp cho người mới, nhưng các comment trong code đã giúp giảm thiểu rủi ro này.
- **Security:** **Rủi ro trung bình.** Các giá trị mặc định cho `session_secret_key` và `jwt_secret_key` là không an toàn. Cần phải ghi đè các giá trị này trong môi trường production thông qua biến môi trường hoặc các file cấu hình khác.

## 6. Technical Debt (TODO / FIXME / HACK)
- Các secret key mặc định trong `schemas.py` là một dạng nợ kỹ thuật. Một hệ thống tốt hơn có thể từ chối khởi động trong môi trường "production" nếu phát hiện các key này chưa được thay đổi.
- Module được thiết kế tốt, không có nợ kỹ thuật nào khác đáng chú ý.
