#!/usr/bin/env bash
# Sync From System - Export current system state
# This script exports the current system's package list and storage layout for reference and backup purposes

set -Eeuo pipefail

echo ">>>>> Exporting installed packages..."
pacman -Qqe > detected-packages.txt
echo "    Saved to detected-packages.txt"

echo ">>>>> Exporting storage layout..."
lsblk -f > detected-storage.txt
echo "    Saved to detected-storage.txt"

echo ">>>>> Done."
