#!/bin/bash
# Wrapper for the local Magic Lantern BLE LED strip control server, used by
# Home Assistant's command_line/shell_command integrations. Reads the auth
# token from an env file so it never needs to appear in configuration.yaml.
#
# Expects a file at /etc/led-strip.env, or /config/led-strip.env if this
# script runs inside the Home Assistant container (where /etc isn't shared
# with the host), or $LED_STRIP_ENV_FILE, containing:
#   LED_STRIP_AUTH_TOKEN=your-token-here
if [ -n "$LED_STRIP_ENV_FILE" ]; then
  ENV_FILE="$LED_STRIP_ENV_FILE"
elif [ -f /etc/led-strip.env ]; then
  ENV_FILE=/etc/led-strip.env
else
  ENV_FILE=/config/led-strip.env
fi
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
TOKEN="$LED_STRIP_AUTH_TOKEN"
BASE="http://localhost:8200/led_strip"

case "$1" in
  on)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" "$BASE/on"
    ;;
  off)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" "$BASE/off"
    ;;
  rgb)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
      -d "{\"r\":$2,\"g\":$3,\"b\":$4}" "$BASE/rgb"
    ;;
  brightness)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
      -d "{\"value\":$2}" "$BASE/brightness"
    ;;
  mode)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
      -d "{\"value\":$2}" "$BASE/mode"
    ;;
  speed)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
      -d "{\"value\":$2}" "$BASE/speed"
    ;;
  gradient)
    curl -s -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
      -d "{\"color_a\":[$2,$3,$4],\"color_b\":[$5,$6,$7],\"speed\":$8}" "$BASE/gradient_start"
    ;;
  *)
    echo "unknown command"
    exit 1
    ;;
esac
