"""Snapper configuration templates."""


def snapper_config(
    subvolume: str,
    allow_groups: str,
    number_limit: int,
    number_limit_important: int,
    hourly: int,
    daily: int,
    weekly: int,
    monthly: int,
    yearly: int,
) -> str:
    return f"""# snapper config for {subvolume}
# created by arch_installer (D-Bus not available in chroot)

SUBVOLUME="{subvolume}"
FSTYPE="btrfs"
ALLOW_USERS=""
ALLOW_GROUPS="{allow_groups}"
SYNC_ACL="yes"
BACKGROUND_COMPARISON="yes"

NUMBER_CLEANUP="yes"
NUMBER_MIN_AGE="1800"
NUMBER_LIMIT="{number_limit}"
NUMBER_LIMIT_IMPORTANT="{number_limit_important}"

TIMELINE_CREATE="yes"
TIMELINE_CLEANUP="yes"
TIMELINE_MIN_AGE="1800"
TIMELINE_LIMIT_HOURLY="{hourly}"
TIMELINE_LIMIT_DAILY="{daily}"
TIMELINE_LIMIT_WEEKLY="{weekly}"
TIMELINE_LIMIT_MONTHLY="{monthly}"
TIMELINE_LIMIT_YEARLY="{yearly}"

EMPTY_PRE_POST_CLEANUP="yes"
EMPTY_PRE_POST_MIN_AGE="1800"
"""


SNAPPER_CONFIGS_CONF = 'SNAPPER_CONFIGS="root home"\n'


SNAP_PAC_ROOT_CONF = """[root]
snapshot = yes
cleanup = timeline
"""


SNAPPER_BOOT_ENTRIES_SERVICE = """[Unit]
Description=Refresh systemd-boot entries for BTRFS snapshots
After=snapper-cleanup.service snapper-timeline.service

[Service]
Type=oneshot
ExecStart=/etc/snapper/scripts/refresh-boot-entries.sh refresh 5
"""


SNAPPER_BOOT_ENTRIES_PATH = """[Unit]
Description=Watch for snapper snapshot changes

[Path]
PathChanged=/.snapshots
Unit=snapper-boot-entries.service

[Install]
WantedBy=multi-user.target
"""
