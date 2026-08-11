import asyncio
import fcntl
import os
from aiohttp import web
from bleak import BleakScanner
import HueBLE

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


AUTH_TOKEN = os.environ["HUE_BLE_AUTH_TOKEN"]

# Map of friendly name -> BLE MAC address. Edit for your own lamps.
LAMPS = {
    "flur": os.environ.get("HUE_LAMP_FLUR_MAC", ""),
    "bad": os.environ.get("HUE_LAMP_BAD_MAC", ""),
}


@web.middleware
async def auth_middleware(request, handler):
    if request.headers.get("X-Auth-Token") != AUTH_TOKEN:
        return web.Response(status=401, text="unauthorized")
    return await handler(request)

lights = {}
locks = {}
idle_disconnect_tasks = {}

# Keep BLE connections short-lived: the Pi's onboard UART Bluetooth chip
# gets overloaded when several connections are held open indefinitely
# alongside the LED strip. Disconnect a lamp after a short idle period
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
    idle_disconnect_tasks[name] = asyncio.ensure_future(_idle_disconnect(name))


async def get_light(name):
    if name not in lights or not lights[name].connected:
        address = LAMPS[name]
        device = await BleakScanner.find_device_by_address(address, timeout=15)
        if device is None:
            raise RuntimeError(f"device {name} ({address}) not found")
        lights[name] = HueBLE.HueBleLight(device)
        locks[name] = asyncio.Lock()
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
        async def action(light):
            await light.set_power(True)
            return light.power_state
        state = await with_lock(name, action)
        return web.Response(text="on" if state else "off")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_off(request):
    name = request.match_info["name"]
    try:
        async def action(light):
            await light.set_power(False)
            return light.power_state
        state = await with_lock(name, action)
        return web.Response(text="on" if state else "off")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_state(request):
    name = request.match_info["name"]
    try:
        async def action(light):
            await light.poll_power_state()
            return light.power_state
        state = await with_lock(name, action)
        return web.Response(text="on" if state else "off")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_brightness_set(request):
    name = request.match_info["name"]
    try:
        data = await request.json()
        pct = int(data["value"])
        raw = round(pct / 100 * 255)
        async def action(light):
            if pct <= 0:
                await light.set_power(False)
            else:
                if not light.power_state:
                    await light.set_power(True)
                await light.set_brightness(raw)
            return True
        await with_lock(name, action)
        return web.Response(text="ok")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def handle_brightness_get(request):
    name = request.match_info["name"]
    try:
        async def action(light):
            await light.poll_brightness()
            return light.brightness
        raw = await with_lock(name, action)
        pct = round((raw or 0) / 255 * 100)
        return web.Response(text=str(pct))
    except Exception as e:
        return web.Response(status=500, text=str(e))


app = web.Application(middlewares=[auth_middleware])
app.router.add_post("/{name}/on", handle_on)
app.router.add_post("/{name}/off", handle_off)
app.router.add_get("/{name}/state", handle_state)
app.router.add_post("/{name}/brightness", handle_brightness_set)
app.router.add_get("/{name}/brightness", handle_brightness_get)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8199)
