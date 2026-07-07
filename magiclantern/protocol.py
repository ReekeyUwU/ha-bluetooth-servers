"""
Magic Lantern / MELK BLE LED controller — wire protocol.

Reverse-engineered from the "Magic Lantern" Android app
(package wl.smartled.rgb, v6.11.06). All control happens by writing a
fixed 9-byte packet to one GATT characteristic.

    BLE service         0000fff0-0000-1000-8000-00805f9b34fb
    write characteristic 0000fff3-0000-1000-8000-00805f9b34fb
    advertised name      starts with "MELK-"

Every packet is exactly 9 bytes:

    byte 0   0x7E   frame header (always)
    byte 1   len    a per-command length/type tag (copied verbatim from the app)
    byte 2   cmd    command id
    byte 3-7 payload
    byte 8   0xEF   frame footer (always)

The app builds these arrays in BluetoothLEService and writes them as-is;
this module reproduces each builder byte-for-byte, so anything the app can
do, these packets can do.
"""
from __future__ import annotations

# GATT identifiers
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "MELK-"

HEADER = 0x7E
FOOTER = 0xEF

# Sub-type flag that rides in byte 7 of colour packets (from the app).
_FLAG_RGB = 0x10      # normal RGB colour
_FLAG_MUSIC = 0x20    # colour pushed by the music/amplitude feature


def _pkt(*b: int) -> bytes:
    """Assemble and validate a 9-byte packet. Bytes may be given as
    signed (-17) or unsigned (0xEF); both are masked to a byte."""
    out = bytes(x & 0xFF for x in b)
    assert len(out) == 9, f"packet must be 9 bytes, got {len(out)}"
    assert out[0] == HEADER and out[8] == FOOTER, "bad frame header/footer"
    return out


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


# --- power -----------------------------------------------------------------
def power(on: bool) -> bytes:
    """Turn the strip on or off. App: lightOn()."""
    o = 1 if on else 0
    return _pkt(0x7E, 0x04, 0x04, o, 0x00, o, 0xFF, 0x00, 0xEF)


# --- static colour ---------------------------------------------------------
def rgb(r: int, g: int, b: int) -> bytes:
    """Set a static RGB colour (0-255 each). App: changeColor()."""
    r, g, b = (_clamp(x, 0, 255) for x in (r, g, b))
    return _pkt(0x7E, 0x07, 0x05, 0x03, r, g, b, _FLAG_RGB, 0xEF)


def music_color(r: int, g: int, b: int) -> bytes:
    """Same as rgb() but tagged as a music-driven update. App: music_amplitude()."""
    r, g, b = (_clamp(x, 0, 255) for x in (r, g, b))
    return _pkt(0x7E, 0x07, 0x05, 0x03, r, g, b, _FLAG_MUSIC, 0xEF)


def color_temperature(warm: int, cold: int) -> bytes:
    """Set warm/cold white channels (0-255 each) on CCT hardware.
    App: changeColorTemperature()."""
    warm, cold = _clamp(warm, 0, 255), _clamp(cold, 0, 255)
    return _pkt(0x7E, 0x06, 0x05, 0x02, warm, cold, 0xFF, 0x08, 0xEF)


def single_color(index: int) -> bytes:
    """Select a preset single colour by index. App: changeSingleColor()."""
    return _pkt(0x7E, 0x05, 0x05, 0x01, _clamp(index, 0, 255), 0xFF, 0xFF, 0x08, 0xEF)


# --- brightness ------------------------------------------------------------
def brightness(value: int, light_mode: int = 0xFF) -> bytes:
    """Set brightness 0-100. App: changeBrightness().
    light_mode is a channel selector; 0xFF (default) = all."""
    return _pkt(0x7E, 0x04, 0x01, _clamp(value, 0, 100),
                light_mode & 0xFF, 0xFF, 0xFF, 0x00, 0xEF)


# --- dynamic effects -------------------------------------------------------
def mode(mode_id: int) -> bytes:
    """Select a built-in dynamic effect (see modes.py, ids 0-212).
    App: changeMode()."""
    return _pkt(0x7E, 0x05, 0x03, _clamp(mode_id, 0, 255), 0x06, 0xFF, 0xFF, 0x00, 0xEF)


def mode_speed(value: int) -> bytes:
    """Set effect speed 0-100. App: changeModeSpeed()."""
    return _pkt(0x7E, 0x04, 0x02, _clamp(value, 0, 100), 0xFF, 0xFF, 0xFF, 0x00, 0xEF)


def scene(scene_id: int) -> bytes:
    """Select a scene preset. App: changeScene()."""
    return _pkt(0x7E, 0x05, 0x31, _clamp(scene_id, 0, 255), 0x07, 0xFF, 0xFF, 0x01, 0xEF)


# --- addressable (symphony / pixel) strips ---------------------------------
def pixel_count(count: int) -> bytes:
    """Set number of LEDs/pixels on an addressable strip (16-bit).
    App: changeSymphonyPoint()."""
    count = _clamp(count, 0, 0xFFFF)
    lo, hi = count & 0xFF, (count >> 8) & 0xFF
    return _pkt(0x7E, 0x07, 0x21, lo, hi, 0x00, 0xFF, 0x00, 0xEF)


def rgb_order(r_pin: int = 1, g_pin: int = 2, b_pin: int = 3) -> bytes:
    """Set the R/G/B channel wiring order (default 1,2,3).
    App: changePinSequence()."""
    return _pkt(0x7E, 0x06, 0x81, r_pin & 0xFF, g_pin & 0xFF, b_pin & 0xFF, 0xFF, 0x00, 0xEF)


def rgbw_status(r: bool, g: bool, b: bool, light_mode: int = 0) -> bytes:
    """Toggle R/G/B/W channels. Reproduces the app's changeRGBWStatus() bit math."""
    on = (r << 16) | (g << 8) | (b << 0)
    i6 = 1 if ((on >> 16) & 1) else 0
    i7 = 1 if (on & 1) else 0
    i8 = 1 if ((on >> 8) & 1) else 0
    i9 = 0xE0 if i6 else 0
    if i7:
        i9 |= 0x10
    lm = light_mode
    if lm not in (0, 1):
        i6 = i7 if lm == 2 else (i8 if lm == 3 else 0)
    return _pkt(0x7E, 0x04, 0x04, i9, lm & 0xFF, i6, 0xFF, 0x00, 0xEF)


# --- microphone / music -in -----------------------------------------------
def mic_on(on: bool) -> bytes:
    """Enable/disable the external microphone. App: changeExternalMicOnOff()."""
    return _pkt(0x7E, 0x04, 0x07, 1 if on else 0, 0xFF, 0xFF, 0xFF, 0x00, 0xEF)


def mic_sensitivity(value: int) -> bytes:
    """Set mic sensitivity 0-100. App: changeExternalMicSensitive()."""
    return _pkt(0x7E, 0x04, 0x06, _clamp(value, 0, 100), 0xFF, 0xFF, 0xFF, 0x00, 0xEF)


def mic_eq_mode(eq_mode: int) -> bytes:
    """Select the mic EQ mode. App: changeExternalMicEqMode() (adds 128)."""
    return _pkt(0x7E, 0x07, 0x03, (eq_mode + 128) & 0xFF, 0x04, 0xFF, 0xFF, 0x00, 0xEF)


# --- timers ----------------------------------------------------------------
def set_time(hour: int, minute: int, second: int, weekmask: int = 0) -> bytes:
    """Push the device clock. App: sendSystemTime()."""
    return _pkt(0x7E, 0x07, 0x83, hour & 0xFF, minute & 0xFF, second & 0xFF,
                weekmask & 0xFF, 0xFF, 0xEF)


def set_timer(hour: int, minute: int, second: int, on_off_mode: int,
              weekmask: int = 0) -> bytes:
    """Program an on/off timer. App: sendTimingStatus()."""
    return _pkt(0x7E, 0x08, 0x82, hour & 0xFF, minute & 0xFF, second & 0xFF,
                on_off_mode & 0xFF, weekmask & 0xFF, 0xEF)


def hexs(pkt: bytes) -> str:
    """Pretty-print a packet as hex, for logging/debugging."""
    return " ".join(f"{b:02X}" for b in pkt)
