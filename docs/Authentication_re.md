# Kế hoạch tái cấu trúc hệ thống Authentication theo hướng mở rộng (Enterprise Architecture)

## Mục tiêu

Thiết kế lại toàn bộ module Authentication để:

- Dễ mở rộng khi thêm tính năng mới.
- Giảm sự phụ thuộc giữa các module.
- Tăng khả năng bảo trì.
- Hỗ trợ nhiều loại xác thực trong tương lai.
- Hỗ trợ nhiều Organization, nhiều Tenant.
- Hỗ trợ Audit, Billing, Quota, Device, MFA...
- Chuẩn bị cho AI Gateway quy mô lớn.

---

# Kiến trúc tổng thể mong muốn

```

```
                    ┌─────────────────────┐
                    │      Router         │
                    └─────────┬───────────┘
                              │
                              ▼
                 ┌────────────────────────┐
                 │ AuthenticationFacade   │
                 └─────────┬──────────────┘
                           │
      ┌────────────────────┼─────────────────────┐
      ▼                    ▼                     ▼

RegistrationService   LoginService      OAuthService

      ▼                    ▼                     ▼

TokenService       SessionService     APIKeyService

      ▼                    ▼                     ▼

OTPService        PermissionService   UserService

                           ▼

                    Repository Layer

                           ▼

                 Storage Framework

                           ▼

      PostgreSQL / Redis / Cache / ...

```

---

# Giai đoạn 1: Chuẩn hóa Repository Layer

## Mục tiêu

Repository chỉ làm đúng một nhiệm vụ:

- CRUD
- Query
- Không xử lý Business Logic
- Không commit transaction

---

## 1. Tách UserRepository

Hiện tại UserRepository đang xử lý:

- User
- OAuth
- Pending Registration
- Role
- Organization

Nên tách thành

```
repositories/

    users.py

    members.py

    organizations.py

    oauth_accounts.py

    pending_registrations.py

    sessions.py

    api_keys.py
```

---

## 2. PendingRegistrationRepository

Tạo Repository riêng.

```
PendingRegistrationRepository

create()

update()

delete()

get()

get_by_email()

cleanup_expired()
```

Không để trong UserRepository.

---

## 3. Repository không commit

Hiện tại

```
Repository

↓

commit()
```

Đổi thành

```
Repository

↓

add()

↓

return entity
```

Commit sẽ do Unit Of Work quản lý.

---

## 4. RepositoryManager

Hiện Service phải inject nhiều Repository.

```
AuthenticationService(

user_repo,

member_repo,

session_repo,

oauth_repo,

organization_repo,

...)

```

Đổi thành

```
RepositoryManager

repositories.users

repositories.members

repositories.oauth

repositories.sessions
```

Service chỉ cần

```
repositories
```

---

# Giai đoạn 2: Unit Of Work

## Mục tiêu

Quản lý Transaction tập trung.

---

Ví dụ

```
BEGIN

↓

Create User

↓

Create Organization

↓

Create Member

↓

Create OAuth

↓

Commit
```

Nếu lỗi

```
Rollback
```

---

Tạo

```
storage/

    unit_of_work.py
```

Ví dụ

```
async with uow:

    user = ...

    org = ...

    member = ...

    await uow.commit()
```

---

# Giai đoạn 3: Chia nhỏ AuthenticationService

## Mục tiêu

Mỗi Service chỉ xử lý một nghiệp vụ.

---

Hiện

```
AuthenticationService
```

đang xử lý:

- Register
- Login
- OAuth
- Refresh
- Logout
- OTP
- User Info

Sau khi tách

```
authentication/

    services/

        registration_service.py

        login_service.py

        oauth_service.py

        otp_service.py

        token_service.py

        session_service.py

        user_service.py

        api_key_service.py
```

---

AuthenticationFacade

```
AuthenticationFacade

↓

RegistrationService

↓

LoginService

↓

OAuthService

↓

TokenService

...
```

Router chỉ gọi Facade.

---

# Giai đoạn 4: Chuẩn hóa Authentication Manager

Hiện tại

```
Bearer

↓

Admin Key

↓

API Key

↓

JWT
```

Sau này nên dùng Strategy Pattern.

```
AuthenticationManager

↓

AuthenticatorFactory

↓

Authenticator
```

Các Authenticator

```
JWTAuthenticator

APIKeyAuthenticator

AdminKeyAuthenticator

PATAuthenticator

WebhookAuthenticator

SessionAuthenticator

CookieAuthenticator
```

Sau này thêm loại xác thực mới không cần sửa Manager.

---

# Giai đoạn 5: Chuẩn hóa Identity

Identity nên trở thành trung tâm của hệ thống.

```
Identity

user_id

organization_id

tenant_id

roles

permissions

plan

session_id

application_id

api_key_id

request_id

device_id

auth_type

scopes
```

Sau này Middleware không cần Query lại.

---

# Giai đoạn 6: Permission System

Hiện tại

```
Role

↓

Permission
```

Nên mở rộng thành

```
Permission

Role

RolePermission

MemberRole

OrganizationRole
```

Có thể hỗ trợ

- Custom Role
- Dynamic Permission
- Tenant Permission

---

# Giai đoạn 7: API Key

Chuẩn hóa API Key.

```
sk_v1_xxxxx

ak_v1_xxxxx
```

Thêm Version.

APIKey

```
id

prefix

hash

status

application_id

created_at

expires_at

last_used_at

rotated_at

description
```

Thêm

```
rotation

expiration

scope

quota
```

---

# Giai đoạn 8: OTP System

Không lưu OTP Plain Text.

Model

```
PendingRegistration

email

otp_hash

payload

attempt_count

resend_count

expires_at

last_sent_at

created_at

updated_at
```

Payload chỉ lưu

```
email

password_hash

name
```

OTP hash riêng.

---

# Giai đoạn 9: Session System

Session không chỉ lưu Refresh Token.

```
Session

id

user_id

refresh_hash

device

browser

ip

created_at

expires_at

last_seen

revoked
```

Hỗ trợ

- Logout Device
- Logout All
- Device Management

---

# Giai đoạn 10: Audit Log

Thêm AuditService.

```
Audit

Login

Logout

Refresh

OTP

OAuth

API Key

Permission

Admin Action
```

Model

```
AuditLog

id

user_id

action

resource

ip

device

status

metadata

created_at
```

---

# Giai đoạn 11: Event System

Không gọi trực tiếp.

Ví dụ

```
Register

↓

UserCreated Event

↓

Create Organization

↓

Send Email

↓

Audit

↓

Billing
```

Thay vì

```
Register

↓

Create User

↓

Create Org

↓

Send Email

↓

Audit

↓

...
```

Event giúp mở rộng cực dễ.

---

# Giai đoạn 12: Cache Layer

Tận dụng Storage Framework.

```
Storage

↓

Cache

↓

Redis

Memory

Hybrid
```

Authentication không biết Redis.

Chỉ biết

```
cache.get()

cache.set()

cache.delete()
```

---

# Giai đoạn 13: Billing Ready

Identity nên có

```
plan

quota

subscription

feature_flags
```

Để sau này

```
Free

Pro

Enterprise
```

không phải sửa Authentication.

---

# Giai đoạn 14: Multi Tenant

Identity

```
tenant_id

organization_id

member_id
```

Repository

```
get_by_tenant()

get_by_org()
```

Toàn bộ hệ thống sẽ Multi Tenant.

---

# Giai đoạn 15: Chuẩn hóa Dependency Injection

Thay vì

```
AuthenticationService(

user_repo,

member_repo,

oauth_repo,

...

)
```

Dùng

```
AuthenticationContext

repositories

cache

storage

config

logger

event_bus
```

Service chỉ cần Context.

---

# Giai đoạn 16: Chuẩn hóa Domain Layer

```
authentication/

    facade/

    services/

    managers/

    authenticators/

    providers/

    policies/

    validators/

    events/

    exceptions/

    dto/

    schemas/

    dependency.py
```

---

# Giai đoạn 17: Chuẩn bị cho AI Gateway

Sau khi hoàn thành Authentication có thể thêm trực tiếp

```
Quota

Billing

Usage

Rate Limit

Project

Workspace

Webhook

Model Permission

Storage Permission

File Permission

Tool Permission

Agent Permission

API Analytics
```

mà gần như không phải sửa Authentication.

---

# Lộ trình triển khai

## Giai đoạn 1

- Tách Repository
- Tách PendingRepository
- RepositoryManager

## Giai đoạn 2

- Unit Of Work
- Bỏ commit trong Repository

## Giai đoạn 3

- Tách AuthenticationService
- AuthenticationFacade

## Giai đoạn 4

- Strategy Authentication

## Giai đoạn 5

- Identity mở rộng

## Giai đoạn 6

- Permission System

## Giai đoạn 7

- Audit
- Event
- Cache

## Giai đoạn 8

- Billing
- Quota
- Multi Tenant
- Feature Flag

---

# Kết quả mong muốn

Sau khi hoàn thành, module Authentication sẽ có các đặc điểm:

- Kiến trúc phân tầng rõ ràng, mỗi lớp chỉ đảm nhiệm một trách nhiệm.
- Dễ dàng thêm phương thức xác thực mới (JWT, API Key, OAuth, PAT, Webhook...) mà không phải sửa mã hiện có.
- Hỗ trợ Unit of Work để đảm bảo transaction nhất quán.
- Repository thuần túy chỉ thực hiện truy cập dữ liệu, không chứa business logic.
- Dễ tích hợp Event Bus, Audit Log, Cache và Billing.
- Sẵn sàng cho Multi-Tenant, RBAC mở rộng và Feature Flags.
- Tương thích với định hướng phát triển AI Gateway quy mô lớn, nơi Authentication chỉ là một module độc lập có thể tái sử dụng cho các dịch vụ khác.