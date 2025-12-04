#!/usr/bin/env bash
set -Eeuo pipefail

# bootloader configuration - systemd-boot installation and signing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${SCRIPT_DIR}/../config/config.yaml"

setup_boot() {
  echo ">>>>> Installing systemd-boot..."

  if [ ! -f "$CFG" ]; then
      echo "Error: Config file not found at $CFG"
      exit 1
  fi

  local timeout
  local console_mode
  local editor

  timeout=$(yq -r '.boot.loader.timeout' "$CFG")
  console_mode=$(yq -r '.boot.loader.console_mode' "$CFG")
  editor=$(yq -r '.boot.loader.editor' "$CFG")

  if [[ -z "$timeout" || "$timeout" == "null" ]]; then
      echo "Error: boot.loader.timeout not set"
      exit 1
  fi
  if [[ -z "$console_mode" || "$console_mode" == "null" ]]; then
      echo "Error: boot.loader.console_mode not set"
      exit 1
  fi
  if [[ -z "$editor" || "$editor" == "null" ]]; then
      echo "Error: boot.loader.editor not set"
      exit 1
  fi

  local editor_value="no"
  if [[ "$editor" == "true" ]]; then
      editor_value="yes"
  fi

  if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
      echo "    [CI] Installing bootloader without EFI variables..."
      arch-chroot /mnt bootctl install --esp-path=/efi --no-variables
  else
      arch-chroot /mnt bootctl install --esp-path=/efi
  fi

  echo ">>>>> Configuring loader.conf..."
  cat <<EOF > /mnt/efi/loader/loader.conf
timeout $timeout
console-mode $console_mode
editor $editor_value
EOF

  echo ">>>>> Signing bootloader..."
  local bootloader_efi="/efi/EFI/BOOT/BOOTX64.EFI"
  local systemd_efi="/efi/EFI/systemd/systemd-bootx64.efi"

  if [ -f "/mnt$bootloader_efi" ]; then
      echo "    Signing $bootloader_efi..."
      arch-chroot /mnt sbctl sign -s "$bootloader_efi"
  fi

  if [ -f "/mnt$systemd_efi" ]; then
      echo "    Signing $systemd_efi..."
      arch-chroot /mnt sbctl sign -s "$systemd_efi"
  fi

  echo ">>>>> Bootloader installation complete."
}
