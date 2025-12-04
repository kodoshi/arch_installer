#!/usr/bin/env bash
set -Eeuo pipefail

# mkinitcpio configuration and UKI generation

# build kernel command line with LUKS and hardening parameters
build_cmdline() {
  local cfg_boot="$1"
  local cmdline=""

  # === LUKS Parameters (CRITICAL for encrypted root) ===
  # get the LUKS partition UUID - try multiple methods
  local luks_uuid=""

  # method 1: From the open cryptroot device
  if [[ -e /dev/mapper/cryptroot ]]; then
      # get the underlying device of cryptroot
      local backing_device
      backing_device=$(cryptsetup status cryptroot 2>/dev/null | grep "device:" | awk '{print $2}')
      if [[ -n "$backing_device" ]]; then
          luks_uuid=$(blkid -s UUID -o value "$backing_device" 2>/dev/null || true)
      fi
  fi

  # method 2: Look for LUKS partition by type
  if [[ -z "$luks_uuid" ]]; then
      luks_uuid=$(blkid -t TYPE=crypto_LUKS -s UUID -o value 2>/dev/null | head -1 || true)
  fi

  # method 3: Check if SELECTED_DISK is set and get partition 2
  if [[ -z "$luks_uuid" && -n "${SELECTED_DISK:-}" ]]; then
      local part_root="${SELECTED_DISK}2"
      if [[ "$SELECTED_DISK" == *"nvme"* ]] || [[ "$SELECTED_DISK" == *"loop"* ]]; then
          part_root="${SELECTED_DISK}p2"
      fi
      if [[ -e "$part_root" ]]; then
          luks_uuid=$(blkid -s UUID -o value "$part_root" 2>/dev/null || true)
      fi
  fi

  if [[ -z "$luks_uuid" ]]; then
      echo "ERROR: Could not determine LUKS partition UUID!" >&2
      echo "       Make sure the LUKS partition exists and is accessible." >&2
      exit 1
  fi

  echo "    LUKS UUID: $luks_uuid" >&2

  # add LUKS unlock parameters for systemd-cryptsetup
  cmdline="rd.luks.name=${luks_uuid}=cryptroot"

  # root device is the unlocked LUKS mapper
  cmdline="$cmdline root=/dev/mapper/cryptroot"

  # base parameters
  cmdline="$cmdline rw"

  # add rootflags for BTRFS subvolume
  local rootflags
  rootflags=$(yq -r '.boot.cmdline.rootflags // "subvol=@"' "$cfg_boot")
  cmdline="$cmdline rootflags=$rootflags"

  # quiet boot (optional)
  if [[ $(yq -r '.boot.cmdline.quiet // true' "$cfg_boot") == "true" ]]; then
    cmdline="$cmdline quiet"
  fi

  # hardening parameters (if enabled)
  local hardening_enabled
  hardening_enabled=$(yq -r '.boot.cmdline.hardening != null' "$cfg_boot")

  if [[ "$hardening_enabled" == "true" ]]; then
    # lockdown mode
    local lockdown
    lockdown=$(yq -r '.boot.cmdline.hardening.lockdown // empty' "$cfg_boot")
    [[ -n "$lockdown" ]] && cmdline="$cmdline lockdown=$lockdown"

    # iOMMU
    local iommu
    iommu=$(yq -r '.boot.cmdline.hardening.iommu // empty' "$cfg_boot")
    [[ -n "$iommu" ]] && cmdline="$cmdline iommu=$iommu"

    local intel_iommu
    intel_iommu=$(yq -r '.boot.cmdline.hardening.intel_iommu // empty' "$cfg_boot")
    [[ -n "$intel_iommu" ]] && cmdline="$cmdline intel_iommu=$intel_iommu"

    local amd_iommu
    amd_iommu=$(yq -r '.boot.cmdline.hardening.amd_iommu // empty' "$cfg_boot")
    [[ -n "$amd_iommu" ]] && cmdline="$cmdline amd_iommu=$amd_iommu"

    # pTI (Meltdown)
    local pti
    pti=$(yq -r '.boot.cmdline.hardening.pti // empty' "$cfg_boot")
    [[ -n "$pti" ]] && cmdline="$cmdline pti=$pti"

    # spectre mitigations
    local spectre_v2
    spectre_v2=$(yq -r '.boot.cmdline.hardening.spectre_v2 // empty' "$cfg_boot")
    [[ -n "$spectre_v2" ]] && cmdline="$cmdline spectre_v2=$spectre_v2"

    local spec_store_bypass
    spec_store_bypass=$(yq -r '.boot.cmdline.hardening.spec_store_bypass_disable // empty' "$cfg_boot")
    [[ -n "$spec_store_bypass" ]] && cmdline="$cmdline spec_store_bypass_disable=$spec_store_bypass"

    # l1TF
    local l1tf
    l1tf=$(yq -r '.boot.cmdline.hardening.l1tf // empty' "$cfg_boot")
    [[ -n "$l1tf" ]] && cmdline="$cmdline l1tf=$l1tf"

    # mDS
    local mds
    mds=$(yq -r '.boot.cmdline.hardening.mds // empty' "$cfg_boot")
    [[ -n "$mds" ]] && cmdline="$cmdline mds=$mds"

    # sRBDS
    local srbds
    srbds=$(yq -r '.boot.cmdline.hardening.srbds // empty' "$cfg_boot")
    [[ -n "$srbds" ]] && cmdline="$cmdline srbds=$srbds"

    # tSX
    local tsx_async_abort
    tsx_async_abort=$(yq -r '.boot.cmdline.hardening.tsx_async_abort // empty' "$cfg_boot")
    [[ -n "$tsx_async_abort" ]] && cmdline="$cmdline tsx_async_abort=$tsx_async_abort"

    # memory init
    local init_on_alloc
    init_on_alloc=$(yq -r '.boot.cmdline.hardening.init_on_alloc // empty' "$cfg_boot")
    [[ -n "$init_on_alloc" ]] && cmdline="$cmdline init_on_alloc=$init_on_alloc"

    local init_on_free
    init_on_free=$(yq -r '.boot.cmdline.hardening.init_on_free // empty' "$cfg_boot")
    [[ -n "$init_on_free" ]] && cmdline="$cmdline init_on_free=$init_on_free"

    # stack randomization
    local randomize_kstack
    randomize_kstack=$(yq -r '.boot.cmdline.hardening.randomize_kstack_offset // empty' "$cfg_boot")
    [[ -n "$randomize_kstack" ]] && cmdline="$cmdline randomize_kstack_offset=$randomize_kstack"

    # vsyscall
    local vsyscall
    vsyscall=$(yq -r '.boot.cmdline.hardening.vsyscall // empty' "$cfg_boot")
    [[ -n "$vsyscall" ]] && cmdline="$cmdline vsyscall=$vsyscall"

    # debugfs
    local debugfs
    debugfs=$(yq -r '.boot.cmdline.hardening.debugfs // empty' "$cfg_boot")
    [[ -n "$debugfs" ]] && cmdline="$cmdline debugfs=$debugfs"
  fi

  echo "$cmdline"
}

# function to converge mkinitcpio configuration and generate UKIs
# this script configures mkinitcpio to use systemd hooks, generates UKIs, and signs them with sbctl.
setup_mkinitcpio() {
  local hooks_file="config/mkinitcpio_hooks"
  local cfg_boot="config/config.yaml"
  local conf_file="/mnt/etc/mkinitcpio.conf"

  echo ">>>>> Configuring mkinitcpio..."

  # read hooks from text file (join with spaces)
  local hooks
  hooks=$(tr '\n' ' ' < "$hooks_file")
  local modules="" # Keeping modules empty as per Source B strategy

  # write mkinitcpio.conf
  cat <<EOF > "$conf_file"
# generated by arch_os_as_code
MODULES=($modules)
BINARIES=()
FILES=()
HOOKS=($hooks)
EOF

  echo "    Written $conf_file"

  # configure Presets for UKI
  mkdir -p /mnt/efi/EFI/Linux

  # use SELECTED_KERNELS from install.sh, fallback to config
  local kernels=()
  if [[ ${#SELECTED_KERNELS[@]} -gt 0 ]]; then
      for kernel in "${SELECTED_KERNELS[@]}"; do
          if [ -f "/mnt/boot/vmlinuz-${kernel}" ]; then
              kernels+=("$kernel")
              echo "    Found installed kernel: $kernel"
          else
              echo "    Skipping kernel $kernel (not installed)"
          fi
      done
  else
      # fallback to config file
      mapfile -t config_kernels < <(yq -r '.boot.kernels[].package' "$cfg_boot")
      for kernel in "${config_kernels[@]}"; do
          if [ -f "/mnt/boot/vmlinuz-${kernel}" ]; then
              kernels+=("$kernel")
              echo "    Found installed kernel: $kernel"
          else
              echo "    Skipping kernel $kernel (not installed)"
          fi
      done
  fi

  if [ ${#kernels[@]} -eq 0 ]; then
      echo ">>>>> Warning: No kernels found installed. Skipping UKI generation."
      return 0
  fi

  # build variant list based on SELECTED_DEBUG_FLAGS
  # always include "default" variant
  local variant_suffixes=("default")
  local variant_params=("")

  # read debug flag definitions from config (boot.variants)
  declare -A debug_flag_params
  local variant_count
  variant_count=$(yq -r '.boot.variants | length' "$cfg_boot" 2>/dev/null || echo "0")

  if [[ "$variant_count" -gt 0 ]]; then
      for ((i=0; i<variant_count; i++)); do
          local v_suffix v_params
          v_suffix=$(yq -r ".boot.variants[$i].suffix" "$cfg_boot")
          v_params=$(yq -r ".boot.variants[$i].params // \"\"" "$cfg_boot")
          # skip 'default' as it's already added
          if [[ "$v_suffix" != "default" && -n "$v_suffix" && "$v_suffix" != "null" ]]; then
              debug_flag_params["$v_suffix"]="$v_params"
          fi
      done
  else
      # fallback defaults if config doesn't have variants
      debug_flag_params=(
          ["no-dc"]="amdgpu.dc=0"
          ["no-runpm"]="amdgpu.runpm=0"
          ["no-dc-no-runpm"]="amdgpu.dc=0 amdgpu.runpm=0"
      )
  fi

  # add selected debug variants
  if [[ ${#SELECTED_DEBUG_FLAGS[@]} -gt 0 ]]; then
      echo "    Debug variants: ${SELECTED_DEBUG_FLAGS[*]}"
      for flag in "${SELECTED_DEBUG_FLAGS[@]}"; do
          if [[ -n "${debug_flag_params[$flag]:-}" ]]; then
              variant_suffixes+=("$flag")
              variant_params+=("${debug_flag_params[$flag]}")
          fi
      done
  else
      echo "    Debug variants: None (default UKI only)"
  fi

  # build base cmdline
  local base_cmdline
  base_cmdline=$(build_cmdline "$cfg_boot")

  # create cmdline directory
  mkdir -p /mnt/etc/kernel

  for kernel in "${kernels[@]}"; do
      local preset="/mnt/etc/mkinitcpio.d/${kernel}.preset"
      echo "    Configuring preset for $kernel..."

      # determine paths
      local kver="/boot/vmlinuz-${kernel}"

      # build preset file header
      cat <<EOF > "$preset"
# mkinitcpio preset file for $kernel (UKI)
# generated by arch_os_as_code - supports multiple variants
ALL_config="/etc/mkinitcpio.conf"
ALL_kver="$kver"
microcode=(/boot/*-ucode.img)

EOF

      # build presets array - convert hyphens to underscores for valid bash variable names
      # include 'fallback' for standalone initramfs (used by snapshot UKI generator)
      local presets_list="'fallback'"
      for i in "${!variant_suffixes[@]}"; do
          local suffix="${variant_suffixes[$i]}"
          # convert hyphens to underscores for bash variable name compatibility
          local preset_name="${suffix//-/_}"
          presets_list="$presets_list '$preset_name'"
      done

      echo "PRESETS=($presets_list)" >> "$preset"
      echo "" >> "$preset"

      # add fallback preset first - creates standalone initramfs for snapshot UKI generation
      cat <<EOF >> "$preset"
# fallback initramfs for snapshot UKI generation (not a UKI, just initramfs)
fallback_image="/boot/initramfs-${kernel}.img"

EOF

      # add each UKI variant
      for i in "${!variant_suffixes[@]}"; do
          local suffix="${variant_suffixes[$i]}"
          local extra_params="${variant_params[$i]}"
          # convert hyphens to underscores for bash variable name compatibility
          local preset_name="${suffix//-/_}"

          # uKI filename uses original suffix (hyphens are fine in filenames)
          local uki_path="/efi/EFI/Linux/arch-${kernel}-${suffix}.efi"

          # create cmdline file for this variant
          # note: cmdline_file_host is for writing, cmdline_file_chroot is for preset reference
          local cmdline_file_host="/mnt/etc/kernel/cmdline-${kernel}-${suffix}"
          local cmdline_file_chroot="/etc/kernel/cmdline-${kernel}-${suffix}"
          if [[ -n "$extra_params" && "$extra_params" != "''" ]]; then
              echo "$base_cmdline $extra_params" > "$cmdline_file_host"
          else
              echo "$base_cmdline" > "$cmdline_file_host"
          fi

          # add variant to preset (uses chroot path)
          cat <<EOF >> "$preset"
${preset_name}_uki="$uki_path"
${preset_name}_options="--splash /usr/share/systemd/bootctl/splash-arch.bmp --cmdline $cmdline_file_chroot"
EOF
      done
  done

  # write default kernel command line for tools that expect it
  echo ">>>>> Writing /mnt/etc/kernel/cmdline (default)..."
  echo "$base_cmdline" > /mnt/etc/kernel/cmdline
  echo "    Cmdline: $base_cmdline"

  # vconsole.conf should already exist (created early in install.sh)
  if [ ! -f /mnt/etc/vconsole.conf ]; then
      local keymap
      keymap=$(yq -r '.system.keymap' "$cfg_boot" 2>/dev/null || echo "us")
      if [[ -z "$keymap" || "$keymap" == "null" ]]; then
          keymap="us"
      fi
      echo "KEYMAP=$keymap" > /mnt/etc/vconsole.conf
  fi

  echo ">>>>> Preparing Secure Boot (sbctl)..."

  # check Secure Boot status
  local sb_setup_mode=false
  local sb_status=""
  sb_status=$(arch-chroot /mnt sbctl status 2>&1 || true)

  if echo "$sb_status" | grep -qi "setup mode.*enabled\|setup mode.*yes"; then
      sb_setup_mode=true
      echo "    Secure Boot is in Setup Mode - keys can be enrolled."
  else
      echo "    Note: Secure Boot is NOT in Setup Mode."
      echo "    UKIs will be generated and signed, but key enrollment will be skipped."
      echo "    "
      echo "    To complete Secure Boot setup later:"
      echo "      1. Reboot and enter BIOS/UEFI setup"
      echo "      2. Navigate to Secure Boot settings"
      echo "      3. Clear/Reset all Secure Boot keys (or enable Setup Mode)"
      echo "      4. Save and reboot into Arch"
      echo "      5. Run: sudo sbctl enroll-keys --microsoft"
      echo "    "
  fi

  # check if we're in a CI environment
  if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
      if [ ! -f /mnt/usr/share/secureboot/keys/PK/PK.key ]; then
          echo "    [CI] Creating secure boot keys..."
          arch-chroot /mnt sbctl create-keys 2>/dev/null || true
      fi
  else
      # create keys if they don't exist
      if [ ! -f /mnt/usr/share/secureboot/keys/PK/PK.key ]; then
          echo "    Creating secure boot keys..."
          if ! arch-chroot /mnt sbctl create-keys 2>&1; then
              echo "    Warning: Could not create Secure Boot keys."
              echo "    This is normal if not in Setup Mode. Keys will be created later."
          fi
      else
          echo "    Secure boot keys already exist."
      fi
  fi

  echo ">>>>> Generating UKIs..."
  arch-chroot /mnt mkinitcpio -P

  # check if keys exist before attempting to sign
  if [ ! -f /mnt/usr/share/secureboot/keys/db/db.key ]; then
      echo ">>>>> Skipping UKI signing (no Secure Boot keys available)."
      echo "    After enabling Setup Mode and creating keys, run:"
      echo "      arch-chroot /mnt sbctl create-keys"
      echo "      arch-chroot /mnt sbctl sign -s /efi/EFI/Linux/*.efi"
      echo "      arch-chroot /mnt sbctl enroll-keys --microsoft"
  else
      echo ">>>>> Signing UKIs..."

      # sign the UKIs
      for kernel in "${kernels[@]}"; do
          for suffix in "${variant_suffixes[@]}"; do
              local uki_path="/efi/EFI/Linux/arch-${kernel}-${suffix}.efi"

              if [ -f "/mnt$uki_path" ]; then
                  echo "    Signing $uki_path..."
                  arch-chroot /mnt sbctl sign -s "$uki_path" 2>/dev/null || \
                      echo "    Warning: Could not sign $uki_path"
              fi
          done
      done

      # enroll keys only if in Setup Mode
      if [[ "$sb_setup_mode" == "true" ]]; then
          echo ">>>>> Enrolling Secure Boot keys..."
          if arch-chroot /mnt sbctl enroll-keys --microsoft 2>&1; then
              echo "    Keys enrolled successfully!"
          else
              echo "    Warning: Key enrollment failed."
              echo "    You may need to manually enroll keys after reboot."
          fi
      else
          echo ">>>>> Skipping key enrollment (not in Setup Mode)."
          echo "    After enabling Setup Mode, run: sudo sbctl enroll-keys --microsoft"
      fi
  fi
}
