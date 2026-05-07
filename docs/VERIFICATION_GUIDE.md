# Hướng dẫn Kiểm tra & Xác minh Hệ thống (Verification Guide)

Sau khi cập nhật code, hãy thực hiện các bước sau để đảm bảo mọi tính năng mới hoạt động hoàn hảo.

---

## 1. Kiểm tra Local SQLite DB

Bây giờ cấu hình AI được lưu trong file database thay vì `.env`.

**Cách kiểm tra:**
1. Mở Launcher (`python run_launcher.py`).
2. Tab **Config & Control** -> Chọn một model và một scaler khác -> Nhấn **Apply**.
3. Dùng công cụ [DB Browser for SQLite](https://sqlitebrowser.org/) hoặc command line để mở file `data/fog_local.db`.
4. Xem bảng `fog_config`. Bạn sẽ thấy 2 dòng:
   - `model_path`: đường dẫn bạn vừa chọn.
   - `scaler_path`: đường dẫn scaler tương ứng.
5. Kiểm tra file `.env`: Các biến `MODEL_PATH` và `SCALER_PATH` sẽ **không đổi** (đúng như thiết kế).

---

## 2. Kiểm tra AI Hot-Reload (Đổi model không restart)

**Cách kiểm tra:**
1. Đảm bảo Fog Node đang chạy (`Start Services`).
2. Trong tab **Config & Control**, chọn một cặp model mới.
3. Nhấn **Apply**.
4. Quan sát ô **Console Log**:
   - Launcher báo: `🔥 Hot-reload sent...`
   - Fog Node phản hồi: `[HOT-RELOAD] ✅ Model swapped: [tên model] + [tên scaler]`
5. **Xác nhận:** ESP32 vẫn gửi dữ liệu liên tục, không bị ngắt kết nối (đèn Mosquitto vẫn xanh suốt quá trình đổi).

---

## 3. Kiểm tra Offline Cloud Queue (Mất mạng AWS)

Đây là tính năng quan trọng để không mất dữ liệu khi rớt mạng.

**Cách kiểm tra:**
1. **Giả lập mất mạng:**
   - Cách dễ nhất: Đổi `AWS_ENDPOINT` trong file `.env` thành một địa chỉ sai (ví dụ: `wrong-endpoint.ats.iot.com`).
   - Nhấn **Restart Services** (chỉ lần này cần restart để đổi endpoint).
2. **Gửi dữ liệu:**
   - Ngồi lên gối để tạo dữ liệu.
   - Quan sát tab **Config & Control**, phần **OFFLINE CLOUD QUEUE**.
   - Bạn sẽ thấy số lượng `pending events` tăng dần (ví dụ: `● 3 events pending`).
3. **Kiểm tra DB:**
   - Mở bảng `pending_cloud_queue` trong `data/fog_local.db`.
   - Bạn sẽ thấy các bản tin JSON đang nằm chờ tại đây.
4. **Khôi phục mạng:**
   - Sửa lại `AWS_ENDPOINT` đúng trong `.env`.
   - Nhấn **Restart Services**.
5. **Xác nhận gửi bù:**
   - Chờ khoảng 60 giây (vòng lặp retry).
   - Log sẽ báo: `[CloudRetry] ✅ Sent X queued events to cloud`.
   - Số lượng `pending events` trên giao diện sẽ quay về **0**.

---

## 4. Kiểm tra Tự động dọn dẹp (Retention)

1. Trên Launcher, chỉnh **Auto-delete after** thành `1` ngày -> Nhấn **Save**.
2. Kiểm tra bảng `fog_config` trong DB, key `cloud_queue_retention_days` phải mang giá trị `1`.
3. Hệ thống sẽ tự động xoá các bản tin cũ hơn 1 ngày mỗi khi vòng lặp retry chạy qua.
