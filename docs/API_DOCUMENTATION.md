# Tài liệu Mô tả Kỹ thuật Hệ thống API Gateway

Tài liệu này cung cấp mô tả chi tiết về các API endpoint, nguồn dữ liệu đầu vào (Input), đầu ra (Output), cùng cấu trúc vận hành luồng xử lý chuyên sâu của hệ thống **AI Gateway** (được xây dựng dựa trên FastAPI).

---

## 1. Tổng quan Cấu trúc Hệ thống

Hệ thống AI Gateway đóng vai trò là một tầng proxy thông minh kết nối các ứng dụng khách hàng với các nhà cung cấp mô hình trí tuệ nhân tạo lớn (LLM Providers như OpenAI, Gemini) một cách tối ưu, bảo mật và hiệu suất cao.

Các thành phần cốt lõi bao gồm:
* **Storage Engine:** Quản lý tập trung toàn bộ kết nối cơ sở dữ liệu, bộ nhớ đệm (Redis), cơ sở dữ liệu Vector và lưu trữ đối tượng nhị phân.
* **Semantic Cache:** Bộ nhớ đệm ngữ nghĩa tối ưu hóa tốc độ và chi phí bằng cách tái sử dụng các phản hồi tương tự từ các mô hình LLM.
* **Guardrail System:** Hệ thống rào chắn bảo mật bao gồm `InputGuardrail` chống Prompt Injection và `OutputGuardrail` lọc/khử trùng nội dung đầu ra an toàn.
* **Rate Limiter & Circuit Breaker:** Kiểm soát tần suất cuộc gọi API và tự động ngắt kết nối/chuyển mạch khi nhà cung cấp LLM gặp sự cố để đảm bảo tính sẵn sàng cao.

---

## 2. Nhóm Endpoints Quản trị (Admin Router)
Tiền tố chung: `/admin`  
*Yêu cầu quyền truy cập nâng cao thông qua phân hệ xác thực hệ thống.*

### 2.1 Tải lại quy tắc định tuyến hot-reload
* **Method:** `POST`
* **URL:** `/admin/reload/routing`
* **Mô tả:** Tải lại nóng các cấu hình và quy tắc định tuyến đa mô hình từ file cấu hình YAML mà không cần khởi động lại dịch vụ.
* **Yêu cầu xác thực:** Quyền `admin:write`
* **Dữ liệu đầu vào:** Không yêu cầu Body.
* **Dữ liệu đầu ra (JSON):**
    ```json
    {
        "status": "success",
        "message": "Routing rules reloaded successfully."
    }
    ```

### 2.2 Xem trạng thái các bộ ngắt mạch (Circuit Breakers)
* **Method:** `GET`
* **URL:** `/admin/circuit-breakers/status`
* **Mô tả:** Trả về thông tin chi tiết về trạng thái hoạt động hiện tại (Closed, Open, Half-Open), tổng số lỗi tích lũy và dấu thời gian xảy ra lỗi gần nhất của tất cả các kênh kết nối bên thứ ba.
* **Yêu cầu xác thực:** Quyền `admin:read`
* **Dữ liệu đầu ra (JSON):** Mảng chi tiết trạng thái của từng nhà cung cấp kết nối.

---

## 3. Nhóm Endpoints Xác thực & Khóa API (Authentication Router)
Tiền tố chung: `/auth`  
*Xử lý quy trình quản lý định danh người dùng, cấp phát mã JWT và tạo/thu hồi API Key.*

| Endpoint & Method | Dữ liệu đầu vào (Input) | Dữ liệu đầu ra (Output) / Phản hồi |
| :--- | :--- | :--- |
| `POST /auth/register/initiate` | `UserCreateSchema` (JSON gồm email, password) | `{"status": "success", "message": "OTP has been sent."}`. Lỗi 429 nếu yêu cầu gửi lại OTP quá nhanh. Lỗi 400 nếu email đã tồn tại. |
| `POST /auth/register/verify` | `VerifyOTPRequest` (JSON gồm email, otp) | `TokenSchema` chứa `access_token` và `refresh_token` khi xác thực OTP thành công. Lỗi 400 nếu OTP không hợp lệ. |
| `POST /auth/login` | `LoginRequestSchema` (Thông tin tài khoản email và mật khẩu) | `TokenSchema` (Mã Token truy cập). Lỗi 401 Unauthorized nếu sai thông tin xác thực. |
| `POST /auth/refresh` | `RefreshRequestSchema` (Chuỗi `refresh_token`) | `AccessTokenSchema` cấp mới chuỗi Access Token để duy trì phiên làm việc. |
| `POST /auth/logout` | `RefreshRequestSchema` (Chuỗi `refresh_token` cần hủy) | Trả về mã HTTP `204 No Content`. Client cần chủ động xóa token khỏi bộ nhớ. |
| `GET /auth/oauth/login/{provider}` | Tham số đường dẫn `provider` (Ví dụ: `github`, `google`) | Redirect người dùng sang trang xác thực của OAuth provider. |
| `GET /auth/oauth/callback/{provider}` | Nhận mã code xác thực từ OAuth Provider | Tự động xử lý, tạo/đăng nhập người dùng và chuyển hướng về `FRONTEND_OAUTH_CALLBACK_URL` kèm theo token. |
| `POST /auth/oauth/{provider}` | `OAuthUserInfoSchema` (Thông tin người dùng từ provider) | `TokenSchema`. Dùng khi client tự xử lý luồng OAuth và gửi thông tin người dùng về server. |
| `POST /auth/api-keys` | `APIKeyCreateSchema` (Tên gợi nhớ cho khóa) | `APIKeyResponseSchema` (Thông tin khóa API được tạo, bao gồm chuỗi secret hiển thị một lần). Yêu cầu xác thực JWT. |
| `GET /auth/api-keys` | Không có. Xác thực qua JWT Header. | Danh sách `List[APIKeyInfoSchema]` hiển thị các thông tin cơ bản về các khóa API của người dùng. |
| `DELETE /auth/api-keys/{key_id}` | Tham số đường dẫn `key_id` | Mã trạng thái `204 No Content` báo hiệu thu hồi khóa thành công. |
| `GET /auth/me` | Không có. Xác thực qua JWT Header. | `UserMeSchema` chứa thông tin hồ sơ (ID, email, roles) của tài khoản đang đăng nhập. |

---

## 4. Phân hệ Kết nối Mô hình Ngôn ngữ (LLM APIs Router)

### 4.1 Kết nối Đa mô hình thông minh (Chat Completions Proxy)
* **Method:** `POST`
* **URL:** `/v1/chat/completions`
* **Mô tả quy trình xử lý nội bộ:**
    1.  Đọc và kiểm tra cấu trúc dữ liệu đầu vào thông qua schema `GatewayChatRequest`.
    2.  Tách biệt văn bản chính và bóc tách các chữ ký media đầu vào (Image, Audio, Video dưới dạng Base64) để băm chuỗi MD5 tạo thành khóa định danh **Cache Key**.
    3.  Chạy đồng thời song song hai tác vụ: Kiểm tra an toàn bảo mật (**Input Fillter** tránh Prompt Injection) và giới hạn tần suất cuộc gọi (**Rate Limiter**).
    4.  Tra cứu trên **Semantic Cache**: Nếu phát hiện câu hỏi tương đồng đã có trong bộ đệm dữ liệu trước đó, hệ thống lập tức trả về kết quả an toàn sau khi lọc qua `OutputFillter` mà không cần gọi đến mô hình gốc, giúp tiết kiệm tối đa độ trễ và chi phí.
    5.  Định tuyến thông minh (Smart Routing) kèm cơ chế tự động chuyển đổi dự phòng (Fallback Mechanism) đến các nhà cung cấp mô hình AI khả dụng dựa theo trạng thái của Circuit Breaker.
* **Dữ liệu đầu vào:** Đối tượng `GatewayChatRequest` bao gồm mảng danh sách các thông điệp `messages` (hỗ trợ văn bản hoặc định dạng multimodal đính kèm file nhị phân mã hóa base64) cùng cấu hình vận hành `config`.
* **Dữ liệu đầu ra:**
    * *Nếu cấu hình đặt `stream: false`:* Trả về một đối tượng JSON cấu trúc `GatewayResponse` đồng bộ chứa đầy đủ nội dung hoàn thiện cùng thống kê chi tiết số lượng Token tiêu thụ (`usage`).
    * *Nếu cấu hình đặt `stream: true`:* Trả về luồng dữ liệu `StreamingResponse` (định dạng chuẩn Server-Sent Events `text/event-stream`) truyền tải liên tục các đoạn text nhỏ cho ứng dụng client hiển thị theo thời gian thực, kết thúc bằng chuỗi nhận diện `data: [DONE]`.

### 4.2 Tạo Vector Trực quan hóa Văn bản (Embeddings Proxy)
* **Method:** `POST`
* **URL:** `/v1/embeddings`
* **Mô tả:** Chuyển tiếp yêu cầu số hóa văn bản thành mảng vector độ phân giải cao phục vụ cho tra cứu ngữ nghĩa RAG.
* **Dữ liệu đầu vào:** JSON body chuẩn chứa chuỗi văn bản cần chuyển đổi.
* **Dữ liệu đầu ra:** Mảng vector số thực phản hồi từ nhà cung cấp mô hình đích.

---

## 5. Hệ thống Quản lý Tệp tin (Files Proxy Router)
Tiền tố chung: `/v1/files`  
*Cung cấp khả năng lưu trữ, truyền phát trực tiếp và tra cứu metadata của tệp đính kèm trên hệ thống bộ nhớ nhà cung cấp AI bên thứ ba.*

### 5.1 Lấy danh sách tệp tin
* **Method:** `GET`
* **URL:** `/v1/files/`
* **Tham số truy vấn (Query Params):** `provider_name` (bắt buộc), `page_size` (tùy chọn), `page_token` (tùy chọn).
* **Dữ liệu đầu ra:** Danh sách mảng thông tin các tệp tin lưu trữ sẵn có trên hệ thống đích.

### 5.2 Tải tệp tin lên hệ thống (Upload via Stream)
* **Method:** `POST`
* **URL:** `/v1/files/`
* **Dữ liệu đầu vào (Multipart/Form-Data):**
    * Tham số URL Query: `provider_name` (bắt buộc), `display_name` (tùy chọn).
    * Dữ liệu File Form: Đối tượng tệp tin nhị phân `file` (kiểu dữ liệu `UploadFile`).
* **Dữ liệu đầu ra:** Đối tượng JSON chứa kết quả định danh và đường dẫn lưu trữ tệp tin thành công trên provider.

### 5.3 Chi tiết Metadata hoặc Tải tệp tin nhị phân
* **Method:** `GET`
* **URL:** `/v1/files/{file_id:path}`
* **Tham số truy vấn:**
    * `provider_name`: Tên nhà cung cấp đích (bắt buộc).
    * `action`: Nhận một trong hai giá trị: `metadata` (mặc định) để lấy thông tin tệp, hoặc `download` để tải nội dung tệp.
* **Dữ liệu đầu ra:** Trả về thông tin JSON chi tiết hoặc một luồng byte nhị phân `StreamingResponse` đính kèm header cấu hình tải về tương ứng `Content-Disposition: attachment`.

### 5.4 Xóa tệp tin khỏi hệ thống lưu trữ
* **Method:** `DELETE`
* **URL:** `/v1/files/{file_id:path}`
* **Dữ liệu đầu vào:** `file_id` trên đường dẫn và tham số truy vấn xác định rõ `provider_name`.
* **Dữ liệu đầu ra:** Mã trạng thái HTTP `204 No Content` báo hiệu xóa tệp thành công.

---

## 6. Quản lý Danh mục Mô hình & Giám sát Sức khỏe Hệ thống

### 6.1 Lấy danh sách mô hình AI khả dụng
* **Method:** `GET`
* **URL:** `/v1/models/`
* **Tham số truy vấn:** `provider_name` (bắt buộc, xác định nhà cung cấp cần liệt kê).
* **Đặc trưng xử lý:** Sau khi nhận danh sách thô từ đối tác bên thứ ba, hệ thống chạy qua `capability_manager.enrich_capabilities` để bổ sung thông tin tính năng đặc trưng riêng biệt được hệ thống AI Gateway hỗ trợ.
* **Dữ liệu đầu ra:** Danh sách các model đã được làm giàu thông tin.

### 6.2 Xem chi tiết tính năng của một mô hình cụ thể
* **Method:** `GET`
* **URL:** `/v1/models/{model_id:path}`
* **Tham số truy vấn:** `provider_name` (bắt buộc).
* **Dữ liệu đầu ra:** Cấu trúc JSON làm giàu thông tin mô hình chi tiết sau khi đã qua bộ bổ sung tính năng.

### 6.3 Các Endpoints Giám sát Hệ thống (Liveness & Readiness Probes)
Các endpoints này được thiết kế để phục vụ hệ thống tự động hóa điều phối như Kubernetes hoặc bảng điều khiển nội bộ:
* `GET /health`: **Liveness Probe.** Kiểm tra khả năng sống của tiến trình độc lập. Luôn trả về `{"status": "ok"}` nếu ứng dụng đang chạy.
* `GET /ready`: **Readiness Probe.** Kiểm tra sự sẵn sàng của các dịch vụ phụ thuộc (Redis, LLM Providers). Trả về `{"status": "ready"}` nếu tất cả các kết nối đều tốt, ngược lại trả về lỗi `503 Service Unavailable`.
* `GET /metrics`: **Prometheus Endpoint.** Cung cấp dữ liệu thô phục vụ cho hệ thống Prometheus thu thập (scrape) dưới dạng `text/plain`.
* `GET /stats`: **Internal Statistics.** Trả về JSON chứa các số liệu hoạt động như phần trăm tải CPU, dung lượng bộ nhớ RAM tiêu thụ, tên và phiên bản của gateway.