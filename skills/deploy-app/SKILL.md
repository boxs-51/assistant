---
name: deploy-app
description: Quy trình tự động build và deploy ứng dụng lên máy chủ local.
---

# QUY TRÌNH DEPLOY
1. Kiểm tra môi trường bằng tool `get_sys_info`.
2. Build ứng dụng bằng tool `execute_terminal` với lệnh `npm run build`.
3. Restart service.