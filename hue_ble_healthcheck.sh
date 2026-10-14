#!/bin/bash
exec 9>/tmp/hue_ble_healthcheck.lock
if ! flock -n 9; then exit 0; fi
LAMPS="bad flur"
COOLDOWN_FILE=/tmp/hue_ble_healthcheck.last_action
COOLDOWN_SECONDS=180
UNHEALTHY_SINCE_FILE=/tmp/hue_ble_healthcheck.unhealthy_since
REBOOT_THRESHOLD_SECONDS=900
FRAME_ERR_MARKER=/tmp/hue_ble_healthcheck.last_frame_err_check

# Reboot circuit breaker: /tmp is tmpfs and is wiped on every reboot, so
# UNHEALTHY_SINCE_FILE alone can never detect "we just rebooted and it's
# still broken" across a reboot - it always restarts its 900s countdown
# from zero on a fresh boot. That previously caused a reboot loop when the
# underlying issue (flaky Bluetooth hardware) didn't clear on reboot. This
# log lives on the real disk (survives reboots) and caps us to at most 2
# reboots per hour; beyond that we give up escalating and just keep doing
# the cheaper restart/adapter-reset actions until a human intervenes.
REBOOT_LOG=/home/reekey/.hue_ble_reboot_log
REBOOT_WINDOW_SECONDS=3600
MAX_REBOOTS_PER_WINDOW=2

recent_reboot_count() {
    [ -f "$REBOOT_LOG" ] || { echo 0; return; }
    local cutoff=$(( $(date +%s) - REBOOT_WINDOW_SECONDS ))
    awk -v cutoff="$cutoff" '$1 >= cutoff' "$REBOOT_LOG" | wc -l
}

now=$(date +%s)
last=0
[ -f "$COOLDOWN_FILE" ] && last=$(cat "$COOLDOWN_FILE")
since=$((now - last))
[ "$since" -lt "$COOLDOWN_SECONDS" ] && exit 0

reset_adapter_hard() {
    logger -t hue-ble-healthcheck "Hard reset: reloading hci_uart kernel module"
    systemctl stop bluetooth.service
    sleep 1
    rmmod hci_uart 2>/dev/null
    sleep 1
    modprobe hci_uart
    sleep 2
    systemctl start bluetooth.service
    sleep 2
}

# Reboot escalation was removed entirely: it never actually fixed the
# underlying Flur Bluetooth hardware fault (a persistent BCM43430 UART
# issue), it only bought a few minutes before the same problem reappeared,
# and each reboot took Home Assistant itself down for everyone. Restarting
# the server / resetting the adapter is the ceiling now; if that's not
# enough the lamp needs a human (or a USB Bluetooth dongle) rather than
# another reboot.
maybe_reboot_if_stuck() {
    if [ ! -f "$UNHEALTHY_SINCE_FILE" ]; then
        echo "$now" > "$UNHEALTHY_SINCE_FILE"
    fi
    return
}

mark_healthy() {
    rm -f "$UNHEALTHY_SINCE_FILE"
}

# Stage 0: the BCM43430 UART chip on the Pi 3B intermittently corrupts HCI
# frames ("Bluetooth: hci0: Frame reassembly failed (-84)" in the kernel log).
# When this happens hci0 still reports UP RUNNING but every GATT op fails.
# A plain server restart does NOT clear it - only reloading the hci_uart
# kernel module does. Detect fresh frame errors and hard-reset immediately,
# before wasting a cooldown cycle on an ineffective server restart.
kmsg_recent_frame_errors() {
    # Look at the last 90 seconds of kernel log for the reassembly error.
    dmesg --time-format=iso 2>/dev/null | tail -400 \
        | grep 'Frame reassembly failed' | tail -1
}

if [ -n "$(kmsg_recent_frame_errors)" ]; then
    # Only act if we haven't already reacted to frame errors this cooldown.
    last_fe=0
    [ -f "$FRAME_ERR_MARKER" ] && last_fe=$(cat "$FRAME_ERR_MARKER")
    if [ $((now - last_fe)) -ge "$COOLDOWN_SECONDS" ]; then
        logger -t hue-ble-healthcheck "Frame reassembly errors detected in kernel log - hard-resetting adapter"
        echo "$now" > "$FRAME_ERR_MARKER"
        echo "$now" > "$COOLDOWN_FILE"
        maybe_reboot_if_stuck
        reset_adapter_hard
        systemctl restart hue-ble-server.service
        exit 0
    fi
fi

# Stage 1: adapter reported down -> try a normal power-up first
if ! hciconfig hci0 | grep -q 'UP RUNNING'; then
    logger -t hue-ble-healthcheck "hci0 is down - attempting normal power-up"
    echo "$now" > "$COOLDOWN_FILE"
    maybe_reboot_if_stuck
    # A transient RF-kill soft-block on hci0 (seen in practice - "Operation
    # not possible due to RF-kill") often clears itself if you just retry
    # "hciconfig hci0 up" a couple more times a moment later; escalating
    # straight to a kernel-module reload for something this transient was
    # needlessly disruptive. Only fall back to the hard reset if it's still
    # down after a few short retries.
    up_ok=1
    for attempt in 1 2 3; do
        if timeout 8 hciconfig hci0 up && hciconfig hci0 | grep -q 'UP RUNNING'; then
            up_ok=0
            logger -t hue-ble-healthcheck "hci0 up succeeded on attempt $attempt"
            break
        fi
        sleep 3
    done
    if [ "$up_ok" -ne 0 ]; then
        logger -t hue-ble-healthcheck "hci0 still down after retries - hard-resetting adapter"
        reset_adapter_hard
    fi
    systemctl restart hue-ble-server.service
    exit 0
fi

unhealthy=""
for lamp in $LAMPS; do
    resp=$(timeout 15 bash /home/reekey/homeassistant/hue_control.sh "$lamp" state 2>/dev/null)
    if [ "$resp" != "on" ] && [ "$resp" != "off" ]; then
        unhealthy="$unhealthy $lamp($resp)"
    fi
done

if [ -n "$unhealthy" ]; then
    maybe_reboot_if_stuck

    scan_log=$(timeout 6 bluetoothctl --timeout 5 scan on 2>&1)
    adapter_dead=1
    for lamp in $LAMPS; do
        case $lamp in
            bad) mac=F8:8D:F2:C8:E4:F2 ;;
            flur) mac=F0:B8:DC:DF:EA:44 ;;
        esac
        echo "$scan_log" | grep -qi "$mac" && adapter_dead=0
    done

    echo "$now" > "$COOLDOWN_FILE"
    if [ "$adapter_dead" = "1" ]; then
        logger -t hue-ble-healthcheck "Lamps unhealthy ($unhealthy) and not advertising - resetting adapter"
        if ! timeout 8 bash -c 'hciconfig hci0 down; sleep 1; hciconfig hci0 up'; then
            reset_adapter_hard
        fi
        systemctl restart hue-ble-server.service
    else
        logger -t hue-ble-healthcheck "Lamps unhealthy ($unhealthy) but still advertising - restarting hue-ble-server.service only"
        systemctl restart hue-ble-server.service
    fi
else
    mark_healthy
fi
