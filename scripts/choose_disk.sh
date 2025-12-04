#!/usr/bin/env bash
set -Eeuo pipefail

# function to choose the target disk
# interactive script to select a disk for installation.
# WARNING: this script asks for confirmation to WIPE the disk.
choose_target_disk() {
  if [[ -n "${TARGET_DISK:-}" ]]; then
    echo ">>>>> Using pre-selected disk from environment: $TARGET_DISK"
    return 0
  fi

  mapfile -t DISKS < <(lsblk -dno NAME,TYPE | awk '$2=="disk"{print $1}')

  echo "Available disks:"
  for i in "${!DISKS[@]}"; do
    d="/dev/${DISKS[$i]}"
    echo "[$i] $d"
    lsblk -dno MODEL,SIZE "$d" | sed 's/^/    /'
    lsblk -no NAME,SIZE,FSTYPE,MOUNTPOINT,UUID "$d" | sed 's/^/    /'
  done

  read -rp "Select disk index to WIPE (the index number): " idx

  if ! [[ "$idx" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid input. Please enter a number."
    exit 1
  fi

  if [[ -z "${DISKS[$idx]:-}" ]]; then
    echo "Error: Invalid selection. Index out of bounds."
    exit 1
  fi

  TARGET_DISK="/dev/${DISKS[$idx]}"

  echo "Selected: $TARGET_DISK"
  read -rp "Type '$TARGET_DISK' to confirm: " c1
  [[ "$c1" == "$TARGET_DISK" ]] || exit 1

  read -rp "Type WIPE-DISK to continue: " c2
  [[ "$c2" == "WIPE-DISK" ]] || exit 1
}
