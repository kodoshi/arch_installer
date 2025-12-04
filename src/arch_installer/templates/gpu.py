"""GPU-related configuration templates."""

NVIDIA_MODPROBE_CONF = "options nvidia_drm modeset=1 fbdev=1"


NVIDIA_PACMAN_HOOK = """[Trigger]
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
"""
