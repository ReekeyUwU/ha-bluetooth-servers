"""
Async BLE client for Magic Lantern / MELK RGB controllers, built on bleak.

Typical use:

    import asyncio
    from magiclantern import MagicLantern, scan

    async def main():
        devices = await scan()          # find MELK-* controllers
        async with MagicLantern(devices[0].address) as light:
            await light.on()
            await light.set_rgb(255, 0, 0)
            await light.set_brightness(50)
            await light.set_mode(3)     # 7-Color Trans

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner

from . import protocol as P


@dataclass
class FoundDevice:
    name: str
    address: str
    rssi: int

    def __str__(self) -> str:
        return f"{self.name}  [{self.address}]  rssi={self.rssi}"


async def scan(timeout: float = 6.0, prefix: str = P.NAME_PREFIX) -> list[FoundDevice]:
    """Scan for advertising controllers whose name starts with `prefix`
    (default "MELK-"). Returns them sorted by signal strength."""
    found: dict[str, FoundDevice] = {}

    def cb(device, adv):
        name = adv.local_name or device.name or ""
        if name.startswith(prefix):
            found[device.address] = FoundDevice(name, device.address, adv.rssi)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return sorted(found.values(), key=lambda d: d.rssi, reverse=True)


class MagicLantern:
    """A single connected controller. Each call sends one 9-byte packet to the
    fff3 characteristic — the same thing the Android app does."""

    def __init__(self, address: str, *, response: bool | None = None,
                 min_interval: float = 0.02):
        """
        address       BLE MAC (or, on macOS, the CoreBluetooth UUID) of the device.
        response      write-with-response? None = auto-detect from the
                      characteristic's properties (recommended).
        min_interval  minimum seconds between writes; the app paces commands,
                      and back-to-back writes can otherwise be dropped.
        """
        self._client = BleakClient(address)
        self._response = response
        self._min_interval = min_interval
        self._last_write = 0.0

    # --- connection lifecycle ---------------------------------------------
    async def connect(self) -> "MagicLantern":
        await self._client.connect()
        if self._response is None:
            self._response = not self._supports_write_no_response()
        return self

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def __aenter__(self) -> "MagicLantern":
        return await self.connect()

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    def _supports_write_no_response(self) -> bool:
        for svc in self._client.services:
            for ch in svc.characteristics:
                if ch.uuid.lower() == P.WRITE_CHAR_UUID:
                    return "write-without-response" in ch.properties
        return False

    # --- raw send ---------------------------------------------------------
    async def send(self, packet: bytes) -> None:
        """Send a raw 9-byte protocol packet."""
        delta = asyncio.get_event_loop().time() - self._last_write
        if delta < self._min_interval:
            await asyncio.sleep(self._min_interval - delta)
        await self._client.write_gatt_char(
            P.WRITE_CHAR_UUID, packet, response=bool(self._response)
        )
        self._last_write = asyncio.get_event_loop().time()

    # --- high-level commands ---------------------------------------------
    async def on(self):                         await self.send(P.power(True))
    async def off(self):                        await self.send(P.power(False))
    async def set_rgb(self, r, g, b):           await self.send(P.rgb(r, g, b))
    async def set_hex(self, hexcolor: str):
        h = hexcolor.lstrip("#")
        await self.set_rgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    async def set_brightness(self, pct):        await self.send(P.brightness(pct))
    async def set_color_temperature(self, warm, cold):
        await self.send(P.color_temperature(warm, cold))
    async def set_mode(self, mode_id):          await self.send(P.mode(mode_id))
    async def set_mode_speed(self, pct):        await self.send(P.mode_speed(pct))
    async def set_scene(self, scene_id):        await self.send(P.scene(scene_id))
    async def set_pixel_count(self, count):     await self.send(P.pixel_count(count))
    async def set_rgb_order(self, r=1, g=2, b=3):
        await self.send(P.rgb_order(r, g, b))
    async def mic_on(self, on=True):            await self.send(P.mic_on(on))
    async def mic_sensitivity(self, pct):       await self.send(P.mic_sensitivity(pct))
    async def mic_eq_mode(self, m):             await self.send(P.mic_eq_mode(m))
