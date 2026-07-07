import asyncio
import os
from aiohttp import web
from bleak import BleakScanner
import HueBLE

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


async def get_light(name):
    if name not in lights:
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
        light = await get_light(name)
        return await coro_fn(light)


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
