"""SSH configuration commands for QEMU test VMs.

these commands are run on the live ISO environment to configure SSH access
in the installed system before rebooting into it.
"""

# commands to configure SSH for post-reboot access in installed system
# run these before rebooting from live ISO into installed system
SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM = [
    # enable root login via SSH for testing
    "mkdir -p /mnt/etc/ssh/sshd_config.d",
    "echo 'PermitRootLogin yes' > /mnt/etc/ssh/sshd_config.d/99-test-root-login.conf",
    # set root password
    "arch-chroot /mnt bash -c 'echo root:root | chpasswd'",
    # enable sshd service
    "arch-chroot /mnt systemctl enable sshd",
    # configure network manager connection
    "mkdir -p /mnt/etc/NetworkManager/system-connections",
    """cat > /mnt/etc/NetworkManager/system-connections/wired.nmconnection << 'EOF'
[connection]
id=Wired
type=ethernet
autoconnect=true

[ethernet]

[ipv4]
method=auto

[ipv6]
method=auto
EOF""",
    "chmod 600 /mnt/etc/NetworkManager/system-connections/wired.nmconnection",
    # add serial console to kernel cmdline for debugging
    "for f in /mnt/etc/kernel/cmdline*; do " "sed -i 's/$/ console=ttyS0,115200/' \"$f\"; done",
    # regenerate initramfs with updated cmdline
    "arch-chroot /mnt mkinitcpio -P",
]
