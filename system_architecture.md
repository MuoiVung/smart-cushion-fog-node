# ErgoVita - Hệ thống Giám sát và Cảnh báo Tư thế ngồi thông minh

Tài liệu đặc tả toàn bộ luồng dữ liệu (Data Interfaces) và danh mục tư thế chuẩn AI cho dự án ErgoVita.

---

## 1. Danh mục Phân loại Nhãn AI (AI Label Classification)

Mô hình AI nhận diện **11 nhãn** từ dữ liệu 9 cảm biến FSR (3×3 matrix). Nhiệt độ **không** được dùng để nhận diện có người ngồi — AI xử lý toàn bộ qua áp lực. Nhiệt độ chỉ được báo cáo lên App.

### 1.1. Nhãn trạng thái bề mặt đệm (2 nhãn)

| STT | Nhãn | Tên | Đặc điểm |
| :--- | :--- | :--- | :--- |
| 0 | **EMPTY** | Empty Cushion | Không có người hoặc vật nào đặt lên đệm. Áp lực toàn bộ rất thấp. |
| 1 | **OBJECT** | Object Detected | Có vật thể đặt lên đệm nhưng **không phải người ngồi** (ví dụ: túi xách, laptop). Áp lực bất thường, không phân bổ như người ngồi. |

### 1.2. Nhãn tư thế ngồi (9 nhãn)

Được kích hoạt khi AI xác định có người đang ngồi trên đệm.

| STT | Nhãn | Tên tư thế | Đặc điểm chính |
| :--- | :--- | :--- | :--- |
| 2 | **NUP** | Natural Upright Posture | Cột sống thẳng tự nhiên, trọng lượng cân bằng. |
| 3 | **LF** | Lean Forward | Thân người đổ về phía trước. |
| 4 | **LB** | Lean Backward | Thân người ngả ra phía sau. |
| 5 | **LFSR** | Lean Forward-Support Right | Đổ người, tựa đầu hoặc khuỷu tay vào tay phải trên bàn. |
| 6 | **LFSL** | Lean Forward-Support Left | Đổ người, tựa khuỷu tay trái trên bàn. |
| 7 | **CRL** | Cross-Right Legged | Vắt chéo chân (Cổ chân phải đặt trên gối trái). |
| 8 | **CLL** | Cross-Left Legged | Vắt chéo chân (Cổ chân trái đặt trên gối phải). |
| 9 | **CRLL** | Cross-Right Legged-Legged | Vắt chéo chân sâu (Đùi phải vắt qua đùi trái). |
| 10 | **CLLL** | Cross-Left Legged-Legged | Vắt chéo chân sâu (Đùi trái vắt qua đùi phải). |

### 1.3. Quy tắc nghiệp vụ từ nhãn AI

| Nhãn AI | occupancy_state | Cảnh báo rung |
| :--- | :--- | :--- |
| `EMPTY` | `empty` | Không |
| `OBJECT` | `uncertain` | Không |
| `NUP` | `occupied` | Không (tư thế đúng) |
| `LF`, `LB`, `LFSR`, `LFSL`, `CRL`, `CLL`, `CRLL`, `CLLL` | `occupied` | Có (sau N lần liên tiếp) |

---

## 2. Các luồng dữ liệu chi tiết

### 2.1. Edge to Fog (Dữ liệu cảm biến thô)

#### 1. Thông số kỹ thuật (Technical Specifications)

| Thông số | Giá trị |
| :--- | :--- |
| Giao thức truyền tải | MQTT |
| Topic | `cushion/raw` |
| Tần suất gửi | 0.5 giây / 1 lần |
| Cơ chế truyền | Không đồng bộ (Asynchronous) |

#### 2. Cấu trúc dữ liệu mẫu (JSON Templates)

**ESP32_1 (Cụm Trái + Nhiệt độ):**
```json
{
  "device_id": "esp32-1",
  "timestamp": 123.45,
  "sensors": {
    "fsr_front_left": 2800,
    "fsr_front_right": 3050,
    "fsr_back_left": 2590,
    "fsr_back_right": 2400,
    "temperature": 36.5
  },
  "actuator": {
    "vibration_status": false
  }
}
```

**ESP32_2 (Trục giữa + Cụm Phải):**
```json
{
  "device_id": "esp32-2",
  "timestamp": 123.50,
  "sensors": {
    "fsr_front_mid": 3000,
    "fsr_mid_mid": 2900,
    "fsr_back_mid": 2500,
    "fsr_mid_left": 2800,
    "fsr_mid_right": 3100
  }
}
```

#### 3. Bảng giải thích các thông số (Data Dictionary)

| Nhóm | Field | Ý nghĩa | Input (Nguồn) | Giá trị & Khoảng (Range) | Đơn vị |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Metadata | `device_id` | Định danh của mạch ESP32 gửi tin | Hardcoded | "esp32-1" hoặc "esp32-2" | - |
| Metadata | `timestamp` | Thời gian kể từ khi mạch khởi động | millis() / 1000 | 0 to 4,294,967 | s |
| FSR Sensors | `fsr_front_left` | Áp lực góc trên bên trái | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_front_right` | Áp lực góc trên bên phải | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_back_left` | Áp lực góc dưới bên trái | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_back_right` | Áp lực góc dưới bên phải | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_front_mid` | Áp lực giữa hàng trên | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_mid_mid` | Áp lực chính giữa đệm | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_back_mid` | Áp lực giữa hàng dưới | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_mid_left` | Áp lực giữa cạnh trái | Analog Pin | 0 to 4095 | ADC |
| FSR Sensors | `fsr_mid_right` | Áp lực giữa cạnh phải | Analog Pin | 0 to 4095 | ADC |
| Environmental | `temperature` | Nhiệt độ người ngồi/đệm | Cảm biến nhiệt | 0 to 100.0 | °C |
| Actuator | `vibration_status` | Trạng thái động cơ rung | Digital Pin | true (bật) hoặc false (tắt) | boolean |

---

### 2.2. Fog to Edge (Lệnh điều khiển rung)

#### 1. Thông số kỹ thuật (Technical Specifications)

| Thông số | Giá trị |
| :--- | :--- |
| Giao thức truyền tải | MQTT |
| Topic | `cushion/command` |
| Cơ chế | Fog Node gửi lệnh điều khiển dựa trên logic cảnh báo tư thế |
| Điều khiển cường độ | Sử dụng PWM (0 - 255) để thay đổi lực rung của mô tơ |

#### 2. Cấu trúc dữ liệu mẫu (JSON Templates)

**Lệnh bật rung (Vibrate ON):**
```json
{
  "device_id": "esp32-1",
  "command": "vibrate",
  "active": true,
  "pattern": "short_triple",
  "intensity": 255
}
```

**Lệnh tắt rung (Vibrate OFF):**
```json
{
  "device_id": "esp32-1",
  "command": "vibrate",
  "active": false
}
```

#### 3. Bảng giải thích các thông số (Data Dictionary)

| Field | Ý nghĩa | Giải thích kỹ thuật | Giá trị gợi ý |
| :--- | :--- | :--- | :--- |
| `device_id` | ID mạch nhận lệnh | Định danh cụ thể mạch ESP32 | "esp32-1" |
| `command` | Loại lệnh | Xác định hành động điều khiển | "vibrate" |
| `active` | Trạng thái | Kích hoạt hoặc ngừng rung | true / false |
| `pattern` | Kiểu nhịp rung | Các chế độ rung lập trình sẵn | short_triple, long_single |
| `intensity` | Cường độ rung | Giá trị PWM điều khiển mô tơ | 0 - 255 (Thường > 150) |

---

### 2.3. Fog to Local App (Real-time Stream)

#### 1. Thông số kỹ thuật (Technical Specifications)

| Thông số | Giá trị |
| :--- | :--- |
| Giao thức | WebSocket (LAN) |
| Tần suất | 0.5 giây / 1 lần |
| Tính đồng bộ | Tên trường khớp 100% với Interface Fog→Cloud |

#### 2. Cấu trúc dữ liệu mẫu (JSON Template)

```json
{
  "record_type": "realtime_update",
  "device_id": "cushion-01",
  "session_id": "sess-20260423-0001",
  "session_start_time_iso": "2026-04-23T10:15:00Z",
  "occupancy_state": "occupied",
  "posture": "LFSR",
  "temperature": 36.5,
  "alert_active": true,
  "alert_status": "WARNING",
  "alert_count": 5,
  "session_duration_sec": 411,
  "sensors_heatmap_pct": [85, 90, 40, 20, 15, 10, 5, 0, 0]
}
```

#### 3. Bảng giải thích các thông số (Data Dictionary)

| Field | Ý nghĩa | Nguồn / Cách tính | Giá trị & Khoảng |
| :--- | :--- | :--- | :--- |
| `record_type` | Loại bản ghi | Fog Logic | "realtime_update" |
| `device_id` | Định danh của thiết bị đệm | Cấu hình Fog | "cushion-01" |
| `session_id` | ID của phiên ngồi hiện tại | Fog Logic | "sess-YYYYMMDD-XXXX" |
| `session_start_time_iso` | Thời điểm bắt đầu ngồi | Fog ghi nhận | Định dạng ISO 8601 (UTC) |
| `occupancy_state` | Trạng thái có người ngồi | Model AI | occupied, empty, uncertain |
| `posture` | Nhãn tư thế hiện tại | Model AI | NUP, LF, LB, LFSR... |
| `temperature` | Nhiệt độ tại đệm | Cảm biến Edge | 0 - 100.0 (°C) |
| `alert_active` | Trạng thái động cơ rung thực tế | Fog Feedback | true (đang rung), false (tắt) |
| `alert_status` | Trạng thái logic cảnh báo | Fog Logic | IDLE, WARNING, COOLDOWN |
| `alert_count` | Tổng số lần đã nhắc nhở | Fog đếm | Số nguyên (lần) |
| `session_duration_sec` | Tổng thời gian đã ngồi | Hiện tại - Start | Số giây (s) |
| `sensors_heatmap_pct` | 9 điểm áp lực (%) | (Giá trị / 4095) × 100 | Mảng 9 số (0 - 100) |

---

### 2.4. Fog to Cloud (AWS IoT Core)

#### 1. Thông số kỹ thuật (Technical Specifications)

| Thông số | Giá trị |
| :--- | :--- |
| Giao thức truyền tải | MQTT (AWS IoT Core) |
| Thời gian chuẩn | ISO 8601 (UTC) |
| Topic — Sự kiện | `cushion/{device_id}/event` |
| Topic — Định kỳ | `cushion/{device_id}/telemetry` |
| Topic — Tổng kết | `cushion/{device_id}/summary` |
| Ví dụ | `cushion/cushion-01/event` |

#### 2. Cấu trúc dữ liệu mẫu (JSON Templates)

**1. Event Record** — Gửi đến Topic: `cushion/{device_id}/event`
```json
{
  "record_type": "event_record",
  "record_id": "evt-20260421-00123",
  "device_id": "cushion-01",
  "session_id": "sess-20260421-0001",
  "fog_timestamp_iso": "2026-04-21T09:10:15Z",
  "event_type": "alert_triggered",
  "occupancy_state": "occupied",
  "posture": "LFSR"
}
```

**2. Telemetry Record** — Gửi đến Topic: `cushion/{device_id}/telemetry`
```json
{
  "record_type": "telemetry_record",
  "record_id": "tel-20260421-00001",
  "device_id": "cushion-01",
  "session_id": "sess-20260421-0001",
  "fog_timestamp_iso": "2026-04-21T09:15:00Z",
  "occupancy_state": "occupied",
  "posture": "NUP",
  "alert_active": false
}
```

**3. Summary Record** — Gửi đến Topic: `cushion/{device_id}/summary`
```json
{
  "record_type": "summary_record",
  "record_id": "sum-20260421-00008",
  "device_id": "cushion-01",
  "session_id": "sess-20260421-0001",
  "fog_timestamp_iso": "2026-04-21T09:55:01Z",
  "start_time": "2026-04-21T09:10:00Z",
  "end_time": "2026-04-21T09:55:00Z",
  "total_sitting_duration_sec": 2700,
  "poor_posture_duration_sec": 720,
  "alert_count": 2,
  "posture_duration_breakdown": {
    "nup_duration_sec": 1620,
    "lf_duration_sec": 300,
    "lb_duration_sec": 60,
    "lfsr_duration_sec": 300,
    "lfsl_duration_sec": 120,
    "crl_duration_sec": 100,
    "cll_duration_sec": 100,
    "crll_duration_sec": 50,
    "clll_duration_sec": 50
  }
}
```

#### 3. Bảng giải thích các thông số (Data Dictionary)

**Bảng 3a — Fog to Cloud (Event / Telemetry / Summary)**

| Field | Ý nghĩa | Input (Nguồn) | Giá trị & Khoảng (Range) | Đơn vị |
| :--- | :--- | :--- | :--- | :--- |
| `record_type` | Phân loại loại bản ghi gửi lên Cloud | Logic từ Fog | event_record, telemetry_record, summary_record | - |
| `record_id` | Mã định danh duy nhất cho mỗi bản ghi | Fog tự tạo | Chuỗi ký tự (e.g., "evt-20260421-001") | - |
| `device_id` | Mã định danh của thiết bị đệm | Cấu hình Fog | "cushion-01" | - |
| `session_id` | Mã định danh của phiên ngồi hiện tại | Logic từ Fog | Chuỗi (e.g., "sess-20260421-0001") | - |
| `fog_timestamp_iso` | Thời điểm Fog đóng gói bản tin | Giờ hệ thống Fog | Định dạng ISO 8601 (UTC) | UTC |
| `start_time` | Thời điểm bắt đầu phiên ngồi | Fog ghi nhận | Định dạng ISO 8601 (UTC) | UTC |
| `end_time` | Thời điểm kết thúc phiên ngồi | Fog ghi nhận | Định dạng ISO 8601 (UTC) | UTC |
| `event_type` | Loại sự kiện quan trọng xảy ra | Logic từ Fog | alert_triggered, session_started, session_ended | - |
| `occupancy_state` | Trạng thái có người ngồi hay không | Mô hình AI | occupied, empty, uncertain | - |
| `posture` | Nhãn tư thế nhận diện được | Mô hình AI | NUP, LF, LB, LFSR... (Xem Mục 1) | - |
| `total_sitting_duration_sec` | Tổng thời gian ngồi của cả phiên | Fog tính toán | 0 to 86400 | giây (s) |
| `poor_posture_duration_sec` | Tổng thời gian ngồi sai tư thế | Fog tính toán | 0 to 86400 | giây (s) |
| `alert_count` | Tổng số lần đã rung cảnh báo | Fog đếm | 0 to 500 | lần |
| `alert_active` | Trạng thái motor rung lúc gửi tin | Fog quản lý | true (đang rung), false (tắt) | boolean |
| `posture_duration_breakdown` | Chi tiết thời gian của từng tư thế | Fog tổng hợp | Object chứa các nhãn và số giây | giây (s) |

**Bảng 3b — Cloud Aggregated (Daily / Session History)**

| Field | Ý nghĩa | Nguồn / Cách tính | Giá trị & Khoảng |
| :--- | :--- | :--- | :--- |
| `date` | Nhãn ngày hiển thị | Cloud ghi nhận | YYYY-MM-DD |
| `total_sitting_duration_sec` | Tổng thời gian ngồi trong ngày | Sum(duration_sec) | Số nguyên (giây) |
| `poor_posture_duration_sec` | Tổng thời gian ngồi sai trong ngày | Sum(poor_sec) | Số nguyên (giây) |
| `alert_count` | Tổng số lần đã nhắc nhở | Tổng hợp từ các Session | Số nguyên (lần) |
| `posture_distribution_pct` | Tỷ lệ phần trăm 9 tư thế | (Thời gian tư thế / Tổng) × 100 | Object chứa 9 mã tư thế |
| `session_id` | ID của phiên ngồi cụ thể | Lưu trữ Cloud | "sess-YYYYMMDD-XXXX" |
| `start_time_iso` | Thời điểm bắt đầu phiên | ISO 8601 (UTC) | UTC String |
| `end_time_iso` | Thời điểm kết thúc phiên | ISO 8601 (UTC) | UTC String |
| `duration_sec` | Thời lượng của một phiên cụ thể | End - Start | Số nguyên (giây) |

---

### 2.5. Cloud to App (On-demand API)

* **Giao thức:** HTTPS GET | **Đơn vị:** Giây (`_sec`).

**Dashboard Summary:**
```json
{
  "device_id": "cushion-01",
  "date": "2026-04-21",
  "total_sitting_duration_sec": 7920,
  "poor_posture_duration_sec": 2460,
  "alert_count": 6,
  "posture_distribution_pct": {
    "nup_pct": 52, "lf_pct": 10, "lb_pct": 8, "lfsr_pct": 12,
    "lfsl_pct": 5, "crl_pct": 4, "cll_pct": 4, "crll_pct": 3, "clll_pct": 2
  }
}
```

#### Data Dictionary

| Field | Ý nghĩa | Kiểu dữ liệu | Ghi chú |
| :--- | :--- | :--- | :--- |
| `date` | Ngày của dữ liệu | String | YYYY-MM-DD |
| `total_sitting_duration_sec` | Tổng ngồi trong ngày | Integer | Đơn vị: Giây (s) |
| `poor_posture_duration_sec` | Tổng ngồi sai ngày | Integer | Đơn vị: Giây (s) |
| `alert_count` | Tổng nhắc nhở ngày | Integer | Số lần |
| `posture_distribution_pct` | Tỷ lệ 9 tư thế | Object | { "nup_pct": 50, "lf_pct": 10, ... } |
| `start_time_iso` | Bắt đầu (History) | String | ISO 8601 (UTC) |
| `duration_sec` | Độ dài phiên (History) | Integer | Đơn vị: Giây (s) |
