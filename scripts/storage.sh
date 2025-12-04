#!/usr/bin/env bash
set -Eeuo pipefail

# storage convergence - handles disk partitioning, LUKS, BTRFS, and swapfile

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${SCRIPT_DIR}/../config/config.yaml"

# securely wipe the disk
wipe_disk() {
  local disk="$1"
  echo ">>>>> Preparing to wipe $disk..."

  # unmount everything just in case
  umount -R /mnt 2>/dev/null || true
  swapoff -a 2>/dev/null || true
  cryptsetup close cryptroot 2>/dev/null || true
  cryptsetup close container 2>/dev/null || true

  echo ""
  echo "Select wipe method for $disk:"
  echo "1) Quick Wipe (Zap partition table only)"
  echo "2) Secure Wipe (Fill with random data - Slow, best for encryption)"
  echo "3) SSD Discard (blkdiscard - Fast, leaks usage patterns)"
  echo "4) Skip Wipe"

  if [[ -n "${WIPE_METHOD:-}" ]]; then
    echo ">>>>> Using pre-selected wipe method from environment: $WIPE_METHOD"
    choice="$WIPE_METHOD"
  else
    read -rp "Enter choice [1-4]: " choice
  fi

  case $choice in
    2)
      echo ">>>>> Filling disk with random data (via temporary LUKS container)..."
      # open disk with a random key
      cryptsetup open --type plain --key-file /dev/random "$disk" container
      # fill with zeros (which become random on disk)
      dd if=/dev/zero of=/dev/mapper/container bs=1M status=progress || true
      # close container
      cryptsetup close container
      ;;
    3)
      echo ">>>>> Discarding blocks..."
      blkdiscard -f "$disk" || echo "blkdiscard failed."
      ;;
    4)
      echo ">>>>> Skipping wipe."
      return 0
      ;;
    *)
      echo ">>>>> Performing quick partition table wipe..."
      ;;
  esac

  # always zap the partition table to ensure a clean slate
  sgdisk -Z "$disk"
  wipefs -a "$disk"
  partprobe "$disk"
  sleep 2
}

# function to converge storage configuration
# this script handles disk partitioning, LUKS encryption, BTRFS formatting, and subvolume creation.
# it attempts to be idempotent by checking if the device is already set up.
setup_storage() {
  local disk="$1"
  local part_efi="${disk}1"
  local part_root="${disk}2"
  # handle NVMe and Loop naming convention (e.g., /dev/nvme0n1 -> /dev/nvme0n1p1, /dev/loop0 -> /dev/loop0p1)
  if [[ "$disk" == *"nvme"* ]] || [[ "$disk" == *"loop"* ]]; then
    part_efi="${disk}p1"
    part_root="${disk}p2"
  fi

  echo ">>>>> Converging storage on $disk..."

  # check if partitions exist
  if ! lsblk "$part_efi" >/dev/null 2>&1 || ! lsblk "$part_root" >/dev/null 2>&1; then
      echo "    Partitions not found. Partitioning $disk..."

      # close any stale LUKS mapping before repartitioning
      if cryptsetup status cryptroot >/dev/null 2>&1; then
          echo "    Closing stale LUKS mapping before repartitioning..."
          umount -R /mnt 2>/dev/null || true
          swapoff -a 2>/dev/null || true
          cryptsetup close cryptroot 2>/dev/null || true
      fi

      # --- DOCKER/CI SPECIFIC START ---
      # in Docker/QEMU environments using loop devices, there can be a race condition
      # where the kernel hasn't fully registered the device size before we try to partition it.
      if [[ "$disk" == *"loop"* ]]; then
          echo "    [CI] Syncing loop device..."
          # verify size
          local size
          size=$(blockdev --getsize64 "$disk" 2>/dev/null || echo "0")
          echo "    [CI] Device size: $size bytes"
          if [[ "$size" == "0" ]]; then
              # try to refresh the loop device
              echo "    [CI] Device appears empty, attempting to refresh..."
              # list all loop devices to debug
              losetup -a || true
              # try losetup -c to update capacity
              losetup -c "$disk" 2>/dev/null || true
              sleep 1
              size=$(blockdev --getsize64 "$disk" 2>/dev/null || echo "0")
              echo "    [CI] Device size after refresh: $size bytes"
              if [[ "$size" == "0" ]]; then
                  echo "    Error: Loop device has 0 size. Cannot partition."
                  exit 1
              fi
          fi
      fi
      blockdev --rereadpt "$disk" 2>/dev/null || true
      sleep 1
      # --- DOCKER/CI SPECIFIC END ---

      sgdisk -Z "$disk"

      # get EFI size from test override or config (no hardcoded default)
      local efi_size_mb
      if [[ -n "${TEST_EFI_SIZE_MB:-}" ]]; then
          efi_size_mb="$TEST_EFI_SIZE_MB"
      elif [ -f "$CFG" ]; then
          efi_size_mb=$(yq -r '.storage.efi_size_mb' "$CFG")
          if [[ -z "$efi_size_mb" || "$efi_size_mb" == "null" ]]; then
              echo "Error: storage.efi_size_mb not set in config"
              exit 1
          fi
      else
          echo "Error: Config file not found and TEST_EFI_SIZE_MB not set"
          exit 1
      fi
      echo "    Using EFI partition size: ${efi_size_mb}MiB"

      sgdisk -n1:0:+${efi_size_mb}M -t1:ef00 "$disk" # EFI Partition
      sgdisk -n2:0:0 -t2:8304 "$disk"      # Linux Root (x86-64) for Discoverable Partitions
      partprobe "$disk" || true
      blockdev --rereadpt "$disk" || true

      # --- DOCKER/CI SPECIFIC START ---
      # workaround for containers: create device nodes manually if udev is missing.
      # in a privileged Docker container, udev is not running, so even though the kernel
      # knows about the partitions (visible in lsblk), the device nodes in /dev/ are not created.
      if [[ "$disk" == *"loop"* ]]; then
          echo "    Ensuring loop partition nodes exist..."
          # get partitions from lsblk
          # output format: NAME MAJ:MIN
          # we need to filter for partitions of the loop device
          # lsblk -r -o NAME,MAJ:MIN,TYPE | grep "part"

          # we expect loopXpY.
          # note: lsblk output might be just "loop0p1", so we need to prepend /dev/ if missing.

          lsblk -r -n -o NAME,MAJ:MIN,TYPE "$disk" | grep "part" | while read -r name majmin type; do
              dev_node="/dev/$name"
              if [ ! -e "$dev_node" ]; then
                  echo "    Creating missing device node $dev_node ($majmin)..."
                  maj=${majmin%:*}
                  min=${majmin#*:}
                  mknod "$dev_node" b "$maj" "$min"
              fi
          done
      fi
      # --- DOCKER/CI SPECIFIC END ---

      # wait for partitions to appear
      echo "    Waiting for partitions to appear..."
      for i in {1..20}; do
          if [ -e "$part_root" ]; then
              echo "    Partition $part_root found."
              break
          fi
          sleep 1
          partprobe "$disk" || true
      done
      if [ ! -e "$part_root" ]; then
          echo "Error: Partition $part_root failed to appear."
          ls -l /dev/loop*
          lsblk
          exit 1
      fi
  else
      echo "    Partitions already exist on $disk."
  fi

  # check if LUKS is already open
  if cryptsetup status cryptroot >/dev/null 2>&1; then
      # Verify the existing cryptroot points to our expected partition
      local current_device
      current_device=$(cryptsetup status cryptroot 2>/dev/null | grep "device:" | awk '{print $2}')

      if [[ "$current_device" == "$part_root" ]]; then
          echo "    LUKS volume 'cryptroot' is already open on $part_root."
      else
          # This shouldn't happen if the earlier cleanup worked
          echo "    ERROR: cryptroot is open but points to wrong device: $current_device (expected $part_root)"
          echo "    Please restart Docker Desktop and try again."
          exit 1
      fi
  else
      # use LUKS_PASSWORD from environment (set in install.sh initial_setup)
      local luks_pass="${LUKS_PASSWORD:-}"

      if cryptsetup isLuks "$part_root"; then
          echo "    Opening existing LUKS volume..."
          if [[ -n "$luks_pass" ]]; then
              echo -n "$luks_pass" | cryptsetup open --key-file - "$part_root" cryptroot
          else
              cryptsetup open "$part_root" cryptroot
          fi
      else
          echo "    Formatting LUKS volume..."
          if [[ -n "$luks_pass" ]]; then
              echo -n "$luks_pass" | cryptsetup luksFormat --batch-mode \
                --type luks2 \
                --pbkdf argon2id \
                --pbkdf-memory 1048576 \
                --pbkdf-parallel 4 \
                --iter-time 4000 \
                --key-file - \
                "$part_root"
              echo -n "$luks_pass" | cryptsetup open --key-file - "$part_root" cryptroot
          else
              cryptsetup luksFormat \
                --type luks2 \
                --pbkdf argon2id \
                --pbkdf-memory 1048576 \
                --pbkdf-parallel 4 \
                --iter-time 4000 \
                "$part_root"
              cryptsetup open "$part_root" cryptroot
          fi
      fi
  fi

  # check BTRFS
  if ! blkid /dev/mapper/cryptroot | grep -q "TYPE=\"btrfs\""; then
      echo "    Formatting BTRFS..."
      local btrfs_label
      btrfs_label=$(yq -r '.storage.btrfs.label // "archroot"' "$CFG" 2>/dev/null || echo "archroot")
      mkfs.btrfs -L "$btrfs_label" /dev/mapper/cryptroot
  else
      echo "    BTRFS filesystem detected."
  fi

  # mount root to create subvolumes
  if ! mountpoint -q /mnt; then
      mount /dev/mapper/cryptroot /mnt
  fi

  # read subvolume definitions from config
  # format: "subvolume_name:mountpoint:nocow_flag"
  local subvol_defs=()
  local subvol_count
  subvol_count=$(yq -r '.storage.btrfs.subvolumes | length' "$CFG" 2>/dev/null || echo "0")

  if [[ "$subvol_count" -gt 0 ]]; then
      echo "    Reading subvolume definitions from config..."
      for ((i=0; i<subvol_count; i++)); do
          local sv_name sv_mount sv_nocow
          sv_name=$(yq -r ".storage.btrfs.subvolumes[$i].name" "$CFG")
          sv_mount=$(yq -r ".storage.btrfs.subvolumes[$i].mountpoint" "$CFG")
          sv_nocow=$(yq -r ".storage.btrfs.subvolumes[$i].nocow // false" "$CFG")
          if [[ "$sv_nocow" == "true" ]]; then
              subvol_defs+=("${sv_name}:${sv_mount}:nocow")
          else
              subvol_defs+=("${sv_name}:${sv_mount}:")
          fi
      done
  else
      echo "    Warning: No subvolumes defined in config, using defaults..."
      subvol_defs=(
        "@:/:"
        "@home:/home:"
        "@home-snapshots:/home/.snapshots:"
        "@srv:/srv:"
        "@var:/var:nocow"
        "@var-log:/var/log:"
        "@cache-pacman-pkgs:/var/cache/pacman/pkg:"
        "@var-tmp:/var/tmp:"
        "@snapshots:/.snapshots:"
        "@swap:/.swap:nocow"
        "@docker:/var/lib/docker:nocow"
        "@libvirt:/var/lib/libvirt:nocow"
      )
  fi

  # create subvolumes if they don't exist
  for def in "${subvol_defs[@]}"; do
    local sv="${def%%:*}"
    if [ ! -d "/mnt/$sv" ]; then
        echo "    Creating subvolume $sv..."
        btrfs subvolume create "/mnt/$sv"
    else
        echo "    Subvolume $sv exists."
    fi
  done

  # unmount to remount with correct subvolumes
  umount /mnt

  # read mount options from config
  local mount_opts
  mount_opts=$(yq -r '.storage.btrfs.mount_options // "compress=zstd,noatime"' "$CFG" 2>/dev/null || echo "compress=zstd,noatime")

  # mount @ (root)
  echo "    Mounting @ to /mnt..."
  mount -o "subvol=@,$mount_opts" /dev/mapper/cryptroot /mnt

  # create top-level mountpoints first
  mkdir -p /mnt/{home,srv,var,efi,boot,.snapshots,.swap}

  # mount subvolumes in order: parent directories first, then nested ones
  echo "    Mounting subvolumes..."

  # first tier: direct children of root
  mount -o "subvol=@home,$mount_opts" /dev/mapper/cryptroot /mnt/home
  mount -o "subvol=@srv,$mount_opts" /dev/mapper/cryptroot /mnt/srv
  mount -o "subvol=@var,$mount_opts" /dev/mapper/cryptroot /mnt/var
  mount -o "subvol=@snapshots,$mount_opts" /dev/mapper/cryptroot /mnt/.snapshots
  mount -o "subvol=@swap,$mount_opts" /dev/mapper/cryptroot /mnt/.swap

  # create nested mount points AFTER parent subvolumes are mounted
  mkdir -p /mnt/home/.snapshots
  mkdir -p /mnt/var/{log,tmp,cache/pacman/pkg,lib/docker,lib/libvirt}

  # second tier: nested subvolumes
  mount -o "subvol=@home-snapshots,$mount_opts" /dev/mapper/cryptroot /mnt/home/.snapshots
  mount -o "subvol=@var-log,$mount_opts" /dev/mapper/cryptroot /mnt/var/log
  mount -o "subvol=@cache-pacman-pkgs,$mount_opts" /dev/mapper/cryptroot /mnt/var/cache/pacman/pkg
  mount -o "subvol=@var-tmp,$mount_opts" /dev/mapper/cryptroot /mnt/var/tmp
  mount -o "subvol=@docker,$mount_opts" /dev/mapper/cryptroot /mnt/var/lib/docker
  mount -o "subvol=@libvirt,$mount_opts" /dev/mapper/cryptroot /mnt/var/lib/libvirt

  # disable Copy-on-Write for specific subvolumes (improves performance for databases, VMs, swap)
  # this must be done AFTER mounting but BEFORE any files are written
  echo "    Disabling CoW for performance-sensitive directories..."
  chattr +C /mnt/var 2>/dev/null || echo "    Warning: Could not set nocow on /var"
  chattr +C /mnt/.swap 2>/dev/null || echo "    Warning: Could not set nocow on /.swap"
  chattr +C /mnt/var/lib/docker 2>/dev/null || echo "    Warning: Could not set nocow on /var/lib/docker"
  chattr +C /mnt/var/lib/libvirt 2>/dev/null || echo "    Warning: Could not set nocow on /var/lib/libvirt"

  # format and Mount EFI
  if ! blkid "$part_efi" | grep -q "TYPE=\"vfat\""; then
      echo "    Formatting EFI partition..."
      mkfs.vfat -F32 -n EFI "$part_efi"
  fi

  echo "    Mounting EFI to /mnt/efi..."
  mount "$part_efi" /mnt/efi

  # create swapfile - use SWAP_SIZE_MB from environment (set in install.sh)
  local swap_size_mb="${SWAP_SIZE_MB:-0}"
  local swap_path="/mnt/.swap/swapfile"

  # override from TEST_SWAP_SIZE_MB for testing
  if [[ -n "${TEST_SWAP_SIZE_MB:-}" ]]; then
      swap_size_mb="$TEST_SWAP_SIZE_MB"
  fi

  # sKIP_SWAP env var disables swap
  if [[ "${SKIP_SWAP:-false}" == "true" ]]; then
      swap_size_mb=0
  fi

  # fall back to config if not set
  if [[ "$swap_size_mb" -eq 0 ]] && [[ "${SKIP_SWAP:-false}" != "true" ]]; then
      if [ -f "$CFG" ]; then
          local cfg_enabled
          cfg_enabled=$(yq -r '.storage.swap.enabled // true' "$CFG" 2>/dev/null || echo "true")
          if [[ "$cfg_enabled" == "true" ]]; then
              swap_size_mb=$(yq -r '.storage.swap.size_mb // 8192' "$CFG" 2>/dev/null || echo "8192")
          fi
      fi
  fi

  if [ ! -f "$swap_path" ]; then
      if [[ "$swap_size_mb" -gt 0 ]]; then
          echo "    Creating ${swap_size_mb}MB swapfile..."
          truncate -s 0 "$swap_path"
          chattr +C "$swap_path" 2>/dev/null || true
          btrfs property set "$swap_path" compression none 2>/dev/null || true
          fallocate -l "${swap_size_mb}M" "$swap_path" || dd if=/dev/zero of="$swap_path" bs=1M count="$swap_size_mb" status=progress
          chmod 600 "$swap_path"
          mkswap "$swap_path"
          swapon "$swap_path"
          echo "    Swapfile created and activated at $swap_path"
      else
          echo "    Swapfile disabled."
      fi
  else
      echo "    Swapfile already exists."
      # ensure swap is active for genfstab to detect it
      if ! swapon --show | grep -q "$swap_path"; then
          swapon "$swap_path" 2>/dev/null || true
      fi
  fi
}
