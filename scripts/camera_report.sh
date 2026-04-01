#!/usr/bin/env bash

set -u

print_section() {
  local title="$1"
  printf "\n===== %s =====\n" "$title"
}

run_command() {
  local description="$1"
  shift
  print_section "$description"
  printf "+ %s\n" "$*"
  "$@" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    printf "[exit-code] %s\n" "$rc"
  fi
}

run_if_available() {
  local binary="$1"
  shift
  local description="$1"
  shift
  if command -v "$binary" >/dev/null 2>&1; then
    run_command "$description" "$@"
  else
    print_section "$description"
    printf "%s not found in PATH\n" "$binary"
  fi
}

printf "OpenScan Camera Report\n"
printf "Generated: %s\n" "$(date --iso-8601=seconds)"
printf "Host: %s\n" "$(hostname)"
printf "Kernel: %s\n" "$(uname -srmo)"

run_if_available "v4l2-ctl" "V4L2 device overview" v4l2-ctl --list-devices
run_command "Video and media device nodes" bash -lc 'ls -l /dev/video* /dev/media* 2>/dev/null || echo "No /dev/video* or /dev/media* nodes found"'
run_if_available "lsusb" "USB device tree" lsusb -t
run_if_available "lsusb" "USB device list" lsusb
run_if_available "usb-devices" "USB devices (kernel view)" usb-devices
run_command "Kernel camera/video log excerpts" bash -lc 'dmesg | egrep -i "camera|video|uvc|bcm2835|unicam" | tail -n 200'
run_command "Kernel USB log excerpts" bash -lc 'dmesg | egrep -i "usb|xhci|dwc2|dwc_otg|hub|mtp|ptp" | tail -n 200'
run_command "Boot firmware config (/boot/firmware/config.txt)" bash -lc 'if [ -f /boot/firmware/config.txt ]; then sed -n "1,240p" /boot/firmware/config.txt; else echo "/boot/firmware/config.txt not found"; fi'

if command -v v4l2-ctl >/dev/null 2>&1; then
  print_section "Per-device V4L2 details"
  shopt -s nullglob
  video_devices=(/dev/video*)
  shopt -u nullglob

  if [ "${#video_devices[@]}" -eq 0 ]; then
    echo "No /dev/video* devices found"
  else
    for dev in "${video_devices[@]}"; do
      printf "\n--- %s ---\n" "$dev"
      v4l2-ctl -d "$dev" --all 2>&1 | head -n 80
    done
  fi
fi

if command -v udevadm >/dev/null 2>&1; then
  print_section "udev info for /dev/video*"
  shopt -s nullglob
  video_devices=(/dev/video*)
  shopt -u nullglob
  if [ "${#video_devices[@]}" -eq 0 ]; then
    echo "No /dev/video* devices found"
  else
    for dev in "${video_devices[@]}"; do
      printf "\n--- %s ---\n" "$dev"
      udevadm info --query=all --name="$dev" 2>&1 | head -n 120
    done
  fi
else
  print_section "udev info for /dev/video*"
  echo "udevadm not found in PATH"
fi
