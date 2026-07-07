"""Toolkit to control Magic Lantern / MELK BLE RGB LED controllers.

Reverse-engineered from the Magic Lantern Android app (wl.smartled.rgb).
"""
from . import protocol
from . import modes
from .client import MagicLantern, FoundDevice, scan

__all__ = ["MagicLantern", "FoundDevice", "scan", "protocol", "modes"]
__version__ = "1.0.0"
