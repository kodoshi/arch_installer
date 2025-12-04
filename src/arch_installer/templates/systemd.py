"""Systemd and pacman hook templates."""

SNAPPER_PACMAN_HOOK = """[Trigger]
Operation=Upgrade
Operation=Install
Operation=Remove
Type=Path
Target=usr/lib/modules/*/vmlinuz

[Action]
Depends=coreutils
Description=Refreshing snapshot UKIs after kernel update
When=PostTransaction
Exec=/usr/local/bin/refresh-snapshot-ukis
"""
