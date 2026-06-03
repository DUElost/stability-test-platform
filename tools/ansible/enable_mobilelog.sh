#!/usr/bin/env bash
set -euo pipefail

command -v adb >/dev/null 2>&1 || { echo "adb not found"; exit 1; }

echo "--- devices ---"
adb devices
echo "--- end ---"

for serial in $(adb devices | grep -E "device$" | awk '{print $1}'); do
  [ -z "$serial" ] && continue
  echo "=== $serial ==="
  adb -s "$serial" shell settings put global development_settings_enabled 1
  adb -s "$serial" shell getprop persist.vendor.mtk.aee.mode
  adb -s "$serial" root 2>&1 | tail -1
  adb -s "$serial" wait-for-device
  adb -s "$serial" shell setprop persist.vendor.mtk.aee.mode 3
  adb -s "$serial" shell getprop persist.vendor.mtk.aee.mode
  adb -s "$serial" shell am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name start --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver 2>&1
  adb -s "$serial" shell settings put global development_settings_enabled 0
  echo "done $serial"
done
echo "=== ALL DONE ==="
