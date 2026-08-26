# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

**AP-NET** — a real-time, LoRa-enabled anti-poaching camera trap network for Huai Kha
Khaeng Wildlife Sanctuary, Thailand.

The whole design turns on one idea: **only human detections are allowed to cost battery
and radio time.** A trap sits at 0 W until a PIR sensor wakes it, captures one frame, runs
a person detector on-device, and then either transmits a heavily compressed image over a
long-range radio link (human) or silently caches the full-resolution frame to SD and goes
back to sleep (animal, wind, falling branch).

Two halves, both real code:

- **`Planning.md`** — the engineering design paper: hardware selection, power budgets,
  Buildroot boot optimisation, NBTC/AS923 spectrum regulation, enclosure specs.
  69 cited sources in `reference/references.json`.
- **The code** — a working implementation of the edge pipeline, the custom fragmented MAC
  layer, and the base station dashboard.

### What is real vs. emulated

| Component | Status |
| :--- | :--- |
| Person detection | **Real** — YOLOv8n via ONNX Runtime / ultralytics / TFLite |
| Camera capture | **Real** — picamera2, rpicam-still, or OpenCV |
| PIR wake + power latch | **Real** — gpiozero / lgpio on Pi 5 GPIO |
| Fragmented MAC + ARQ | **Real protocol**, carried over UDP instead of 923 MHz |
| SX1262 radio | **Emulated** — no hardware yet; swap `transmit()` in `field_node.py` |
| Packet loss | Simulated (`loss_rate`, default 0.15) |
| ESP32-C3 gateway | Replaced by a Python HTTP server + vanilla-JS dashboard |

`SETUP_PI.md` covers running this on real Raspberry Pi 5 hardware.

## Layout

```
paths.py                          Every filesystem path, derived from __file__
lora_protocol.py                  The wire format: fragments, BLOCKNACK, reassembly

edge_node/                        The camera trap
  field_node.py                   THE FIELD RUNNER: PIR -> capture -> detect -> transmit
  detector.py                     Person detection; backends + an honest mock fallback
  camera.py                       picamera2 / rpicam / opencv / file
  pir_sensor.py                   PIRSensor + PowerLatch (gpiozero / lgpio)
  edge_node.py                    One-shot CLI: detect, compress, or cache
  lora_sender_emulator.py         Standalone transmitter with ARQ
  capture_webcam.py               Live preview window for aiming the camera
  models/                         Drop yolov8n.onnx here (gitignored)

base_station/                     The gateway
  dashboard_server.py             MAIN ENTRY POINT: HTTP + SSE + embedded receiver
  receiver_runtime.py             The shared receive loop
  incident_store.py               Append-only incident history (JSONL)
  lora_receiver_emulator.py       Standalone receiver (thin CLI over receiver_runtime)
  sensor_config.json              Node ID -> lat/lng. The "virtualized GPS"
  dashboard_static/               index.html / app.js / styles.css — no build step

run_demo.sh                       Menu launcher (bash, works on macOS and Pi OS)
run_demo_queue.py                 Integration test over in-memory queues (fastest)
run_demo.py                       Integration test over real UDP sockets
mock_images/                      Synthetic placeholder art, NOT real camera-trap photos
```

## Running things

```bash
./run_demo.sh                                   # menu; option 7 = system check
python3 run_demo_queue.py                       # fastest end-to-end test
python3 run_demo.py                             # same, over real sockets
python3 base_station/dashboard_server.py        # dashboard on :8080
python3 edge_node/field_node.py --trigger interval --interval 10   # field node, no PIR
```

Every module has a `--check`:

```bash
python3 edge_node/detector.py --check      # which inference runtimes exist
python3 edge_node/camera.py --check        # which camera backends exist
python3 edge_node/pir_sensor.py --check    # which GPIO backends exist
```

Only Pillow is required. Everything else degrades gracefully and says what to install.

## Wire protocol

**Data fragment** — 8-byte big-endian header + UTF-8 Base64 text:

```
!BBHHH  node_id | trans_id | seq_num (1-based) | total_chunks | payload_len
```

**BLOCKNACK** — 4-byte header + N × uint16 sequence numbers:

```
!BBBB   node_id | trans_id | status (0=SUCCESS, 1=NACK) | count
```

`MAX_FRAGMENT_SIZE = 180` (+8 header = 188 B, under the SX1262's 255 B frame limit).
A BLOCKNACK carries at most 125 sequence numbers so it fits one real LoRa frame; more
missing than that simply takes another ARQ round.

All of this lives in `lora_protocol.py`. Do not reimplement it elsewhere — it used to be
copy-pasted across four files and the copies drifted.

## Conventions and gotchas

- **`edge_node.py` exit codes are a contract**: `0` human detected, `1` no threat,
  `2` error. `run_demo.sh` and `run_demo.py` branch on them. Never collapse 1 and 2 —
  that conflation once made crashes report as passing tests.
- **The mock detector announces itself.** When no model is found, `detect_human()` falls
  back to matching the filename and prints a loud warning; `allow_mock=False` makes it
  raise instead. Never let a mock verdict be presented as real evidence.
- **Acknowledge before persisting.** `receiver_runtime` sends SUCCESS as soon as the
  payload decodes, then writes to disk. A disk stall must never sit inside the radio
  protocol's timing budget — a battery-powered trap would retransmit an image the base
  station already has.
- **`dashboard_server.py` only wipes `received_images/` on an explicit `--reset`**, and
  archives rather than deletes.
- **Incident history is server-side** (`received_images/incidents.jsonl`, via
  `incident_store.py`). The browser's `localStorage` is only an offline cache.
- **Resolving an incident requires its `log_id`.** `POST /resolve-alert` without one falls
  back to the newest incident, which is what the old buggy behaviour did unconditionally.
- **Two receivers, one port.** `dashboard_server.py` runs a receiver internally, so it and
  `lora_receiver_emulator.py` cannot both hold UDP 5005. Use `--no-receiver` to free it.
- **`sensor_config.json` is the virtualized GPS.** The design deliberately omits GPS
  hardware and resolves coordinates from node ID at the base station. Preserve that.
- **Pi 5 GPIO: use gpiozero or lgpio, never RPi.GPIO** — it does not work on the Pi 5.
- **Demo timings are shortened from field values** and say so in comments: heartbeat
  3 h → 5 s, online window 3 h 10 m → 15 s.
- Frontend is plain JS, no bundler, no framework, no tests. Edit `app.js` directly.
- Leaflet and Google Fonts load from CDNs, so the dashboard needs internet.

## Dashboard security

`dashboard_server.py` defaults to `--host 127.0.0.1`. Binding anywhere else
(`--host 0.0.0.0`, needed to view it from another machine on the LAN - e.g. a Pi 5 field
node reaching this as its base station) automatically requires HTTP Basic Auth: a
password is auto-generated and printed at startup, or set a fixed one with `--password`.
The browser handles the challenge natively, including on images, `fetch()` and the
`/events` SSE stream - nothing in `app.js` special-cases auth. `--no-auth` exists only for
an isolated test network.

`app.js` escapes every server-sourced string before it goes into `innerHTML`
(`escapeHtml()`), since `sensor_config.json` and `POST /simulate-alert`'s `node_id` are
both attacker-writable. One spot needed more than escaping: a "view alert" button used to
build an inline `onclick="viewActiveThreat('${nodeId}')"` handler, and HTML-escaping alone
does not close that hole - the browser HTML-decodes an attribute's value before handing it
to the JS parser, so an escaped `'` becomes a real one again right before execution. It's
wired through `data-node-id` + a delegated `addEventListener` instead, which never turns
the value into JS source text at all.

`/simulate-alert` rate-limits to `SIMULATE_MIN_INTERVAL_S` (429 + `Retry-After` past that),
caps stored images at `SIMULATE_MAX_IMAGES` via `prune_simulated_images()`, and sanitises
`node_id` through `safe_filename_component()` before it can become part of a path - an
unfiltered `node_id` like `"../../../../tmp/x"` used to let the seed-image copy escape
`received_images/` entirely.

## Known gaps

`FIX_PLAN.md` tracks all findings with status markers. Still open, both documentation-only:

- **E1** — the Mermaid diagram in `Planning.md` closes its code fence early, so only the
  first subgraph renders.
- **E4** — `mock_images/` holds synthetic vector art, useless for evaluating detection
  accuracy. Real camera-trap imagery is needed for that.

Git is initialized; there is no pytest suite yet. `lora_protocol.py` is the natural place
to start one.
