# 🟡 Hướng dẫn cài đặt và chạy Smart Cushion Node trên Windows (Không Dùng Docker)

Tài liệu này hướng dẫn chi tiết cách cài đặt và chạy **Smart Cushion Fog Node** trực tiếp trên máy tính Windows mà **không cần cài đặt Docker**. 

Giải pháp này hoàn toàn tự động thông qua file `start_windows.bat`. Bạn chỉ cần làm theo các bước cực kỳ đơn giản dưới đây.

---

## 🛠 1. Yêu cầu Hệ thống (Prerequisites)

Trước khi bắt đầu, máy tính Windows của bạn cần có:
- **Python (Phiên bản 3.9 trở lên):** 
  - Nếu bạn chưa có máy, hãy tải xuống và cài đặt từ trang chủ gốc: https://www.python.org/downloads/
  - **LƯU Ý QUAN TRỌNG KHI CÀI PYTHON:** Ở màn hình cài đặt đầu tiên của Python, hãy CHẮC CHẮN tick vào ô vuông **`"Add Python 3.X to PATH"`** trước khi ấn nút Install.

---

## 🚀 2. Các Bước Cài Đặt và Chạy Ứng Dụng (Chỉ với 1 Click)

### Bước 1: Khởi chạy file tự động
1. Tải toàn bộ thư mục mã nguồn phần mềm này về máy Windows của bạn (Tải file ZIP và giải nén, hoặc dùng `git clone`).
2. Mở thư mục dự án vừa giải nén.
3. Tìm và click đúp chuột (Double-click) vào file có tên là **`start_windows.bat`**.

### Bước 2: Chờ quá trình cài đặt tự động diễn ra
Khi chạy file `start_windows.bat`, một cửa sổ màn hình đen (Terminal) sẽ hiện ra. Bạn không cần gõ lệnh gì cả, file này sẽ tự động:
- Kiểm tra xem máy bạn đã cài Python chưa.
- Tự động tạo thư mục **môi trường ảo (virtual environment - `venv`)** để không làm rác máy tính của bạn.
- Tự động tạo file **`.env`** (chứa cấu hình bảo mật) từ file mẫu `.env.example`.
- Tự động tải và cài đặt toàn bộ các thư viện cần thiết.
- Tự động khởi chạy luôn ứng dụng Launcher của Smart Cushion Fog Node khi cài xong.

> **Nếu Launcher báo lỗi phần MQTT khi chạy lần đầu:** Đừng lo lắng! Lý do là vì tài khoản bảo mật và địa chỉ IP kết nối của bạn trong file `.env` chưa đúng. Hãy làm tiếp Bước 3.

---

## ⚙️ 3. Cấu hình Tham số Hệ thống (File `.env`)

Lần đầu chạy file `start_windows.bat`, hệ thống đã tự động sao chép cho bạn một file tên là **`.env`** từ `.env.example`. File này chứa các tham số chạy cấu hình cho máy chủ. 

Hãy tắt ứng dụng đang mở, làm theo các bước sau để cấu hình lại cho đúng trước khi chạy lại:

1. Mở file **`.env`** bằng bất kỳ trình soạn thảo văn bản nào (ví dụ: Notepad, VS Code).
2. Tìm và chỉnh sửa các thông tin sau (Thay `CHANGE_ME` bằng thông tin của bạn):

**Các thông số quan trọng cần lưu ý kết nối MQTT:**
- **`MQTT_HOST`**: Nếu bạn dùng Broker chung công ty/phòng Lab, hãy điền IP của Broker đó (VD: `192.168.1.100`). Bạn dùng MQTT qua ngrok thì điền URL tcp tương đương chẳng hạn `0.tcp.ap.ngrok.io`.
- **`MQTT_PORT`**: Mặc định là `1883` (nếu dùng Ngrok hoặc Port khác vui lòng chỉnh tại đây).
- **`MQTT_USERNAME`** và **`MQTT_PASSWORD`**: Điền tài khoản đăng nhập để có quyền kết nối vào MQTT của phần cứng ESP32.

**Cấp quyền cho Ứng dụng Web / App Mobile kết nối (WebSocket Auth):**
- **`WS_AUTH_TOKEN`**: Khai báo mật khẩu cho Website theo dõi áp suất (Có thể tạo 1 chuỗi dài ngẫu nhiên, VD: `MySecretToken123456`). Khi mở giao diện Dashboard web, trình duyệt sẽ yêu cầu chuỗi token này để xem thông tin trực tiếp.

3. **Lưu file `.env` lại**.

---

## 🔄 4. Các Lần Chạy Tiếp Theo

Sau khi cài đặt thành công lần đầu tiên và chỉnh sửa xong file `.env`, từ nay về sau:
- Mỗi khi bật máy mở phần mềm, bạn **chỉ cần double-click vào `start_windows.bat`**. 
- Hệ thống sẽ phát hiện đã cài đặt rồi, bỏ qua các bước cài đặt và lập tức mở app giao diện Launcher của bạn lên ngay.

--- 

## ❓ Xử lý sự cố (Troubleshooting)

- **Lỗi `Python is not installed or not in the system PATH`:** Lỗi do bạn chưa cài Python, hoặc lúc cài đã quên tick vào ô `Add Python to PATH`. Hãy gỡ phần mềm Python cũ, tiến hành cài lại và nhớ tick vào ô trống đó.
- **Lỗi thiếu file `requirements.txt`:** Chắc chắn bạn đã giải nén đầy đủ thư mục mà không làm thất lạc file.
- **Có cần cài thêm thư viện MQTT ngoài không?**: File `.bat` đã cài sẵn module `paho-mqtt` vào venv của bạn! Bạn chỉ cần cấu hình `.env` cho khớp với Broker (Phần cứng ESP32 gửi thông số tới đâu thì app này nghe tại đó).
