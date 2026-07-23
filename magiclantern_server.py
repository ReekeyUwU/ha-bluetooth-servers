import asyncio
import colorsys
import os
import sys
import time

sys.path.insert(0, os.environ.get("MAGICLANTERN_LIB_PATH", os.path.dirname(__file__)))

from aiohttp import web
from magiclantern import MagicLantern

AUTH_TOKEN = os.environ["LED_STRIP_AUTH_TOKEN"]

# Map of friendly name -> BLE MAC address. Edit for your own strip(s).
STRIPS = {
    "led_strip": os.environ.get("LED_STRIP_MAC", ""),
}


@web.middleware
async def auth_middleware(request, handler):
    if request.headers.get("X-Auth-Token") != AUTH_TOKEN:
        return web.Response(status=401, text="unauthorized")
    return await handler(request)


lights = {}
locks = {}
rainbow_tasks = {}


# Onboard Bluetooth adapters (e.g. the Pi's UART-attached BCM chip) can wedge
# themselves into a state where bluetoothd answers every request, even scans,
# with org.bluez.Error.InProgress until the hci device is power-cycled.
# Detect that class of error and self-heal by resetting the adapter, then
# retry the connection once. A cooldown avoids hammering the adapter with
# resets if the strip is genuinely just unreachable (powered off / out of
# range) rather than the adapter being stuck.
_last_adapter_reset = 0.0
_ADAPTER_RESET_COOLDOWN = 30.0
_adapter_reset_lock = asyncio.Lock()


def _is_bluez_stuck_error(exc):
    msg = str(exc)
    return "InProgress" in msg or "org.bluez.Error" in msg


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


async def get_light(name):
    if name not in lights or not lights[name].is_connected:
        address = STRIPS[name]
        light = MagicLantern(address)
        try:
            await light.connect()
        except Exception as e:
            if not _is_bluez_stuck_error(e):
                raise
            await reset_adapter()
            light = MagicLantern(address)
            await light.connect()
        lights[name] = light
    return lights[name]


async def with_lock(name, coro_fn):
    if name not in locks:
        locks[name] = asyncio.Lock()
    async with locks[name]:
        light = await get_light(name)
        return await coro_fn(light)


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


async def rainbow_loop(name, speed_pct):
    hue = 0.0
    step = 0.5 + (speed_pct / 100.0) * 4.0
    delay = 0.08
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


app = web.Application(middlewares=[auth_middleware])
app.router.add_post("/{name}/on", handle_on)
app.router.add_post("/{name}/off", handle_off)
app.router.add_post("/{name}/rgb", handle_rgb)
app.router.add_post("/{name}/brightness", handle_brightness)
app.router.add_post("/{name}/mode", handle_mode)
app.router.add_post("/{name}/speed", handle_speed)
app.router.add_post("/{name}/rainbow_start", handle_rainbow_start)
app.router.add_post("/{name}/rainbow_stop", handle_rainbow_stop)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8200)
