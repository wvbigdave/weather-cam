#!/usr/bin/env python3
import ctypes
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from astral import LocationInfo
from astral.sun import sun
from libcamera import Transform
from picamera2 import Picamera2

sys.dont_write_bytecode = True  # never write .pyc to the SD card

# ============================= CONFIG =============================
LATITUDE = 0.0    # <-- set to your latitude
LONGITUDE = 0.0   # <-- set to your longitude
TIMEZONE = "America/New_York"
SUNRISE_OFFSET_MINUTES = 30   # capture starts this long before sunrise
SUNSET_OFFSET_MINUTES = 30    # capture ends this long after sunset
CAPTURE_INTERVAL_SECONDS = 60
TUNING_FILE = "/usr/share/libcamera/ipa/rpi/vc4/ov5647_noir.json"
OUTPUT_PATH = Path("/mnt/ramcam/current.jpg")
LOG_PATH = Path("/mnt/ramcam/weathercam.log")
LOG_MAX_BYTES = 1_000_000     # weathercam.log caps at ~1MB...
LOG_BACKUP_COUNT = 2          # ...plus 2 rotations, ~3MB max on the ramdisk
SETTLE_TIME_SECONDS = 5
RETRY_DELAY_SECONDS = 15
MAX_INIT_ATTEMPTS = 5         # give up and restart the process (drops all leaked FDs)
NIGHT_POLL_SECONDS = 30
FD_WARN_THRESHOLD = 2000      # log a warning if the process holds this many FDs
FD_EXIT_THRESHOLD = 3500      # hard-exit before we truly run out (LimitNOFILE=4096)

TIME_FORMAT = "%Y-%m-%d %I:%M %p"          # e.g. 2026-08-09 08:45 PM
LAST_IMAGE_BANNER = "New Images Start Tomorrow At Sunrise"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
STAMP_TEXT_SIZE = 20
BANNER_TEXT_SIZE = 20
STAMP_MARGIN = 12
STAMP_PAD = 8
BANNER_PAD = 6
BOX_COLOR = (0, 0, 0, 210)                 # near-black, ~82% opaque
BOX_RADIUS = 8
TEXT_COLOR = (255, 255, 255)
JPEG_QUALITY = 90
# ==================================================================

location = LocationInfo("WeatherCam", "World", TIMEZONE, LATITUDE, LONGITUDE)
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

_sun_date = None
_sun_start = None
_sun_end = None

log = logging.getLogger("weathercam")
log.setLevel(logging.DEBUG)
_LOG_FMT = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
try:
    _fh = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    _fh.setFormatter(_LOG_FMT)
    log.addHandler(_fh)
except Exception as exc:
    print(f"WARNING: could not open log file {LOG_PATH}: {exc}", flush=True)
_sh = logging.StreamHandler()
_sh.setFormatter(_LOG_FMT)
log.addHandler(_sh)


def fd_count():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


_systemd_lib = None


def sd_notify(state):
    global _systemd_lib
    try:
        if _systemd_lib is None:
            _systemd_lib = ctypes.CDLL("libsystemd.so.0", use_errno=True)
        if _systemd_lib.sd_notify(0, state.encode()) < 0:
            raise OSError("sd_notify failed")
    except Exception:
        pass


def get_sun_times(now):
    global _sun_date, _sun_start, _sun_end
    today = now.date()
    if today != _sun_date:
        s = sun(location.observer, date=today, tzinfo=location.timezone)
        sunrise = s["sunrise"]
        sunset = s["sunset"]
        if sunrise is None or sunset is None:
            _sun_start = _sun_end = None
        else:
            _sun_start = sunrise - timedelta(minutes=SUNRISE_OFFSET_MINUTES)
            _sun_end = sunset + timedelta(minutes=SUNSET_OFFSET_MINUTES)
        _sun_date = today
        log.info("Capture window: %s -> %s", _sun_start, _sun_end)
    return _sun_start, _sun_end


def is_daytime(now):
    start, end = get_sun_times(now)
    if start is None:
        return True
    return start <= now <= end


_font_cache = {}


def load_font(size):
    font = _font_cache.get(size)
    if font is None:
        font = ImageFont.truetype(FONT_PATH, size)
        _font_cache[size] = font
    return font


def draw_text_box(draw, img_w, box_text, font, anchor_center_x=None, anchor_right_x=None, anchor_y=None):
    bbox = draw.textbbox((0, 0), box_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = STAMP_PAD if anchor_right_x is not None else BANNER_PAD

    if anchor_right_x is not None:
        x0 = anchor_right_x - text_w - 2 * pad
        x1 = anchor_right_x
        y0 = anchor_y
        y1 = anchor_y + text_h + 2 * pad
    else:
        x0 = anchor_center_x - text_w // 2 - pad
        x1 = anchor_center_x + text_w // 2 + pad
        y0 = anchor_y
        y1 = anchor_y + text_h + 2 * pad

    overlay = Image.new("RGBA", (img_w, y1), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x0, y0, x1, y1], radius=BOX_RADIUS, fill=BOX_COLOR)
    od.text((x0 + pad, y0 + pad), box_text, font=font, fill=TEXT_COLOR)
    return overlay


def stamp_photo(path, timestamp_text, banner_text=None):
    with Image.open(path) as src:
        img = src.convert("RGB")
        exif = src.info.get("exif")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    ts_font = load_font(STAMP_TEXT_SIZE)
    right_x = w - STAMP_MARGIN
    ts_y = h - STAMP_MARGIN - STAMP_TEXT_SIZE - 2 * STAMP_PAD
    overlay = draw_text_box(draw, w, timestamp_text, ts_font, anchor_right_x=right_x, anchor_y=ts_y)
    img.paste(overlay, (0, 0), overlay)

    if banner_text:
        b_font = load_font(BANNER_TEXT_SIZE)
        bbox = draw.textbbox((0, 0), banner_text, font=b_font)
        banner_w = bbox[2] - bbox[0]
        banner_h = bbox[3] - bbox[1]
        banner_y = STAMP_MARGIN + 4
        overlay = draw_text_box(draw, w, banner_text, b_font, anchor_center_x=w // 2, anchor_y=banner_y)
        img.paste(overlay, (0, 0), overlay)

    kwargs = {"quality": JPEG_QUALITY}
    if exif is not None:
        kwargs["exif"] = exif
    img.save(path, **kwargs)


def teardown_camera(camera):
    if camera is None:
        return
    try:
        camera.stop()
    except Exception as exc:
        log.debug("camera.stop() error: %r", exc)
    try:
        camera.close()
    except Exception as exc:
        log.debug("camera.close() error: %r", exc)


def init_camera():
    camera = None
    for attempt in range(1, MAX_INIT_ATTEMPTS + 1):
        try:
            tuning = Picamera2.load_tuning_file(TUNING_FILE)
            camera = Picamera2(tuning=tuning)
            config = camera.create_still_configuration(
                main={"size": (1920, 1080)},
                controls={"FrameDurationLimits": (1000000, 1000000)},
                transform=Transform(hflip=True, vflip=True),
            )
            camera.configure(config)
            camera.start()
            time.sleep(SETTLE_TIME_SECONDS)
            log.info("Camera initialized (attempt %d, fds=%d).", attempt, fd_count())
            return camera
        except Exception as exc:
            log.error("Camera init attempt %d/%d failed: %r (fds=%d)",
                      attempt, MAX_INIT_ATTEMPTS, exc, fd_count())
            if camera is not None:
                teardown_camera(camera)
            camera = None
            if attempt < MAX_INIT_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    log.error("Giving up on camera init after %d attempts; exiting for systemd restart.",
              MAX_INIT_ATTEMPTS)
    sys.exit(1)


def main():
    now = datetime.now().astimezone()
    camera = None
    log.info("=== WeatherCam started (daylight=%s, fds=%d) ===", is_daytime(now), fd_count())

    try:
        while True:
            now = datetime.now().astimezone()
            if is_daytime(now):
                if camera is None:
                    log.info("Camera starting (daylight).")
                    camera = init_camera()
                    sd_notify("WATCHDOG=1")
                else:
                    fds = fd_count()
                    if fds > FD_EXIT_THRESHOLD:
                        log.error("FD count %d exceeds exit threshold %d; restarting.",
                                  fds, FD_EXIT_THRESHOLD)
                        teardown_camera(camera)
                        camera = None
                        sys.exit(1)
                    elif fds > FD_WARN_THRESHOLD:
                        log.warning("High FD count: %d (threshold %d).", fds, FD_WARN_THRESHOLD)
                try:
                    camera.capture_file(str(OUTPUT_PATH))
                    ts = datetime.now().astimezone().strftime(TIME_FORMAT)
                    stamp_photo(OUTPUT_PATH, ts)
                    sd_notify("WATCHDOG=1")
                    log.info("Captured %s (%s) fds=%d", OUTPUT_PATH, ts, fd_count())
                except Exception as exc:
                    log.error("Capture failed: %r; resetting camera.", exc)
                    teardown_camera(camera)
                    camera = None
                    sd_notify("WATCHDOG=1")
                time.sleep(CAPTURE_INTERVAL_SECONDS)
            else:
                if camera is not None:
                    log.info("Nightfall: capturing last image of the day.")
                    try:
                        camera.capture_file(str(OUTPUT_PATH))
                        ts = datetime.now().astimezone().strftime(TIME_FORMAT)
                        stamp_photo(OUTPUT_PATH, ts, banner_text=LAST_IMAGE_BANNER)
                        log.info("Last image saved %s (%s)", OUTPUT_PATH, ts)
                    except Exception as exc:
                        log.error("Last capture failed: %r", exc)
                    teardown_camera(camera)
                    camera = None
                    sd_notify("WATCHDOG=1")
                    log.info("Camera stopped (night). fds=%d", fd_count())
                time.sleep(NIGHT_POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Capture stopped by user.")
    except Exception as exc:
        log.exception("Unhandled error: %r", exc)
        raise
    finally:
        if camera is not None:
            teardown_camera(camera)
        log.info("Camera stopped cleanly. fds=%d", fd_count())


if __name__ == "__main__":
    main()