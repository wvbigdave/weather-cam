# 📷 Weather-Cam

**Daylight-only weather camera for the Raspberry Pi Zero W** — captures a
timestamped JPEG every 60 seconds during daylight, then fully shuts the
camera down overnight and automatically restarts at dawn.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-Zero%20W-A22846?style=for-the-badge&logo=raspberrypi&logoColor=white)
![Picamera2](https://img.shields.io/badge/Picamera2-libcamera-0078D4?style=for-the-badge&logo=camera&logoColor=white)
![License](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey?style=for-the-badge&logo=creativecommons&logoColor=white)

---

## 🌤️ What is this?

A single, self-contained Python script (`weather.py`) that turns a
**Raspberry Pi Zero W** with an **OV5647 NoIR** (night-vision) camera into a
daylight-only weather camera.

It computes sunrise and sunset **offline** (using the `astral` library — no
internet or time API required), captures a timestamped JPEG every 60 seconds
during the daylight window, then **fully powers down the camera at night** and
brings it back up automatically at dawn. The idle Pi sips near-zero CPU
overnight instead of burning ~27% keeping a sensor stream alive.

---

## ✨ Features

- **Daylight-only capture** — sunrise/sunset computed offline with `astral`.
  The camera stops after sunset and restarts shortly before sunrise.
- **Full camera shutdown at night** — the Pi idles at ~0.2% CPU overnight
  instead of ~27% when the sensor stream is left running.
- **Configurable capture window** — capture starts 30 minutes before sunrise
  and ends 30 minutes after sunset (both offsets configurable).
- **Timestamp stamping** — the current time is burned into the bottom-right of
  each frame inside a high-contrast, rounded box.
- **End-of-day banner** — the last image of each day carries a *"new images
  start at sunrise"* banner, clearly marking day boundaries in the photo stream.
- **Low CPU streaming** — the sensor stream is capped at 1 fps via
  `FrameDurationLimits`, cutting continuous-stream CPU from ~27% down to
  ~2.5% even while idle.
- **Unbreakable-by-design loop** — hardened against the silent failures that
  originally killed this as a 24/7 service (see
  [Reliability engineering](#reliability-engineering)).
- **On-ramdisk logging** — every capture, window change, and error is logged
  to `weathercam.log` on the same RAM drive as the image, size-capped so it
  can never crowd out the photo (see [Logging](#logging)).
- **Full-config-in-one-place** — every behavior (offsets, paths, fonts,
  times) is a constant in the `CONFIG` section near the top of the script.

---

## 🖥️ Hardware

| Component | Detail |
|-----------|--------|
| **Board** | Raspberry Pi Zero W (BCM2835, armv6l) — rev 1.1 |
| **Also runs on** | Raspberry Pi Zero 2 W (BCM2710, armv7l) |
| **Camera** | OV5647 NoIR (night-vision) module |
| **Storage for output** | `tmpfs` RAM disk (e.g. `/mnt/ramcam`) |

> **Note on NoIR:** the NoIR camera has **no IR filter**, so it needs an IR
> light source to see in low light — see [Troubleshooting](#troubleshooting).

---

## 📦 Software & Dependencies

- Raspberry Pi OS (Debian Trixie), **Python 3.13**
- `python3-picamera2` (libcamera v0.7.1)
- `python3-pil` (Pillow 11.1.0)
- `python3-astral` (3.2)
- DejaVu fonts (shipped with Pi OS)

Install the dependencies:

```bash
sudo apt-get update
sudo apt-get install -y python3-picamera2 python3-pil python3-astral
```

---

## 🛠️ Installation

1. Copy `weather.py` to your Pi, e.g. `/home/(user)/weather.py`.
2. Edit the `CONFIG` section: set your **latitude**, **longitude**, and
   **timezone**.
3. Create the output directory (a `tmpfs` RAM disk so SD wear stays low):
   ```bash
   sudo mkdir -p /mnt/ramcam
   sudo mount -t tmpfs -o size=32m,noatime tmpfs /mnt/ramcam
   ```
   Add the mount to `/etc/fstab` if you want it to survive a reboot.
4. Optionally run as a systemd service (see below).

### Running manually

```bash
python3 /home/(user)/weather.py
```

> First frames may lag a minute or two after boot while libcamera and the
> sensor stream settle; the script's built-in settle delay handles this.

---

## ⚙️ Configuration

Everything lives in the `CONFIG` section near the top of the script:

| Constant | Default | Description |
|----------|---------|-------------|
| `LATITUDE` / `LONGITUDE` | *fill in yours* | Location for sun-time math |
| `TIMEZONE` | `"America/New_York"` | Zone used for sunrise/sunset and stamps |
| `SUNRISE_OFFSET_MINUTES` | 30 | How early capture starts before sunrise |
| `SUNSET_OFFSET_MINUTES` | 30 | How late capture continues after sunset |
| `CAPTURE_INTERVAL_SECONDS` | 60 | Seconds between photos |
| `TUNING_FILE` | `ov5647_noir.json` | libcamera tuning file for the NoIR sensor |
| `OUTPUT_PATH` | `"/mnt/ramcam/current.jpg"` | Where each new photo overwrites the last |
| `LOG_PATH` | `"/mnt/ramcam/weathercam.log"` | On-ramdisk log (auto-created) |
| `LOG_MAX_BYTES` | 1 000 000 | Log rotates at ~1MB |
| `LOG_BACKUP_COUNT` | 2 | Keeps at most 3MB of logs on the ramdisk |
| `SETTLE_TIME_SECONDS` | 5 | Post-start settle delay before first capture |
| `RETRY_DELAY_SECONDS` | 15 | Pause between camera init attempts |
| `MAX_INIT_ATTEMPTS` | 5 | Max init attempts before process exit + restart |
| `NIGHT_POLL_SECONDS` | 30 | How often the night loop checks the clock |
| `FD_WARN_THRESHOLD` | 2000 | Warn if the process holds this many FDs |
| `FD_EXIT_THRESHOLD` | 3500 | Hard-exit before FDs run out (see below) |
| `TIME_FORMAT` | `"%Y-%m-%d %I:%M %p"` | Timestamp text format |
| `LAST_IMAGE_BANNER` | `"New Images Start Tomorrow At Sunrise"` | Text on the day's last image |
| `FONT_PATH` | `DejaVuSans-Bold.ttf` | Font used for stamps |
| `STAMP_TEXT_SIZE` | 20 | Timestamp font size |
| `BANNER_TEXT_SIZE` | 20 | End-of-day banner font size |
| `STAMP_MARGIN` | 12 | Gap between stamp and image edge |
| `STAMP_PAD` | 8 | Padding inside the stamp box |
| `BOX_COLOR` | `(0, 0, 0, 210)` | Near-black ~82% opaque box behind text |
| `BOX_RADIUS` | 8 | Rounded-corner radius of the box |
| `TEXT_COLOR` | `(255, 255, 255)` | White text |
| `JPEG_QUALITY` | 90 | Output JPEG quality |

> The frame is stamped with the time the photo was **taken**, not the time it
> finished processing, so the timestamp reflects the actual moment of capture.

---

## 📝 Logging

A rotating log is written to `/mnt/ramcam/weathercam.log` alongside the
image, recording every startup, capture-window change, capture, nightfall,
shutdown, warning, and error with timestamps.

It is deliberately size-capped: the log rotates at `LOG_MAX_BYTES` (~1MB),
keeping `LOG_BACKUP_COUNT` (2) previous files, so the log can never grow
beyond ~3MB — leaving ~29MB of the 32MB RAM disk free for the image. A full
disk can never block a capture.

To watch it live:

```bash
tail -f /mnt/ramcam/weathercam.log
```

---

## 🔒 Reliability engineering

This script originally ran as a 24/7 service and **stopped taking pictures
for a full day** because of a silent failure. The root cause and the fixes
are documented here so the same thing can't happen again.

### What happened (the `[Errno 24] Too many open files` incident)

On 2026-08-10 the service wedged permanently:

1. Something caused a single camera-initialization failure.
2. The original `init_camera()` looped **forever** retrying inside one
   process: `while True: try: ... except: sleep(15)`.
3. When picamera2's camera setup fails partway, the object's `__del__` calls
   `close()`, which crashed with
   `AttributeError: 'Picamera2' object has no attribute '_preview'`
   **before** it could reach `camera.release()` / `_cm.cleanup()`. Each
   failed attempt therefore leaked libcamera resources (pipes, eventfds,
   dma-bufs).
4. The process leaked file descriptors until it hit the 1024-FD ulimit. From
   then on **every** camera init failed with `[Errno 24] Too many open files`
   — and because the retry loop never exited the process, systemd's
   `Restart=always` could never recycle it. 1100+ failures later it was still
   spinning, taking zero photos.

**Diagnosis:** `ls /proc/<pid>/fd | wc -l` showed exactly 1024 FDs, ~493 of
them leaked pipe pairs, and the journal showed the `__del__`/`close()`
traceback before every `Camera init failed: [Errno 24]` line.

### What we changed to make it unbreakable

1. **Bounded init retries, then give up** — `init_camera()` now tries at
   most `MAX_INIT_ATTEMPTS` (5) times, then **exits the process**
   (`sys.exit(1)`). systemd's `Restart=always` starts a fresh process with a
   clean file-descriptor table. A wedged camera can no longer wedge the
   process forever.
2. **Explicit teardown** — `teardown_camera()` calls both `stop()` and
   `close()` (guarded), so a fully-initialized camera releases libcamera
   resources deterministically instead of relying on the crash-prone `__del__`
   destructor.
3. **FD guard-rail** — each capture loop checks `/proc/self/fd`. Above
   `FD_WARN_THRESHOLD` it logs a warning; above `FD_EXIT_THRESHOLD` it tears
   the camera down and exits so systemd restarts cleanly. Combined with a
   raised `LimitNOFILE`, this turns any future leak into a self-healing
   restart rather than a permanent outage.
4. **systemd watchdog** — the service override sets `WatchdogSec=180` and the
   script calls `sd_notify("WATCHDOG=1")` after every successful
   capture/transition, so systemd kills and restarts the process if it ever
   hangs.
5. **Capture self-healing** — if a capture raises mid-day, the script tears
   down the camera and re-initializes on the next iteration instead of
   crashing out of the day.
6. **Closed image handles** — `stamp_photo()` now opens images with context
   managers so every PIL file handle is released, eliminating a secondary
   FD-leak vector.

The same loop has been verified end-to-end with simulated clock jumps:
sunrise → daytime capture → nightfall last-image + shutdown → next-morning
restart all succeed, with FDs returning to ~5 after shutdown.

---

## 🔁 How it works

- Once per day, `get_sun_times()` computes the day's sunrise/sunset.
- The script considers itself "in the window" from
  `sunrise - SUNRISE_OFFSET_MINUTES` to `sunset + SUNSET_OFFSET_MINUTES`.
- During the window it captures, stamps, and sleeps for
  `CAPTURE_INTERVAL_SECONDS`.
- At window end it stamps the day's final image with the end-of-day banner
  and **fully releases the camera** (`stop()` + `close()`).
- During the night it wakes every `NIGHT_POLL_SECONDS` to check the time; at
  the next morning's window start it re-initializes the camera and resumes.
- Camera init is retried up to `MAX_INIT_ATTEMPTS` times with
  `RETRY_DELAY_SECONDS` backoff; after that the process exits and systemd
  restarts it with a clean state.

---

## ⚡ Running as a systemd service

Create `/etc/systemd/system/weathercam-capture.service`:

```ini
[Unit]
Description=Weather camera capture (daylight only)
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/(user)/weather.py
Restart=always
RestartSec=10
User=(pi user)

[Install]
WantedBy=multi-user.target
```

Recommended hardening override
(`/etc/systemd/system/weathercam-capture.service.d/override.conf`):

```ini
[Service]
# Give the process headroom so a slow leak doesn't hit the 1024 default.
LimitNOFILE=4096
# If the script hangs for 3 minutes, systemd kills and restarts it.
WatchdogSec=180
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now weathercam-capture
sudo systemctl status weathercam-capture
```

### Viewing logs

```bash
journalctl -u weathercam-capture -f
tail -f /mnt/ramcam/weathercam.log
```

You should see messages marking the capture window, nightfall
(*"Nightfall: capturing last image"*), the overnight shutdown
(*"Camera stopped (night)"*), and every capture with the running FD count.

---

## 🛟 Troubleshooting

- **No camera found / initialization errors** — make sure the camera ribbon
  cable is seated correctly and the camera is enabled. A minimum
  `gpu_mem=64` in `/boot/config.txt` is typically required (the default
  `gpu_mem=16` breaks the camera on some setups).
- **First frame takes a while** — normal after boot; the script waits for the
  sensor stream to settle before the first capture.
- **`[Errno 24] Too many open files` in the log** — check the FD count and
  restart the service:
  ```bash
  ls /proc/$(pgrep -f weather.py)/fd | wc -l
  sudo systemctl restart weathercam-capture
  ```
  With this build the script self-heals by exiting; this is just for
  inspection.
- **Overheating / throttling** — check with:
  ```bash
  vcgencmd measure_temp
  vcgencmd get_throttled
  ```
- **Images look dark/black** — this is a NoIR camera; it has no IR filter and
  needs an IR light source to see in low light.

---

## 📋 Change Log

- **2026-08-09** — Original code found: 30s interval, 2592×1944,
  `ov5647_noir` tuning, no timestamp, camera streaming 24/7.
- **2026-08-09** — Interval increased 30s → 60s; resolution lowered to
  1920×1080; added `FrameDurationLimits` 1 fps cap.
- **2026-08-09** — Added daylight-only capture via `astral`; full night
  shutdown; dawn auto-restart with retry/backoff.
- **2026-08-09** — Added timestamp stamping (bottom-right) and end-of-day
  banner.
- **2026-08-09** — Fixed stamp to use actual capture time instead of process
  time.
- **2026-08-11** — **Fixed `[Errno 24] Too many open files` outage.** Bounded
  camera-init retries (then process exit for systemd restart), explicit
  `stop()`+`close()` teardown, FD-count guard-rails, systemd `WatchdogSec`,
  capture self-healing, and context-managed image handles. Added on-ramdisk
  size-capped logging (`weathercam.log`).
- **2026-08-16** — **SD-card wear + memory optimization.** Cached font loading
  (no per-capture SD font read) and the libsystemd handle, added
  `sys.dont_write_bytecode` (no `.pyc` writes to SD), and removed dead code.

---

## 📄 License

This project is licensed under the
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/)
license. See the [LICENSE](LICENSE) file for the full text.

In short: share and adapt freely, but **attribute** the author and link back
to the original code, do **not** use it commercially or sell it, and release
any derivatives under the same license.

---

*Author: [David Flowers](https://github.com/wvbigdave) · Raspberry Pi Zero W ·
OV5647 NoIR · Daylight-only weather capture*
