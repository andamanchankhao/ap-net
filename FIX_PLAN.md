# AP-NET — แผนการแก้ไข (Fix Plan)

สำรวจครั้งแรก 2026-08-26 · **แก้ไขเฟส 1–3 + YOLO จริง เมื่อ 2026-08-26** · **แก้กลุ่ม C (ความปลอดภัย) เมื่อ 2026-08-26**

## สถานะปัจจุบัน

| กลุ่ม | หัวข้อ | สถานะ |
| :--- | :--- | :--- |
| A | Path ตายตัว — ระบบรันไม่ได้ | ✅ แก้แล้วทั้ง 3 ข้อ |
| B | ตรรกะผิด / ข้อมูลเสียหาย | ✅ แก้แล้วทั้ง 7 ข้อ (เจอเพิ่ม 1 ข้อระหว่างแก้) |
| C | ความปลอดภัยของ Dashboard | ✅ แก้แล้วทั้ง 3 ข้อ (เจอเพิ่ม 2 ข้อระหว่างแก้) |
| D | โค้ดซ้ำซ้อน / โค้ดตาย | ✅ แก้แล้ว 5 ข้อ |
| E | เอกสารกับโค้ดไม่ตรงกัน | 🔶 แก้บางส่วน (E2 ✅, E3 บางส่วน) |

**เหลือค้าง 2 ข้อ**, ทั้งคู่เป็นงานเอกสารล้วน ไม่กระทบการทำงานของระบบ:
E1 (Mermaid diagram พัง), E4 (mock images ไม่ใช่ภาพจริง)

**บั๊กที่เจอเพิ่มระหว่างแก้กลุ่ม C** (ไม่ได้อยู่ในรายการสำรวจเดิม):
- **onclick-breakout XSS** ในปุ่ม popup บนแผนที่ — รุนแรงกว่าที่บันทึกไว้เดิม เพราะ `escapeHtml()`
  เฉย ๆ แก้ไม่ได้จริงสำหรับ inline event handler (ดูรายละเอียดใน C2)
- **Path traversal ใน `/simulate-alert`** — `node_id` จาก request body ไหลตรงเข้าชื่อไฟล์
  โดยไม่กรอง ทำให้เขียนไฟล์หลุดออกจาก `received_images/` ได้ (ดูรายละเอียดใน C3)

### ไฟล์ที่เพิ่มเข้ามา

| ไฟล์ | หน้าที่ |
| :--- | :--- |
| `paths.py` | แหล่งเดียวของทุก path — คำนวณจาก `__file__` ย้ายโฟลเดอร์ได้อิสระ |
| `lora_protocol.py` | โปรโตคอล LoRa ชุดเดียว (เดิมกระจาย 4 ไฟล์) |
| `base_station/receiver_runtime.py` | loop รับข้อมูลที่ใช้ร่วมกัน |
| `base_station/incident_store.py` | ประวัติ incident แบบ append-only ฝั่ง server |
| `edge_node/detector.py` | YOLO จริง (ONNX / ultralytics / TFLite) + mock ที่เตือนตัวเอง |
| `edge_node/camera.py` | picamera2 / rpicam / OpenCV / file |
| `edge_node/pir_sensor.py` | PIR HC-SR501 + MOSFET power latch (gpiozero / lgpio) |
| `edge_node/field_node.py` | ตัวรันจริงบน Pi 5: PIR → กล้อง → YOLO → ส่ง |
| `SETUP_PI.md` | คู่มือติดตั้งบน Raspberry Pi 5 |
| `requirements.txt` / `requirements-pi.txt` / `.gitignore` | |

---

## บันทึกเดิมจากการสำรวจ

สรุป: พบข้อผิดพลาด **20 รายการ** แบ่งเป็น 5 กลุ่ม เรียงตามลำดับที่ควรลงมือ
กลุ่ม A ทำให้โปรแกรมรันไม่ได้เลยในตอนนี้ จึงต้องแก้ก่อนเป็นอันดับแรก

| กลุ่ม | หัวข้อ | จำนวน | ความรุนแรง |
| :--- | :--- | :--- | :--- |
| A | Path ตายตัว — ระบบรันไม่ได้ | 3 | 🔴 Blocker |
| B | ตรรกะผิด / ข้อมูลเสียหาย | 6 | 🔴 High |
| C | ความปลอดภัยของ Dashboard | 3 | 🟠 Medium |
| D | โค้ดซ้ำซ้อน / โค้ดตาย | 5 | 🟡 Low |
| E | เอกสารกับโค้ดไม่ตรงกัน | 3 | 🟡 Low |

---

## กลุ่ม A — Path ตายตัว (ต้องแก้ก่อน ไม่งั้นรันไม่ได้เลย)

### ✅ A1. ทุกสคริปต์ชี้ไปที่โฟลเดอร์ที่ไม่มีอยู่จริง 🔴

โปรเจกต์ย้ายมาอยู่บน Google Drive แล้ว แต่โค้ดยัง hardcode
`/Users/andamanchankhao/Workspace/AntiPoaching` ซึ่ง **ไม่มีอยู่แล้ว**

ตำแหน่งที่ต้องแก้ (9 จุด ใน 6 ไฟล์):

| ไฟล์ | บรรทัด |
| :--- | :--- |
| `run_demo.py` | 8 |
| `run_demo_queue.py` | 13 |
| `run_demo_sandboxed.py` | 9 |
| `edge_node/edge_node.py` | 9, 10, 11 |
| `edge_node/lora_sender_emulator.py` | 9 |
| `edge_node/capture_webcam.py` | 93, 180, 225 |

ยืนยันแล้วด้วยการรันจริง:

```
$ python3 run_demo_queue.py
FileNotFoundError: '/Users/andamanchankhao/Workspace/AntiPoaching/mock_images/human_intruder_01.jpg'

$ python3 edge_node/edge_node.py --image mock_images/human_intruder_01.jpg
FileNotFoundError: '/Users/andamanchankhao/Workspace/AntiPoaching/edge_node/compressed_preview.webp'
```

**แนวทางแก้:** ใช้รูปแบบเดียวกับที่ `base_station/dashboard_server.py:13` ทำอยู่แล้ว
(ซึ่งเป็นเหตุผลว่าทำไมไฟล์นั้นยังรันได้)

```python
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))          # ใน run_demo*.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ใน edge_node/*.py
```

เสนอให้สร้าง `paths.py` ที่ root แล้วให้ทุกไฟล์ import จากที่เดียว จะได้ไม่พลาดซ้ำอีกตอนย้ายโฟลเดอร์

### ✅ A2. `edge_node.py:88-99` เขียนไฟล์ก่อนสร้างโฟลเดอร์ 🔴

`process_image()` เขียน preview ที่บรรทัด 91 แต่ไป `os.makedirs()` ที่บรรทัด 99 — สายเกินไป
และ preview ยังใช้ค่าคงที่ `DEFAULT_PREVIEW_PATH` โดยไม่ฟังพารามิเตอร์ `--output-payload` เลย

**แนวทางแก้:** ย้าย `makedirs` ขึ้นก่อนการเขียนทุกครั้ง และให้ preview path
อนุมานจาก `output_payload_path` หรือเพิ่ม flag `--output-preview`

### ✅ A3. `capture_webcam.py` เรียก subprocess ด้วย relative path 🟠

บรรทัด 180 และ 225 เรียก `subprocess.run(["python3", "edge_node/edge_node.py", ...])`
ซึ่งจะพังทันทีถ้า CWD ไม่ใช่ root ของโปรเจกต์

**แนวทางแก้:** ใช้ `sys.executable` แทน `"python3"` และสร้าง path แบบ absolute จาก `__file__`

---

## กลุ่ม B — ตรรกะผิดและข้อมูลเสียหาย

### ✅ B1. `dashboard_server.py:630-645` ลบภาพทั้งหมดทุกครั้งที่สตาร์ท 🔴

`main()` วนลบทุกไฟล์ใน `received_images/` แบบไม่มีเงื่อนไข ไม่มีคำเตือน ไม่มีแฟล็กปิด

ผลกระทบจริง: ตอนทดสอบระบบวันนี้ ภาพ demo 16 ไฟล์จากวันที่ 26 มิ.ย. หายไปทั้งหมด
เหลือแค่ `seed_human.png` ที่สร้างใหม่ และเนื่องจากประวัติ incident เก็บใน localStorage
ของเบราว์เซอร์ (ดู B2) ประวัติเก่าจึงยังอยู่ในตาราง แต่ภาพประกอบขึ้น 404 ทั้งหมด

**แนวทางแก้:** เปลี่ยนเป็น opt-in — `--reset` หรือ `--clean` ถ้าไม่ใส่ให้เก็บของเดิมไว้
หรืออย่างน้อยย้ายไป `received_images/archive/<timestamp>/` แทนการลบทิ้ง

### ✅ B2. ประวัติ incident อยู่แค่ในเบราว์เซอร์ ไม่มีฝั่ง server 🔴

`app.js:890` เก็บทุกอย่างลง `localStorage["ap_incident_log_v2"]`
ฝั่ง server เก็บได้แค่ **alert เดียว** ใน `received_images/alert_metadata.json`

ผลที่ตามมา:
- เปิดจากเครื่องอื่น / เบราว์เซอร์อื่น = ประวัติหายหมด
- ล้าง browser data = ประวัติหายถาวร
- ประวัติในเบราว์เซอร์อ้างถึงภาพที่ server ลบไปแล้ว (B1) → ภาพเสียทั้งชุด
- ไม่มีทาง audit ย้อนหลังจากฝั่ง server ได้เลย ซึ่งขัดกับจุดประสงค์ของระบบ anti-poaching

**แนวทางแก้:** เพิ่ม `received_images/incidents.jsonl` (append-only) ฝั่ง server
พร้อม endpoint `GET /incidents` ให้ frontend ดึงตอนโหลด ใช้ localStorage เป็นแค่ cache

### ✅ B3. `POST /resolve-alert` แก้ alert ผิดตัวเสมอ 🔴

`dashboard_server.py:264-292` รับ request ที่ **ไม่มี body** แล้วไปแก้
`alert_metadata.json` ซึ่งเป็น alert ล่าสุดเสมอ

`app.js:725` ก็ส่งมาแบบไม่มี body เช่นกัน ดังนั้นถ้าผู้ใช้กด Resolve
บน incident เก่าในตาราง ระบบจะไปทำเครื่องหมาย resolved ให้ incident **ใหม่ล่าสุด** แทน

**แนวทางแก้:** ต้องพึ่ง B2 ก่อน แล้วเปลี่ยนเป็น `POST /resolve-alert {"logId": "..."}`
ให้ server ค้นหาและอัปเดตรายการที่ถูกต้อง

### ✅ B4. Exit code `1` หมายถึงทั้ง "ไม่พบภัยคุกคาม" และ "โปรแกรมพัง" 🔴

`edge_node.py:111` (ไฟล์ภาพไม่มี) กับ `edge_node.py:129` (ไม่พบมนุษย์) คืนค่า `1` เหมือนกัน
และ crash ที่ไม่ได้ดักก็คืน `1` ด้วย

`run_demo.sh:96-101` ตีความ `1` ว่า *"[SUCCESS] Edge node correctly classified threat as negative"*
→ ระบบพังแต่รายงานว่าเทสต์ผ่าน

**แนวทางแก้:** ใช้ 3 ค่า — `0` = พบมนุษย์ / `1` = ไม่พบภัยคุกคาม / `2` = ข้อผิดพลาด
แล้วแก้เงื่อนไขใน `run_demo.sh` ให้ตรวจ `2` แยกต่างหาก

### ✅ B5. ตัวถอดรหัส payload ไม่ดักข้อผิดพลาด → thread ตายเงียบ 🟠

`dashboard_server.py:471` และ `lora_receiver_emulator.py:102`

```python
payload = data[8:8+p_len].decode('utf-8')
```

`p_len` มาจากแพ็กเก็ตโดยตรง ไม่มีการตรวจสอบ ถ้าไบต์เสียหาย `.decode()` จะโยน
`UnicodeDecodeError` ซึ่งอยู่นอก try/except — ใน `dashboard_server.py` มันจะฆ่า
LoRa receiver thread ทิ้งไปเงียบ ๆ ส่วน HTTP server ยังทำงานต่อ ผู้ใช้จะเห็นแค่ว่า
"dashboard ยังอยู่แต่ไม่มี alert เข้ามาอีกเลย" โดยไม่รู้สาเหตุ

**แนวทางแก้:** ตรวจ `if p_len > len(data) - 8: continue` ก่อน แล้วครอบ recv loop
ด้วย try/except กว้าง ๆ พร้อม log — ไม่ให้ thread หยุดจาก packet เสียหายเพียงตัวเดียว

### ✅ B6. ARQ loop ของ sender ไม่มีขีดจำกัดรอบ 🟠

`lora_sender_emulator.py:114` เป็น `while True:` ที่หลุดออกได้เฉพาะเมื่อได้ SUCCESS หรือ timeout
ถ้า loss rate สูง มันจะวนส่งซ้ำไม่มีที่สิ้นสุด — บนอุปกรณ์จริงคือแบตหมดและละเมิด duty cycle 10%
ที่ `Planning.md` §9 กำหนดไว้เอง

**แนวทางแก้:** จำกัด `MAX_ARQ_ROUNDS` (เช่น 5) แล้วยอมแพ้ ทิ้ง log ไว้
ถือเป็นการจำลอง duty-cycle guard ในตัว

### B7. ✅ ดิสก์ช้าทำให้ ARQ ล้มเหลว 🔴 *(เจอเพิ่มระหว่างแก้ ไม่ได้อยู่ในรายการเดิม)*

เจอตอนรัน `run_demo.py` หลังแก้กลุ่ม A: Test 1 ล้มเหลวทั้งที่ตรรกะถูกหมดแล้ว

สาเหตุ: receiver ประกอบภาพเสร็จ → เขียนไฟล์ลงดิสก์ → **แล้วค่อย** ส่ง SUCCESS กลับ
วันที่ทดสอบ Google Drive เกิด I/O stall 4.3 วินาที (ปกติ <1 ms) ทำให้ SUCCESS
ส่งไม่ทัน timeout 5 วินาทีของ sender

ผลบนอุปกรณ์จริง: กล้องดักถ่ายที่ใช้แบตจะส่งภาพซ้ำทั้งชุด ทั้งที่ base station
ถอดรหัสภาพสำเร็จเรียบร้อยแล้ว — เปลืองทั้งแบตและ airtime โดยเปล่าประโยชน์
Pi ที่เขียน SD การ์ดตอน garbage collect ก็เจออาการเดียวกันได้

**แก้แล้ว:** ย้ายการส่ง SUCCESS ขึ้นมา**ก่อน**การเขียนดิสก์
([base_station/receiver_runtime.py](base_station/receiver_runtime.py)) — ทุกอย่างที่ต้องใช้
ตัดสินใจตอบรับ (payload ครบและถอดรหัสผ่าน) รู้ครบแล้วตรงนั้น ดิสก์จึงไม่ต้องอยู่ใน
งบเวลาของโปรโตคอลอีก และเพิ่ม `--ack-timeout` (ค่าเริ่มต้น 10 วินาที) ให้ sender


---

## กลุ่ม C — ความปลอดภัยของ Dashboard ✅ แก้แล้วทั้ง 3 ข้อ

> บริบทเดิม: นี่เป็น demo บนเครื่องตัวเอง ความเสี่ยงจึงต่ำในตอนนั้น
> แต่ผู้ใช้มี Raspberry Pi 5 พร้อมใช้งานจริงแล้ว และ dashboard ต้องเปิดให้เข้าถึงจากเครื่องอื่น
> บน LAN ได้ (ดู SETUP_PI.md) จึงแก้ทั้ง 3 ข้อก่อนเริ่มใช้งานจริง

### ✅ C1. ไม่มีการยืนยันตัวตน + เปิดรับทุก interface 🟠

`dashboard_server.py:660` ผูกกับ `("", 8080)` = ทุก network interface
POST endpoint ทั้ง 3 ตัว (`/sensor-config`, `/resolve-alert`, `/simulate-alert`)
ไม่มี auth ใด ๆ และทุก response ตั้ง `Access-Control-Allow-Origin: *`
(บรรทัด 84, 124, 134, 147, 161, 171, 254, 280, 373)

ใครก็ตามที่อยู่ในวง Wi-Fi เดียวกันสามารถย้ายพิกัดกล้อง ปิด alert
หรือยิง alert ปลอมเข้าระบบได้

**แก้แล้ว:**
- `--host` ค่าเริ่มต้นเปลี่ยนเป็น `127.0.0.1` (จากเดิม `0.0.0.0`) — ปลอดภัยโดยไม่ต้องทำอะไรเพิ่ม
- เมื่อ bind กับ interface อื่นที่ไม่ใช่ loopback ระบบจะ**บังคับ HTTP Basic Auth**
  โดยอัตโนมัติ (`configure_auth()` + `_require_auth()` ใน `dashboard_server.py`)
  - ไม่ใส่ `--password` → สุ่มรหัสผ่านด้วย `secrets.token_urlsafe(12)` แล้วพิมพ์ที่ console ตอนสตาร์ท
  - ใส่ `--password <fixed>` เพื่อกำหนดรหัสผ่านคงที่ (สำหรับใช้งานจริงที่ต้องรอด restart)
  - `--no-auth` เปิดไว้สำหรับเครือข่ายทดสอบที่แยกออกมาต่างหากเท่านั้น
- เลือกใช้ **Basic Auth** แทน custom token เพราะเบราว์เซอร์จัดการ challenge/response เองทั้งหมด
  (prompt ครั้งเดียว แล้วแนบ credential ให้ทุก request ในหลังจากนั้นอัตโนมัติ — รวมถึง
  `<img>`, `fetch()`, และ `EventSource`) จึงไม่ต้องแก้ `app.js` เลยสักบรรทัด
- ทดสอบแล้วครบ: GET/POST ไม่มี credential → 401, credential ผิด → 401, ถูก → 200,
  รูปภาพและ SSE stream ก็ถูกป้องกันด้วย (ไม่ใช่แค่ endpoint POST) — payload คลื่นวิทยุ
  (`field_node.py` ผ่าน UDP พอร์ต 5005) ไม่เกี่ยวข้องกับ auth ชุดนี้เลย เพราะเป็นคนละโปรโตคอล

### ✅ C2. XSS ผ่าน `sensor_config.json` 🟠

`app.js:829, 1478, 1925` ประกอบ `innerHTML` จากค่าที่ไม่ผ่าน escape:

```javascript
<td style="color: #9ca3af;">${log.locationName}</td>
```

`locationName` มาจาก `sensor_config.json` ซึ่งเขียนได้ผ่าน `POST /sensor-config`
ตั้งชื่อกล้องเป็น `<img src=x onerror=...>` แล้ว JS จะทำงานในหน้า dashboard ของ ranger ทุกคน

**แก้แล้ว:** เพิ่มฟังก์ชัน `escapeHtml()` ใน `app.js` ครอบทุกจุดที่ประกอบ `innerHTML`
จากข้อมูลภายนอก — `renderCameraStatusList`, `addLogToTable`, incident-history table row,
`renderCameraTrapsModal`, และ `getPopupContent` (popup บนแผนที่)

พบจุดที่รุนแรงกว่าที่บันทึกไว้เดิมระหว่างแก้: ปุ่ม "VIEW ALERT DETAILS" ใน popup ใช้
`onclick="viewActiveThreat('${nodeId}')"` — เป็น **inline event handler ที่ใส่ค่าดิบเข้าไปในโค้ด JS**
ซึ่ง `escapeHtml()` เฉย ๆ **แก้ไม่ได้จริง**: เบราว์เซอร์จะ decode HTML entity ในค่าของ attribute
ก่อนส่งต่อให้ JS parser เสมอ ดังนั้น `&#39;` ที่ escape ไว้จะกลับเป็น `'` ก่อนถูกรันเป็นโค้ด
ทำให้ attacker ยังคง break out ของ string literal ได้อยู่ดีด้วย payload เช่น
`x'); alert(document.cookie); //` แก้โดยตัด inline `onclick` ทิ้งทั้งหมด เปลี่ยนเป็น
`data-node-id` + delegated `addEventListener` แทน (แพตเทิร์นเดียวกับปุ่มอื่นในไฟล์นี้อยู่แล้ว)
ซึ่งไม่มีการแปลงค่าเป็น HTML/JS source text เลยตลอดทาง — ปลอดภัยโดยไม่ขึ้นกับ encoding

เพิ่มการป้องกันแบบเดียวกันให้ `updateNodeOnlineState()` ด้วย: `document.querySelector`
ที่ประกอบ CSS selector จาก `nodeId` ตรง ๆ อาจโยน exception ถ้าเจอ node ID ที่มีอักขระพิเศษ
(ไม่ใช่ XSS แต่ทำให้สถานะกล้องตัวอื่นค้างได้) แก้ด้วย `CSS.escape()`

ยืนยันด้วยการทดสอบ string-level ทั้ง payload แบบ `<img onerror>` และแบบ quote-breakout
ว่าถูก neutralize ถูกต้องทั้งสองแบบ

### ✅ C3. `/simulate-alert` สร้างไฟล์ได้ไม่จำกัด 🟡

`dashboard_server.py:297-340` คัดลอก `seed_human.png` เป็นไฟล์ใหม่ทุกครั้งที่ถูกเรียก
ไม่มี rate limit ไม่มีการล้าง — เรียกถี่ ๆ ก็ถมดิสก์ได้

**แก้แล้ว:**
- Rate limit ที่ 1 ครั้ง/วินาที (`SIMULATE_MIN_INTERVAL_S`) คืน `429 Too Many Requests`
  พร้อม header `Retry-After` เมื่อเรียกถี่เกินไป — ทดสอบด้วยการยิง 3 request พร้อมกันจริง
  (ไม่ใช่ทีละตัว) ยืนยันว่ามีแค่ 1 ตัวผ่าน อีก 2 ตัวได้ 429
- เก็บภาพ `simulated_*.png` ไว้ไม่เกิน `SIMULATE_MAX_IMAGES` = 50 ไฟล์ล่าสุด
  (`prune_simulated_images()`) ลบไฟล์เก่าสุดทิ้งเมื่อเกิน ไม่แตะไฟล์ประเภทอื่น
  ทดสอบแบบแยกด้วยไฟล์ mock 60 ไฟล์ ยืนยันว่าเก็บ 50 ไฟล์ใหม่สุดถูกต้อง

พบช่องโหว่ที่ไม่ได้บันทึกไว้เดิมระหว่างแก้จุดนี้: `node_id` จาก request body ถูกใส่ตรง ๆ
ลงในชื่อไฟล์ (`f"simulated_{node_name...}_{stamp}.png"`) แล้วส่งต่อให้ `os.path.join()`
โดยไม่ตรวจสอบ — ส่ง `{"node_id": "../../../../tmp/pwned"}` เข้าไปจะทำให้ `shutil.copy()`
เขียนไฟล์หลุดออกจาก `received_images/` ไปที่ไหนก็ได้ที่โปรเซสเซิร์ฟเวอร์มีสิทธิ์เขียน
(รวมถึงเขียนทับ `dashboard_static/index.html` ได้ถ้าเดา relative path ถูก = persistent XSS)
แก้ด้วย `safe_filename_component()` กรองเหลือแค่ `[A-Za-z0-9_-]` ก่อนประกอบชื่อไฟล์
(ค่า `node_id` ใน metadata ที่แสดงผลจริงยังคงเป็นค่าดิบเดิม ปลอดภัยอยู่แล้วจาก C2)
ทดสอบยิง payload traversal จริงแล้วยืนยันว่าไฟล์ไม่หลุดออกนอก `received_images/`

---

## กลุ่ม D — โค้ดซ้ำซ้อนและโค้ดตาย

### ✅ D1. Receiver มีสองชุด ต้องแก้สองที่เสมอ 🟠

`base_station/lora_receiver_emulator.py:63-215` กับ
`base_station/dashboard_server.py:400-580` เป็นตรรกะเดียวกันเกือบทั้งหมด
(parse header, จำลอง loss, ประกอบภาพ, เขียน metadata, ส่ง BLOCKNACK)
แต่แตกต่างกันในรายละเอียดที่สำคัญ:

| | `lora_receiver_emulator.py` | `dashboard_server.py` |
| :--- | :--- | :--- |
| ชื่อไฟล์ผลลัพธ์ | `reassembled_image.webp` คงที่ | `reassembled_<timestamp>.webp` |
| จัดการ HEARTBEAT | ไม่มี | มี (บรรทัด 441) |
| `SO_REUSEADDR` | ไม่ตั้ง → restart เร็ว ๆ แล้ว bind ไม่ได้ | ตั้ง (บรรทัด 412) |

ทั้งคู่ผูกพอร์ต 5005 จึงรันพร้อมกันไม่ได้ — `server.log` บันทึกอาการนี้ไว้แล้ว
(`OSError: [Errno 48] Address already in use`)

**แนวทางแก้:** แยกตรรกะออกเป็น `base_station/lora_protocol.py` โมดูลเดียว
แล้วให้ทั้งสองไฟล์เรียกใช้ ตั้ง `SO_REUSEADDR` และตั้งชื่อไฟล์แบบ timestamp ให้เหมือนกัน

### ✅ D2. Demo runner มีสามตัวที่ทับซ้อนกัน 🟡

`run_demo.py` (subprocess) · `run_demo_sandboxed.py` (thread + UDP) · `run_demo_queue.py` (queue ล้วน)
ทั้งสามทำเทสต์ชุดเดียวกัน และทั้งสามพังจาก A1 เหมือนกัน
`run_demo_queue.py` ยัง copy-paste ตรรกะ edge/sender/receiver มาไว้ในไฟล์เดียวอีก ~200 บรรทัด

**แนวทางแก้:** เก็บ `run_demo_queue.py` ไว้ตัวเดียว (เร็วที่สุด ไม่ต้องพึ่ง socket permission)
แล้วให้มัน import จากโมดูลจริงแทนการ copy ส่วน `run_demo.py` และ `run_demo_sandboxed.py` ลบทิ้งได้

### ✅ D3. `edge_node.py:31-57` สาขา TFLite คำนวณแล้วทิ้ง 🟠

โค้ดโหลดโมเดล เตรียม tensor เรียก `invoke()` อ่าน `output_data` ที่บรรทัด 54 —
แล้วไม่เอาไปใช้เลย หลุดออกจาก try block ไปเข้าเงื่อนไข **เช็คชื่อไฟล์** ที่บรรทัด 58 แทน

แปลว่าถ้าใส่โมเดล YOLO จริงเข้าไป ระบบยังตัดสินจากชื่อไฟล์อยู่ดี
และไฟล์ `webcam_human.jpg` ที่ `capture_webcam.py` สร้างก็มีคำว่า "human" อยู่ในชื่อ
→ inference "ผ่าน" 100% เสมอ โดยไม่ได้ดูภาพเลย

ปัญหาเพิ่มเติมในสาขานี้: บรรทัด 48 normalize เป็น `float32/255.0`
ทั้งที่ `Planning.md` §6.2 ระบุว่าใช้โมเดล INT8 quantized และไม่มี `.convert("RGB")`
ก่อน `np.array()` ภาพ grayscale หรือ RGBA จะได้ shape ไม่ตรงกับ input tensor

**แนวทางแก้:** เลือกอย่างใดอย่างหนึ่ง —
(ก) parse YOLO output จริง (หา class `person`, เทียบ threshold 0.70 ตาม `Planning.md` §6.3) หรือ
(ข) ลบสาขานี้ทิ้งพร้อมกับ `--model` แล้วเขียนให้ชัดว่า inference เป็น mock
ถ้าเลือก (ก) ต้องแก้ normalization ให้ตรงกับ `input_details[0]['dtype']` และเติม `.convert("RGB")`

### ✅ D4. `random.seed(42)` ไปกวน RNG ของทั้งโปรเซส 🟡

`dashboard_server.py:429` และ `lora_receiver_emulator.py:61` เรียก `random.seed(42)`
ซึ่งเป็น global state ทำให้ค่า telemetry สุ่มใน `/simulate-alert`
(บรรทัด 336, 348-351: confidence, battery, rssi, snr) กลายเป็นชุดเดิมซ้ำทุกครั้งที่ restart

**แนวทางแก้:** ใช้ instance แยก `loss_rng = random.Random(42)` สำหรับจำลอง packet loss
ปล่อยให้ `random` ระดับ module เป็นค่าสุ่มจริงสำหรับ telemetry

### ✅ D5. โค้ดตายและ DOM id ที่ไม่มีจริง 🟡

- `dashboard_server.py:37-66` `directory_watcher()` ทั้งฟังก์ชันไม่ถูกเรียก
  (คอมเมนต์ปิดไว้ที่บรรทัด 652-653) — ลบทิ้งหรือเปิดใช้ให้ตัดสินใจ
- `app.js:930-936` `seedHistoricalLogs()` สร้าง `const logs = []` ว่าง ๆ แล้ววนลูปบนอาเรย์เปล่า
- `app.js` อ้าง element 8 ตัวที่ไม่มีใน `index.html`:
  `alerts-today-count`, `patrols-dispatched-count`, `avg-battery-value`, `avg-rssi-value`,
  `last-sync-time`, `threat-indicator`, `btn-settings-nav`, `camera-traps-modal`
  ทุกจุดมี null-guard จึงไม่ error แต่ `updateStatsBar()` (บรรทัด 947, 950)
  เขียนค่าลง element ที่ไม่มีอยู่ = **stat card ด้านบนไม่เคยอัปเดตเลย**
- `app.js:438, 656` fallback เป็น `"reassembled_preview.png"` ซึ่งเป็นชื่อไฟล์แบบเก่า
  ปัจจุบัน server สร้างเป็น `reassembled_preview_<timestamp>.png` → fallback พังเสมอ
- `app.js:920` มี `}console.log(...)` ติดกันบรรทัดเดียว — แค่จัดฟอร์แมต
- `__pycache__/run_demo_queue.cpython-313.pyc` ควรลบและเพิ่ม `.gitignore`
  (โปรเจกต์ยังไม่ได้ init git ด้วย — ควรทำ ไฟล์สำคัญอยู่บน Drive อย่างเดียวตอนนี้)

---

## กลุ่ม E — เอกสารกับโค้ดไม่ตรงกัน

### ⬜ E1. `Planning.md` — Mermaid diagram พัง 🟡

`Planning.md:13-21` ปิด code fence ` ``` ` หลัง subgraph แรกเพียงอันเดียว
subgraph ที่เหลืออีก 3 บล็อก (Edge Compute, AI Logic, RF Communication)
จึงหลุดออกมาเป็นข้อความธรรมดา ไดอะแกรมที่ render ได้จริงมีแค่ส่วนแบตเตอรี่กับ PIR

**แนวทางแก้:** ย้าย fence ปิดไปไว้ท้ายสุดหลัง subgraph สุดท้าย

พร้อมกันนี้: บรรทัด 5 มีคำสะกดผิด `"Converseto ly"` → ควรเป็น `"Conversely"`

### ✅ E2. WebP ที่ได้ไม่ใช่ grayscale จริง 🟡

`Planning.md:133` (§7.1) อ้างว่า *"eliminating the chrominance channels... reduces raw pixel data volume by 66.6%"*

แต่ Pillow ไม่รองรับการเขียน WebP แบบ grayscale — เมื่อ `.convert("L")` แล้ว save เป็น WebP
มันจะแปลงกลับเป็น RGB ให้อัตโนมัติ ทดสอบยืนยันแล้ว:

```
wildlife_deer_01.jpg → gray→webp: 208 bytes,  rgb→webp: 502 bytes,  decoded mode: RGB
```

ขนาดไฟล์ลดลงจริง (เพราะ chroma channel แบนราบ) แต่ **ไม่ใช่ 66.6%** และภาพที่ถอดออกมา
ที่ base station เป็น RGB → PNG preview ใหญ่กว่าที่ควรเป็น 3 เท่าโดยไม่ได้ข้อมูลเพิ่ม

**แนวทางแก้:** แก้ตัวเลขใน `Planning.md` ให้ตรงกับที่วัดได้จริง และให้ receiver
`.convert("L")` ก่อนเซฟ PNG preview

### 🔶 E3. ตัวเลขในเอกสารกับค่าจริงในโค้ดไม่ตรง 🟡

| หัวข้อ | `Planning.md` | โค้ดจริง |
| :--- | :--- | :--- |
| ขนาด fragment | "under 200 bytes" (§8.1) | 180 + 8 header = 188 ✅ ตรง |
| ขนาดภาพหลังบีบอัด | 1.5–3.0 KB (§7.2) | 1044 bytes (วัดจริง) — เล็กกว่าที่อ้าง |
| Application header | "4-byte" (บรรทัด 149) | 8 bytes (`!BBHHH`) ❌ ไม่ตรง |
| BLOCKNACK | ส่งกลับผ่าน LoRa (§8.1) | 4 + 2×255 = สูงสุด 514 bytes → **เกินขีดจำกัด 255 bytes ของ SX1262** |
| Base station | ESP32-C3 + MQTT (§10) | Python `http.server` + SSE |
| Duty cycle 10%/ชม. | อ้างอิง NBTC TS 1033-2560 (§9) | ควรตรวจสอบซ้ำ — AS923 ในไทยหลายกรณีกำหนด 1% หรือใช้ LBT |

**แนวทางแก้:** สำหรับ BLOCKNACK ที่ยาวเกิน 255 bytes ต้องออกแบบเป็น bitmap
แทนการส่งรายการ seq number (8 chunks ใช้แค่ 1 byte) หรือแบ่ง BLOCKNACK เป็นหลายแพ็กเก็ต
ส่วนหัวข้ออื่นให้ปรับข้อความในเอกสารให้ตรงกับสิ่งที่ implement จริง
และเพิ่มหมายเหตุว่าส่วนไหนเป็น "design target" ที่ยังไม่ได้ทำ

### ⬜ E4. Mock images ไม่ใช่ภาพจริง — ประเมินโมเดลไม่ได้ 🟡

`mock_images/human_intruder_01.jpg` เป็นภาพเวกเตอร์สังเคราะห์ (คนไม้ขีดสีดำบนพื้นเทา)
และ `wildlife_deer_01.jpg` เป็นรูปกวางวาดด้วยรูปทรงเรขาคณิตบนพื้นเขียว

ใช้ทดสอบ pipeline การบีบอัดและส่งข้อมูลได้ แต่ **ใช้ประเมินความแม่นยำของ YOLO ไม่ได้เลย**
เป็นอีกเหตุผลที่ทำให้การตัดสินจากชื่อไฟล์ (D3) ยังไม่ถูกจับได้

**แนวทางแก้:** ถ้าจะทำ D3 ข้อ (ก) ต้องหาภาพ camera-trap จริงมาก่อน เช่น
Snapshot Serengeti, Caltech Camera Traps หรือ LILA BC ซึ่งเปิดให้ใช้เพื่อการวิจัย

---

## สิ่งที่เหลือทำ

กลุ่ม C แก้ครบแล้ว — สรุปพฤติกรรมปัจจุบัน: `dashboard_server.py` ค่าเริ่มต้น
bind `127.0.0.1` (ปลอดภัยโดยไม่ต้องทำอะไร) ถ้าใช้ `--host 0.0.0.0` เพื่อเปิดดูจากเครื่องอื่น
บน LAN (กรณีใช้งานจริงบน Pi ตาม SETUP_PI.md) ระบบจะบังคับ HTTP Basic Auth ให้อัตโนมัติ
พร้อมสุ่มรหัสผ่านและพิมพ์ที่ console หรือกำหนดเองด้วย `--password`

**เก็บงานเอกสาร**
E1 (Mermaid fence ปิดผิดที่ ทำให้ไดอะแกรมแสดงแค่ส่วนแรก) ·
E3 ที่เหลือ (ตัวเลขในเอกสารกับโค้ด — วัดค่าจริงบน Pi ก่อนแล้วค่อยแก้ ดู SETUP_PI.md §6) ·
E4 (หาภาพ camera-trap จริงมาแทนภาพวาดสังเคราะห์ เช่น Snapshot Serengeti / LILA BC)

**งานที่ยังต้องรอฮาร์ดแวร์**
เปลี่ยน `transmit()` ใน `field_node.py` จาก UDP เป็น SX1262 จริงเมื่อมีโมดูล ·
ทำ Buildroot image ให้บูตถึงถ่ายภาพใต้ 4 วินาที (Planning.md §5) — ยังไม่ได้เริ่ม

---

## หมายเหตุก่อนเริ่ม

- **ยังไม่มี git** ควร `git init` แล้ว commit ทันที — มี `.gitignore` เตรียมไว้ให้แล้ว
  ตอนนี้ถ้าแก้พังจะย้อนได้แค่จาก Google Drive version history เท่านั้น
- **ยังไม่มี pytest** `run_demo_queue.py` และ `run_demo.py` ทำหน้าที่เป็น integration test
  อยู่กลาย ๆ (คืน exit 0/1 ตามผล) ถ้าจะเขียนเทสต์จริง เริ่มที่ `lora_protocol.py`
  ซึ่งแยกออกมาแล้วและทดสอบง่าย
- **แก้ receiver ที่ `receiver_runtime.py` ที่เดียวพอ** — ทั้ง `lora_receiver_emulator.py`
  และ `dashboard_server.py` เรียกใช้ตัวเดียวกันแล้ว
