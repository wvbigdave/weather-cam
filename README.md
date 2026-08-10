# Weather-Cam

A daylight-only weather camera script for the Raspberry Pi Zero W with an
OV5647 NoIR (night vision) camera module, running on a stock Pi OS image.

Captures a timestamped JPEG every 60 seconds during daylight hours, then
**fully shuts the camera down overnight** and automatically restarts it at
dawn — keeping the idle Pi at near-zero CPU.

## Features

- **Daylight-only capture** — sunrise/sunset computed offline with
  `astral` (no internet or time API needed). Camera is stopped after
  sunset and started back up shortly before sunrise.
- **Full camera shutdown at night** — the Pi idles at ~0.2% CPU overnight
  instead of ~27% when the sensor stream is left running.
- **Configurable capture window** — capture starts 30 minutes before
  sunrise and ends 30 minutes after sunset (both offsets configurable).
- **Timestamp stamping** — current time burned into the bottom-right of
  each frame inside a high-contrast box.
- **End-of-day banner** — the last image of each day carries a "new images
  start at sunrise" banner, so the photo stream clearly marks day
  boundaries.
- **Low CPU streaming** — the sensor stream is capped at 1 fps via
  `FrameDurationLimits`, cutting continuous-stream CPU from ~27% down to
  ~2.5% even while idle.
- **Full-config-in-one-place** — every behavior (offsets, paths, fonts,
  times) is a constant in the `CONFIG` section near the top of the script.

## Hardware

- Raspberry Pi Zero W (BCM2835, armv6l) — rev 1.1
- Should also run on a Pi Zero 2 W (BCM2710, armv7l)
- OV5647 NoIR camera module
- Output written to a `tmpfs` RAM disk (see configuration)

## Software & Dependencies

- Raspberry Pi OS (Debian Trixie), Python 3.13
- `python3-picamera2` (libcamera v0.7.1)
- `python3-pil` (Pillow 11.1.0)
- `python3-astral` (3.2)
- DejaVu fonts (shipped with Pi OS)

Install them with:

```bash
sudo apt-get update
sudo apt-get install -y python3-picamera2 python3-pil python3-astral
```

## Installation

1. Copy `weather.py` to your Pi, e.g. `/home/dave/weather.py`.
2. Edit the `CONFIG` section: set your latitude, longitude and
   `ZoneInfo` timezone.
3. Create the output directory:
   ```bash
   sudo mkdir -p /mnt/ramcam
   sudo mount -t tmpfs -o size=32m,noatime tmpfs /mnt/ramcam
   ```
   (Add the mount to `/etc/fstab` if you want it on reboot.)
4. Optionally run as a systemd service (see below).

### Running manually

```bash
python3 /home/dave/weather.py
```

First frames may lag a minute or two after boot while libcamera and the
sensor stream settle; the script's built-in settle delay handles this.

## Configuration

Everything lives in the `CONFIG` dict near the top of the script:

| Constant                  | Default                         | Description                                        |
| ------------------------- | ------------------------------- | -------------------------------------------------- |
| `LATITUDE` / `LONGITUDE`  | Please fill in your own         | Location for sun-time math                         |
| `TIMEZONE`                | `ZoneInfo("America/New_York")`  | Zone used for sunrise/sunset and stamps            |
| `PRE_SUNRISE_MINUTES`     | 30                              | How early capture starts before sunrise            |
| `POST_SUNSET_MINUTES`     | 30                              | How late capture continues after sunset            |
| `CAPTURE_INTERVAL_SECONDS`| 60                              | Seconds between photos                             |
| `RESOLUTION`              | `(1920, 1080)`                  | Frame size (1080p)                                 |
| `OUTPUT_PATH`             | `"/mnt/ramcam/current.jpg"`     | Where each new photo overwrites the previous one   |
| `STAMP_BOTTOM_RIGHT`      | `True`                          | Burn timestamp into the frame                      |
| `STAMP_TEXT_COLOR`        | `"white"`                       | Timestamp text color                               |
| `STAMP_BG_COLOR`          | `(0, 0, 0, 190)`                | Semi-transparent box behind the timestamp          |
| `STAMP_FONT`              | `"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"` | Font used for stamps      |
| `STAMP_FONT_SIZE`         | 40                              | Timestamp font size                                |
| `TIME_FORMAT`             | `"%Y-%m-%d %H:%M:%S"`           | Timestamp text format                              |
| `STAMP_PADDING`           | 12                              | Gap between text and box edge                      |
| `STAMP_BOTTOM` / `STAMP_RIGHT` | 20, 20                      | Offset from the image corners                      |
| `BANNER_FONT_SIZE`        | 56                              | End-of-day banner font size                        |
| `BANNER_MESSAGE`          | `"New Images Start Tomorrow At Sunrise"` | Text shown on the last image of the day     |
| `BANNER_BOTTOM`           | 56                              | Banner position from the bottom                    |

The frame is stamped using the time the photo was **taken**, not the time
it was finished processing, so the timestamp reflects the actual moment of
capture.

## How it works

- Once per day, `get_sun_times()` computes the day's sunrise/sunset.
- The script considers itself "in the window" from
  `sunrise - PRE_SUNRISE_MINUTES` to `sunset + POST_SUNSET_MINUTES`.
- During the window it captures, stamps, and sleeps for
  `CAPTURE_INTERVAL_SECONDS`.
- At window end it stamps the day's final image with the end-of-day banner
  and **stops the camera** (`camera.stop()`) so the sensor stream is fully
  released.
- During the night it wakes every 30 seconds to check the time; at the
  next morning's window start it re-initializes the camera and resumes.
- Camera startup failures are retried with backoff so a slow sensor wake
  doesn't kill the day's capture.

## Running as a systemd service

Create `/etc/systemd/system/weathercam-capture.service`:

```ini
[Unit]
Description=Weather camera capture (daylight only)
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/dave/weather.py
Restart=on-failure
User=(pi user)

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now weathercam-capture
sudo systemctl status weathercam-capture
```

### Viewing logs

```bash
journalctl -u weathercam-capture -f
```

You should see messages marking the capture window, nightfall
("Nightfall reached"), and the overnight shutdown ("Camera stopped (night)")
along with the countdown to next sunrise.

## Troubleshooting

- **No camera found / initialization errors** — make sure the camera ribbon
  cable is seated correctly and the camera is enabled. A minimum
  `gpu_mem=64` in `/boot/config.txt` is typically required (the default
  `gpu_mem=16` breaks the camera on some setups).
- **First frame takes a while** — normal after boot; the script waits for
  the sensor stream to settle before the first capture.
- **Overheating / throttling** — check with:
  ```bash
  vcgencmd measure_temp
  vcgencmd get_throttled
  ```
- **Images look dark/black** — this is a NoIR camera; it has no IR filter
  and needs an IR light source to see in low light.

## Change Log

- **2026-08-09** — Original code found: 30s interval, 2592x1944,
  `ov5647_noir` tuning, no timestamp, camera streaming 24/7.
- **2026-08-09** — Interval increased 30s → 60s; resolution lowered to
  1920x1080; added `FrameDurationLimits` 1 fps cap.
- **2026-08-09** — Added daylight-only capture via `astral`; full night
  shutdown; dawn auto-restart with retry/backoff.
- **2026-08-09** — Added timestamp stamping (bottom-right) and end-of-day
  banner.
- **2026-08-09** — Fixed stamp to use actual capture time instead of
  process time.

## License

This project is licensed under the
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/)
license. See the [LICENSE](LICENSE) file for the full text.

In short: share and adapt freely, but **attribute** the author and link
back to the original code, do **not** use it commercially or sell it, and
release any derivatives under the same license.
