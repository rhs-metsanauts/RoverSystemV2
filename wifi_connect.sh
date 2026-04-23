#!/bin/bash
# wifi_connect.sh — connect to SKANDALAPTOP 6268 and keep retrying if disconnected

TARGET_SSID="SKANDALAPTOP 6268"
TARGET_PASSWORD="SkandaPassword"
RETRY_INTERVAL=10  # seconds between connection checks

connect() {
    echo "$(date): Attempting to connect to '$TARGET_SSID'..."
    nmcli dev wifi connect "$TARGET_SSID" password "$TARGET_PASSWORD" 2>/dev/null
    return $?
}

get_current_ssid() {
    nmcli -t -f active,ssid dev wifi | grep '^yes' | cut -d: -f2
}

echo "$(date): WiFi monitor started. Target: '$TARGET_SSID'"

while true; do
    CURRENT_SSID=$(get_current_ssid)

    if [ "$CURRENT_SSID" = "$TARGET_SSID" ]; then
        echo "$(date): Connected to '$TARGET_SSID'. Monitoring..."
    else
        echo "$(date): Not connected to '$TARGET_SSID' (current: '${CURRENT_SSID:-none}'). Retrying..."
        connect
        if [ $? -eq 0 ]; then
            echo "$(date): Successfully connected to '$TARGET_SSID'."
        else
            echo "$(date): Connection failed. Will retry in ${RETRY_INTERVAL}s..."
        fi
    fi

    sleep $RETRY_INTERVAL
done
