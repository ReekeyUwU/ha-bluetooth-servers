#!/bin/bash
# Wrapper for the local Hue BLE control server, used by Home Assistant's
# command_line/shell_command integrations. Reads the auth token from an env
# file so it never needs to appear in configuration.yaml.
#
# Expects a file at /etc/hue-ble.env (or $HUE_BLE_ENV_FILE) containing:
#   HUE_BLE_AUTH_TOKEN=your-token-here
ENV_FILE="${HUE_BLE_ENV_FILE:-/etc/hue-ble.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
TOKEN="$HUE_BLE_AUTH_TOKEN"
LAMP="$1"
ACTION="$2"

if [ "$ACTION" = "state" ]; then
  curl -s -H "X-Auth-Token: $TOKEN" "http://localhost:8199/${LAMP}/state"
elif [ "$ACTION" = "brightness_set" ]; then
  curl -s -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
    -d "{\"value\":$3}" "http://localhost:8199/${LAMP}/brightness"
elif [ "$ACTION" = "brightness_get" ]; then
  curl -s -H "X-Auth-Token: $TOKEN" "http://localhost:8199/${LAMP}/brightness"
else
  curl -s -X POST -H "X-Auth-Token: $TOKEN" "http://localhost:8199/${LAMP}/${ACTION}"
fi
