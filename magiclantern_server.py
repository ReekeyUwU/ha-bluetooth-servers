import asyncio
import colorsys
import fcntl
import os
import sys
import time

sys.path.insert(0, os.environ.get("MAGICLANTERN_LIB_PATH", os.path.dirname(__file__)))

from aiohttp import web
from magiclantern import MagicLantern

# Shared across ALL BLE-controlling services on this Pi (hue_ble_server.py,
# magiclantern_server.py) - the onboard Bluetooth adapter crashes (hci0 goes
# DOWN) when two separate processes hit it with truly concurrent operations,
# even though each service already serializes its own device access. This
# lock forces every BLE connect/read/write on the whole Pi to be strictly
# one-at-a-time across process boundaries.
BLE_ADAPTER_LOCK_PATH = os.environ.get("BLE_ADAPTER_LOCK_PATH", "/tmp/ble_adapter.lock")


class _AsyncFileLock:
    def __init__(self, path):
        self._path = path
        self._fh = None

    async def __aenter__(self):
        loop = asyncio.get_event_loop()
        self._fh = open(self._path, "w")
        await loop.run_in_executor(None, fcntl.flock, self._fh, fcntl.LOCK_EX)
        return self

    async def __aexit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None


def ble_adapter_lock():
    return _AsyncFileLock(BLE_ADAPTER_LOCK_PATH)


AUTH_TOKEN = os.environ["LED_STRIP_AUTH_TOKEN"]

# Map of friendly name -> BLE MAC address. Edit for your own strip(s).
STRIPS = {
    "led_strip": os.environ.get("LED_STRIP_MAC", ""),
}

PIXEL_COUNTS = {
    "led_strip": int(os.environ.get("LED_STRIP_PIXEL_COUNT", "0")) or None,
}


@web.middleware
async def auth_middleware(request, handler):
    if request.headers.get("X-Auth-Token") != AUTH_TOKEN:
        return web.Response(status=401, text="unauthorized")
    return await handler(request)


lights = {}
locks = {}
rainbow_tasks = {}
idle_disconnect_tasks = {}

# Keep BLE connections short-lived: the Pi's onboard UART Bluetooth chip
# gets overloaded when several connections are held open indefinitely
# alongside the Hue lamps. Disconnect a strip after a short idle period
# instead of keeping it connected forever; get_light() will reconnect
# on-demand next time it is actually needed.
_IDLE_DISCONNECT_DELAY = 10.0


async def disconnect_light(name):
    light = lights.pop(name, None)
    if light is not None:
        try:
            await light.disconnect()
        except Exception:
            pass


async def _idle_disconnect(name):
    try:
        await asyncio.sleep(_IDLE_DISCONNECT_DELAY)
        await disconnect_light(name)
    except asyncio.CancelledError:
        pass


def _schedule_idle_disconnect(name):
    pending = idle_disconnect_tasks.pop(name, None)
    if pending is not None:
        pending.cancel()
    if name not in rainbow_tasks:
        idle_disconnect_tasks[name] = asyncio.ensure_future(_idle_disconnect(name))


# The Pi's onboard Bluetooth adapter occasionally wedges itself into a state
# where bluetoothd answers every request (even scans) with
# org.bluez.Error.InProgress until the hci device is power-cycled. Detect
# that class of error and self-heal by resetting the adapter, then retry
# the connection once. Cooldown avoids hammering the adapter with resets if
# the strip is genuinely just unreachable (powered off / out of range).
_last_adapter_reset = 0.0
_ADAPTER_RESET_COOLDOWN = 30.0
_adapter_reset_lock = asyncio.Lock()


def _is_bluez_stuck_error(exc):
    msg = str(exc)
    return "InProgress" in msg or "org.bluez.Error" in msg or "was not found" in msg.lower()


async def reset_adapter():
    global _last_adapter_reset
    async with _adapter_reset_lock:
        now = time.monotonic()
        if now - _last_adapter_reset < _ADAPTER_RESET_COOLDOWN:
            return
        _last_adapter_reset = now
        lights.clear()
        hci = os.environ.get("LED_STRIP_HCI_DEVICE", "hci0")
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c",
            f"hciconfig {hci} down && sleep 1 && hciconfig {hci} up && "
            "systemctl restart bluetooth.service",
        )
        await proc.wait()
        await asyncio.sleep(3)


# A single BLE connect attempt can time out simply because of a weak signal
# (out-of-range strip, interference) even when the adapter itself is fine.
# Retry a few times with a bounded per-attempt timeout before giving up; if
# any attempt fails with the BlueZ "stuck adapter" error class, reset the
# adapter before the next retry.
_CONNECT_TIMEOUT = 8.0
_CONNECT_RETRIES = 4
_CONNECT_RETRY_DELAY = 2.0


async def _connect_with_retries(address):
    last_exc = None
    for attempt in range(_CONNECT_RETRIES):
        light = MagicLantern(address)
        try:
            await asyncio.wait_for(light.connect(), timeout=_CONNECT_TIMEOUT)
            return light
        except Exception as e:
            last_exc = e
            if _is_bluez_stuck_error(e):
                await reset_adapter()
            if attempt < _CONNECT_RETRIES - 1:
                await asyncio.sleep(_CONNECT_RETRY_DELAY)
    raise last_exc


async def get_light(name):
    if name not in lights or not lights[name].is_connected:
        address = STRIPS[name]
        light = await _connect_with_retries(address)
        if PIXEL_COUNTS.get(name):
            await light.set_pixel_count(PIXEL_COUNTS[name])
        lights[name] = light
    return lights[name]


async def with_lock(name, coro_fn):
    if name not in locks:
        locks[name] = asyncio.Lock()
    async with locks[name]:
        async with ble_adapter_lock():
            pending = idle_disconnect_tasks.pop(name, None)
            if pending is not None:
                pending.cancel()
            light = await get_light(name)
            result = await coro_fn(light)
    _schedule_idle_disconnect(name)
    return result


async def handle_on(request):
    name = request.match_info["name"]
    try:
        await with_lock(name, lambda light: light.on())
        return web.Response(text="on")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_off(request):
    name = request.match_info["name"]
    try:
        await stop_rainbow(name)
        await with_lock(name, lambda light: light.off())
        return web.Response(text="off")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_rgb(request):
    name = request.match_info["name"]
    try:
        await stop_rainbow(name)
        data = await request.json()
        r, g, b = int(data["r"]), int(data["g"]), int(data["b"])

        async def action(light):
            await light.on()
            await light.set_rgb(r, g, b)

        await with_lock(name, action)
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_brightness(request):
    name = request.match_info["name"]
    try:
        data = await request.json()
        pct = int(data["value"])

        async def action(light):
            await light.on()
            await light.set_brightness(pct)

        await with_lock(name, action)
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_mode(request):
    name = request.match_info["name"]
    try:
        await stop_rainbow(name)
        data = await request.json()
        mode_id = int(data["value"])
        await with_lock(name, lambda light: light.set_mode(mode_id))
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_speed(request):
    name = request.match_info["name"]
    try:
        data = await request.json()
        pct = int(data["value"])
        await with_lock(name, lambda light: light.set_mode_speed(pct))
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def stop_rainbow(name):
    task = rainbow_tasks.pop(name, None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _schedule_idle_disconnect(name)


async def rainbow_loop(name, speed_pct):
    hue = 0.0
    step = 0.5 + (speed_pct / 100.0) * 4.0
    delay = 0.25
    while True:
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(hue / 360.0, 1.0, 1.0)]
        try:
            await with_lock(name, lambda light: light.set_rgb(r, g, b))
        except Exception:
            pass
        hue = (hue + step) % 360
        await asyncio.sleep(delay)


async def handle_rainbow_start(request):
    name = request.match_info["name"]
    try:
        data = await request.json() if request.can_read_body else {}
        speed_pct = int(data.get("speed", 30))
        await stop_rainbow(name)
        await with_lock(name, lambda light: light.on())
        rainbow_tasks[name] = asyncio.ensure_future(rainbow_loop(name, speed_pct))
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_rainbow_stop(request):
    name = request.match_info["name"]
    try:
        await stop_rainbow(name)
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


def _lerp(a, b, t):
    return a + (b - a) * t


async def gradient_loop(name, color_a, color_b, speed_pct):
    t = 0.0
    step = 0.002 + (speed_pct / 100.0) * 0.006
    direction = 1
    delay = 0.15
    while True:
        r = int(_lerp(color_a[0], color_b[0], t))
        g = int(_lerp(color_a[1], color_b[1], t))
        b = int(_lerp(color_a[2], color_b[2], t))
        try:
            await with_lock(name, lambda light: light.set_rgb(r, g, b))
        except Exception:
            pass
        t += step * direction
        if t >= 1.0:
            t = 1.0
            direction = -1
        elif t <= 0.0:
            t = 0.0
            direction = 1
        await asyncio.sleep(delay)


async def handle_gradient_start(request):
    name = request.match_info["name"]
    try:
        data = await request.json()
        color_a = tuple(int(c) for c in data["color_a"])
        color_b = tuple(int(c) for c in data["color_b"])
        speed_pct = int(data.get("speed", 30))
        await stop_rainbow(name)
        await with_lock(name, lambda light: light.on())
        rainbow_tasks[name] = asyncio.ensure_future(gradient_loop(name, color_a, color_b, speed_pct))
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


app = web.Application(middlewares=[auth_middleware])
app.router.add_post("/{name}/on", handle_on)
app.router.add_post("/{name}/off", handle_off)
app.router.add_post("/{name}/rgb", handle_rgb)
app.router.add_post("/{name}/brightness", handle_brightness)
app.router.add_post("/{name}/mode", handle_mode)
app.router.add_post("/{name}/speed", handle_speed)
app.router.add_post("/{name}/rainbow_start", handle_rainbow_start)
app.router.add_post("/{name}/rainbow_stop", handle_rainbow_stop)
app.router.add_post("/{name}/gradient_start", handle_gradient_start)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8200)
