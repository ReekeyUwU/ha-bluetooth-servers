# HA Bluetooth Servers

Small local HTTP control servers that let Home Assistant control Bluetooth LE
devices that have no local/cloud HA integration, by wrapping their BLE
protocols behind a simple authenticated HTTP API. Runs on the same machine
as Home Assistant (or anywhere on the local network with BLE range).

## Devices

- **Philips Hue BLE bulbs** (no Hue Bridge required) via [HueBLE](https://pypi.org/project/HueBLE/) — `hue_ble_server.py`, port 8199
- **Magic Lantern / MELK RGB LED strip controllers** via the bundled `magiclantern` package (reverse-engineered protocol, vendored from a colleague's toolkit — no license file was provided with it) — `magiclantern_server.py`, port 8200

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Copy the example env files and fill in your own auth token + BLE MAC addresses:

```bash
cp hue-ble.env.example /etc/hue-ble.env
cp led-strip.env.example /etc/led-strip.env
```

Generate a random auth token, e.g.:

```bash
python3 -c "import secrets; print(secrets.token_hex(24))"
```

Install as systemd services (edit the `ExecStart` paths in the `.service`
files first):

```bash
sudo cp hue_ble_server.py magiclantern_server.py /opt/ha-bluetooth-servers/
sudo cp -r magiclantern led_strip_control.py led_strip_modes.py /opt/ha-bluetooth-servers/
sudo cp hue-ble-server.service magiclantern-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hue-ble-server.service magiclantern-server.service
```

## Home Assistant wiring

`hue_control.sh` / `led_strip_control.sh` are thin wrappers used from HA's
`command_line:` / `shell_command:` integrations — they read the auth token
from the same env files so the token never needs to live in
`configuration.yaml`. Place them somewhere HA's `command_line:` /
`shell_command:` configs can call (e.g. `/config/` inside the HA container).

See each script's HTTP routes in the source for the available actions
(on/off, brightness, RGB, effect mode, effect speed).
