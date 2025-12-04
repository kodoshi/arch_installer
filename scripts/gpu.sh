#!/usr/bin/env bash
set -Eeuo pipefail

# function to converge GPU drivers
# configures GPU-specific settings based on selection from install.sh
# note: GPU packages are installed in packages.sh
setup_gpu() {
  local cfg="config/config.yaml"
  echo ">>>>> Converging GPU configuration..."

  # use environment variable set by install.sh, fallback to config
  local gpu_vendor="${GPU_VENDOR:-}"
  local gpu_driver="${GPU_DRIVER:-}"

  if [[ -z "$gpu_vendor" ]]; then
      # fallback to config file
      gpu_vendor=$(yq -r '.gpu.vendor // "none"' "$cfg" 2>/dev/null || echo "none")
      gpu_driver=$(yq -r '.gpu.driver // ""' "$cfg" 2>/dev/null || echo "")
  fi

  echo "    GPU vendor: $gpu_vendor"
  [[ -n "$gpu_driver" ]] && echo "    GPU driver: $gpu_driver"

  if [[ "$gpu_vendor" == "none" ]]; then
      echo "    No GPU-specific configuration needed."
      return 0
  fi

  # configure NVIDIA-specific settings
  if [[ "$gpu_vendor" == "nvidia" && "$gpu_driver" != "nouveau" ]]; then
      echo "    Configuring NVIDIA settings..."

      # enable DRM kernel mode setting for NVIDIA
      local mkinitcpio_conf="/mnt/etc/mkinitcpio.conf"
      if [[ -f "$mkinitcpio_conf" ]] && ! grep -q "nvidia" "$mkinitcpio_conf"; then
          echo "    Adding nvidia modules to initramfs..."
          sed -i 's/^MODULES=(\(.*\))/MODULES=(\1 nvidia nvidia_modeset nvidia_uvm nvidia_drm)/' "$mkinitcpio_conf"
      fi

      # create modprobe config for DRM
      echo "    Enabling NVIDIA DRM modeset..."
      mkdir -p /mnt/etc/modprobe.d
      echo "options nvidia_drm modeset=1 fbdev=1" > /mnt/etc/modprobe.d/nvidia.conf

      # pacman hook to rebuild initramfs on NVIDIA updates
      echo "    Installing NVIDIA pacman hook..."
      mkdir -p /mnt/etc/pacman.d/hooks
      cat > /mnt/etc/pacman.d/hooks/nvidia.hook <<'EOF'
[Trigger]
Operation=Install
Operation=Upgrade
Operation=Remove
Type=Package
Target=nvidia
Target=nvidia-dkms
Target=nvidia-open
Target=linux
Target=linux-lts
Target=linux-hardened

[Action]
Description=Rebuilding initramfs after NVIDIA driver update...
Depends=mkinitcpio
When=PostTransaction
NeedsTargets
Exec=/bin/sh -c 'while read -r trg; do case $trg in linux*) exit 0; esac; done; /usr/bin/mkinitcpio -P'
EOF
  fi

  # configure AMD-specific settings
  if [[ "$gpu_vendor" == "amd" ]]; then
      echo "    AMD GPU uses open-source drivers, no special configuration needed."
  fi

  # configure Intel-specific settings
  if [[ "$gpu_vendor" == "intel" ]]; then
      echo "    Intel GPU uses open-source drivers, no special configuration needed."
  fi

  echo ">>>>> GPU configuration complete."
}
