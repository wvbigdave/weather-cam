#!/usr/bin/env python3
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from astral import LocationInfo
from astral.sun import sun
from libcamera import Transform
from picamera2 import Picamera2

# ============================= CONFIG =============================
LATITUDE = 38.599269
LONGITUDE = -82.005519
TIMEZONE = "America/New_York"
SUNRISE_OFFSET_MINUTES = 30   # capture starts this long before sunrise
SUNSET_OFFSET_MINUTES = 30    # capture ends this long after sunset
CAPTURE_INTERVAL_SECONDS = 60
TUNING_FILE = "/usr/share/libcamera/ipa/rpi/vc4/ov5647_noir.json"
OUTPUT_PATH = Path("/mnt/ramcam/current.jpg")
SETTLE_TIME_SECONDS = 5
RETRY_DELAY_SECONDS = 15
NIGHT_POLL_SECONDS = 30

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
        print(f"Capture window: {_sun_start} -> {_sun_end}", flush=True)
    return _sun_start, _sun_end


def is_daytime(now):
    start, end = get_sun_times(now)
    if start is None:
        return True
    return start <= now <= end


def load_font(size):
    return ImageFont.truetype(FONT_PATH, size)


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
    img = Image.open(path).convert("RGB")
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

    exif = Image.open(path).info.get("exif")
    kwargs = {"quality": JPEG_QUALITY}
    if exif is not None:
        kwargs["exif"] = exif
    img.save(path, **kwargs)


def init_camera():
    while True:
        try:
            tuning = Picamera2.load_tuning_file(TUNING_FILE)
            picam2 = Picamera2(tuning=tuning)
            config = picam2.create_still_configuration(
                main={"size": (1920, 1080)},
                controls={"FrameDurationLimits": (1000000, 1000000)},
                transform=Transform(hflip=True, vflip=True),
            )
            picam2.configure(config)
            picam2.start()
            time.sleep(SETTLE_TIME_SECONDS)
            return picam2
        except Exception as exc:
            print(f"Camera init failed: {exc}; retrying in {RETRY_DELAY_SECONDS}s", flush=True)
            time.sleep(RETRY_DELAY_SECONDS)


def stop_camera(picam2):
    try:
        picam2.stop()
    except Exception:
        pass


def main():
    now = datetime.now().astimezone()
    picam2 = None
    daytime = is_daytime(now)

    try:
        while True:
            now = datetime.now().astimezone()
            if is_daytime(now):
                if picam2 is None:
                    print("Camera starting (daylight).", flush=True)
                    picam2 = init_camera()
                picam2.capture_file(str(OUTPUT_PATH))
                ts = datetime.now().astimezone().strftime(TIME_FORMAT)
                stamp_photo(OUTPUT_PATH, ts)
                print(f"Captured {OUTPUT_PATH} ({ts})", flush=True)
                time.sleep(CAPTURE_INTERVAL_SECONDS)
            else:
                if picam2 is not None:
                    print("Nightfall: capturing last image of the day.", flush=True)
                    try:
                        picam2.capture_file(str(OUTPUT_PATH))
                        ts = datetime.now().astimezone().strftime(TIME_FORMAT)
                        stamp_photo(OUTPUT_PATH, ts, banner_text=LAST_IMAGE_BANNER)
                        print(f"Last image saved {OUTPUT_PATH} ({ts})", flush=True)
                    except Exception as exc:
                        print(f"Last capture failed: {exc}", flush=True)
                    stop_camera(picam2)
                    picam2 = None
                    print("Camera stopped (night).", flush=True)
                time.sleep(NIGHT_POLL_SECONDS)
    except KeyboardInterrupt:
        print("Capture stopped by user.", flush=True)
    except Exception as exc:
        print(f"Capture failed: {exc}", flush=True)
        raise
    finally:
        if picam2 is not None:
            stop_camera(picam2)
        print("Camera stopped cleanly.", flush=True)


if __name__ == "__main__":
    main()
