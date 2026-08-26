# ติดตั้ง AP-NET บน Raspberry Pi 5

คู่มือนี้ครอบคลุมอุปกรณ์ที่คุณมีอยู่: **Raspberry Pi 5 + กล้อง + PIR HC-SR501**
ส่วน LoRa ยังไม่มี ระบบจึงส่งข้อมูลผ่าน **UDP บน Wi-Fi แทนคลื่นวิทยุ** — โปรโตคอล MAC,
การแบ่ง fragment, BLOCKNACK และเพดาน duty cycle ทำงานเหมือนกันทุกอย่าง
เมื่อมีโมดูล SX1262 แล้วเปลี่ยนแค่ฟังก์ชัน `transmit()` ใน [edge_node/field_node.py](edge_node/field_node.py)

---

## 1. เตรียมระบบปฏิบัติการ

ใช้ **Raspberry Pi OS Bookworm (64-bit)** — จำเป็น เพราะ `picamera2` และ `lgpio`
มาพร้อมกับ Bookworm และ Pi 5 ไม่รองรับ `RPi.GPIO` แบบเดิมอีกแล้ว

```bash
sudo apt update && sudo apt full-upgrade -y

# กล้อง, GPIO, และ Pillow จาก apt (แม่นยำกว่าและเร็วกว่า pip บน ARM)
sudo apt install -y python3-picamera2 python3-lgpio python3-gpiozero python3-pil git
```

### ลดไฟรั่วตอน shutdown (Planning.md §2.1)

Pi 5 กินไฟเกิน 1.2 W แม้สั่ง halt ไปแล้ว เพราะ PMIC ยังเลี้ยงราง 5V ไว้
แก้ที่ EEPROM ให้ตัดไฟจริง — ลดเหลือ ~0.01 W (ประหยัดขึ้นราว 140 เท่า)

```bash
sudo rpi-eeprom-config --edit
```

เพิ่มสองบรรทัดนี้ แล้ว reboot:

```
POWER_OFF_ON_HALT=1
WAKE_ON_GPIO=0
```

> ทำข้อนี้เฉพาะเมื่อจะใช้วงจร power latch จริง ถ้ายังทดลองบนโต๊ะและอยากปลุกเครื่อง
> ด้วยปุ่มหรือ GPIO ให้ข้ามไปก่อน — `WAKE_ON_GPIO=0` จะปิดการปลุกด้วย GPIO 3

---

## 2. ติดตั้งโปรเจกต์

```bash
cd ~
git clone <repo>  ap-net          # หรือ copy โฟลเดอร์มาจาก Google Drive
cd ap-net

# --system-site-packages สำคัญมาก: ทำให้ venv มองเห็น picamera2/lgpio ที่ลงจาก apt
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

pip install -r requirements-pi.txt
```

ตรวจว่าทุกอย่างพร้อม:

```bash
./run_demo.sh      # เลือกข้อ 7 (System check)
```

ควรเห็น `picamera2 available`, `lgpio available` และ `onnxruntime available`

---

## 3. ใส่โมเดล YOLO

ระบบมองหาโมเดลใน `edge_node/models/` โดยอัตโนมัติ ถ้าไม่เจอจะเตือนแล้วถอยไปใช้
mock (เดาจากชื่อไฟล์) ซึ่ง**ห้ามใช้จริง**

**วิธีที่แนะนำ — export บนเครื่อง Mac แล้วคัดลอกไป** (ไม่ต้องลง PyTorch ~1 GB บน Pi):

```bash
# บน Mac
pip install ultralytics
yolo export model=yolov8n.pt format=onnx imgsz=640

# คัดลอกไป Pi
scp yolov8n.onnx pi@raspberrypi.local:~/ap-net/edge_node/models/
```

ทดสอบบน Pi:

```bash
python3 edge_node/detector.py --image mock_images/human_intruder_01.jpg
```

ควรได้ `backend='onnxruntime'` ไม่ใช่ `'mock'`

> ภาพใน `mock_images/` เป็นภาพวาดสังเคราะห์ YOLO อาจตรวจไม่เจอ — นั่นเป็นพฤติกรรม
> ที่ถูกต้อง ให้ทดสอบด้วยภาพคนจริงจากกล้องแทน (FIX_PLAN.md E4)

---

## 4. ต่อสาย

### กล้อง

Camera Module เสียบสาย MIPI CSI-2 เข้าพอร์ต **CAM/DISP** (Pi 5 มีสองพอร์ต ใช้พอร์ตไหนก็ได้)
Pi 5 ใช้สายแบบบางกว่า Pi 4 — ถ้าสายไม่พอดี ต้องใช้สาย adapter

ทดสอบ: `python3 edge_node/camera.py --backend picamera2`

USB webcam ใช้ `--backend opencv` แทน

### PIR HC-SR501

| HC-SR501 | Raspberry Pi 5 | หมายเหตุ |
| :--- | :--- | :--- |
| VCC | 5V (pin 2 หรือ 4) | โมดูลต้องการ 4.5–20 V |
| GND | GND (pin 6) | |
| OUT | **GPIO 4** (pin 7) | ออก 3.3 V TTL ต่อเข้าขา input ของ Pi ได้ตรง ๆ |

ปรับสองปุ่มบนโมดูล: **Sensitivity** (ระยะ 3–7 m) และ **Time Delay**
(นานแค่ไหนที่ OUT ค้าง HIGH) ตั้ง jumper เป็น **H (repeat trigger)**

ทดสอบ — เดินผ่านหน้าเซ็นเซอร์:

```bash
python3 edge_node/pir_sensor.py --watch 30
```

### วงจร power latch (ทำทีหลังได้)

| สัญญาณ | Pi | หน้าที่ |
| :--- | :--- | :--- |
| HOLD | **GPIO 17** (pin 11) | ขับ HIGH เพื่อค้าง MOSFET latch ไว้เอง |

ลำดับการทำงาน: PIR ส่ง HIGH → latch จ่ายไฟ → Pi บูต → Pi ยก GPIO 17 HIGH ค้างไว้เอง
→ PIR ตกลง LOW แต่ไฟยังอยู่ → ทำงานเสร็จ → Pi ดึง GPIO 17 LOW → halt → ไฟดับสนิท

> **จุดที่พลาดง่าย:** ถ้าซอฟต์แวร์กว่าจะยก GPIO 17 ช้ากว่า pulse ของ PIR ไฟจะดับกลางคัน
> แล้วเครื่องจะวนบูตไม่จบ โค้ดจึงเรียก `latch.hold()` เป็นอย่างแรกก่อน import อะไรที่ช้า

---

## 5. รันจริง

### ตั้ง base station (บน Mac หรือบน Pi เองก็ได้)

```bash
python3 base_station/dashboard_server.py --host 0.0.0.0
# หรือกำหนดรหัสผ่านเอง แทนการให้สุ่มใหม่ทุกครั้งที่ restart:
python3 base_station/dashboard_server.py --host 0.0.0.0 --password "your-team-password"
```

เปิด `http://<ip>:8080` — จะแสดง IP บน LAN ให้ตอนสตาร์ท พร้อม username/password
สำหรับ Basic Auth ที่ browser จะถามตอนเปิดหน้าเว็บครั้งแรก (บังคับใช้อัตโนมัติเมื่อ bind
กับ `0.0.0.0` — ไม่ต้องตั้งค่าเพิ่มถ้ารันแค่ `--host 127.0.0.1` บนเครื่องเดียวกัน)

### ตั้ง field node บน Pi

```bash
# base station อยู่ที่แล็ปท็อป
python3 edge_node/field_node.py --base-host 192.168.1.42 --node-id 1

# ยังไม่ได้ต่อ PIR — ให้ถ่ายทุก 10 วินาทีแทน
python3 edge_node/field_node.py --trigger interval --interval 10

# เล็งกล้อง (ต้องมีจอ)
python3 edge_node/capture_webcam.py --camera picamera2 --transmit
```

### โหมด deploy จริง (power-gated รอบเดียวแล้วดับ)

```bash
sudo python3 edge_node/field_node.py --once --halt-after
```

ให้รันอัตโนมัติตอนบูตด้วย systemd:

```ini
# /etc/systemd/system/apnet-trap.service
[Unit]
Description=AP-NET camera trap
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/home/pi/ap-net
ExecStart=/home/pi/ap-net/.venv/bin/python edge_node/field_node.py --once --halt-after --base-host 192.168.1.42

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable apnet-trap.service
```

---

## 6. ตัวเลขที่ควรวัดเมื่อรันบน Pi จริง

`field_node.py` พิมพ์เวลาให้ทุกรอบอยู่แล้ว:

```
[NODE] capture 180 ms | inference 42 ms | backend onnxruntime | person 91.00%
```

เทียบกับที่ [Planning.md](Planning.md) อ้างไว้:

| ตัวเลข | Planning.md | ที่ควรวัดจริง |
| :--- | :--- | :--- |
| Inference บน Pi 5 (640×640) | 10–35 ms (§6.2) | ดูค่า `inference` |
| Boot ถึงถ่ายภาพ | < 4 s (§5.2) | ต้องทำ Buildroot ก่อน — ยังไม่ได้ทำ |
| ขนาดภาพหลังบีบอัด | 1.5–3.0 KB (§7.2) | วัดได้จริง ~1.0 KB |
| กำลังไฟตอนทำงาน | 3.7–5.0 W (§2.1) | ต้องใช้ USB power meter วัด |

ถ้าตัวเลขที่วัดได้ต่างจากเอกสาร ให้แก้ในเอกสารตามของจริง — FIX_PLAN.md §E3 ค้างไว้อยู่แล้ว

---

## แก้ปัญหาที่พบบ่อย

| อาการ | สาเหตุและวิธีแก้ |
| :--- | :--- |
| `No camera backend available` | `sudo apt install python3-picamera2` แล้วสร้าง venv ใหม่ด้วย `--system-site-packages` |
| `No module named 'lgpio'` | `sudo apt install python3-lgpio` (อย่าใช้ pip) |
| PIR ทริกไม่หยุด | ลด sensitivity, เลี่ยงแดดส่องตรงและลมร้อน, เพิ่ม `--cooldown` |
| `Cannot bind 127.0.0.1:5005` | มี receiver ตัวอื่นรันอยู่ — dashboard มี receiver ในตัว ใช้ `--no-receiver` |
| Dashboard เปิดจากเครื่องอื่นไม่ได้ | ต้องรันด้วย `--host 0.0.0.0` |
| Browser ขึ้น popup ขอ username/password | ปกติ — เกิดตอน bind `0.0.0.0` (ดูรหัสผ่านที่ console ตอนสตาร์ท หรือกำหนดเองด้วย `--password`) |
| ตรวจเจอคนตลอด/ไม่เจอเลย | ดูว่าเป็น `backend='mock'` อยู่หรือเปล่า — ต้องใส่โมเดลจริง |

> **ความปลอดภัย:** เมื่อรันด้วย `--host 0.0.0.0` ระบบจะบังคับ HTTP Basic Auth ให้อัตโนมัติ
> (ดูรหัสผ่านที่ console ตอนสตาร์ท) แจกรหัสผ่านนี้เฉพาะทีม ranger เท่านั้น — ใครก็ตามที่รู้
> รหัสผ่านจะเห็นพิกัดและภาพการตอบสนองของทีมได้แบบเรียลไทม์
