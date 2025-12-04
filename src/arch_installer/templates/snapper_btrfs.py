"""Templates for snapper BTRFS snapshot configuration."""


def snapper_volume_config(
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
    """Generate a snapper config file for a BTRFS subvolume."""
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


SNAPPER_CONFIGS_LIST = 'SNAPPER_CONFIGS="root home"\n'


SNAP_PAC_ROOT_CONFIG = """[root]
snapshot = yes
cleanup = timeline
"""


SNAPPER_BOOT_ENTRIES_REFRESH_SERVICE = """[Unit]
Description=Refresh systemd-boot entries for BTRFS snapshots
After=snapper-cleanup.service snapper-timeline.service

[Service]
Type=oneshot
ExecStart=/etc/snapper/scripts/refresh-boot-entries.sh refresh 5
"""


SNAPPER_BOOT_ENTRIES_WATCH_PATH = """[Unit]
Description=Watch for snapper snapshot changes

[Path]
PathChanged=/.snapshots
Unit=snapper-boot-entries.service

[Install]
WantedBy=multi-user.target
"""


SNAPPER_UKI_REFRESH_PACMAN_HOOK = """[Trigger]
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


SNAPPER_NOTIFY_SERVICE = """[Unit]
Description=Notify on snapper snapshot creation
After=snapper-timeline.timer snap-pac.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/snapper-notify
"""


SNAPPER_NOTIFY_PATH = """[Unit]
Description=Watch for new snapshots and notify

[Path]
PathChanged=/.snapshots
Unit=snapper-notify.service

[Install]
WantedBy=multi-user.target
"""


SNAPPER_NOTIFY_SCRIPT = """#!/bin/bash
# Desktop notification for snapper snapshot creation
# Requires: libnotify (notify-send)

set -euo pipefail

SNAPSHOT_DIR="/.snapshots"
LAST_NOTIFIED_FILE="/tmp/snapper-last-notified"

latest_snapshot=$(ls -1t "$SNAPSHOT_DIR" 2>/dev/null | head -n1)
[[ -z "$latest_snapshot" ]] && exit 0

# avoid duplicate notifications
if [[ -f "$LAST_NOTIFIED_FILE" ]]; then
    last_notified=$(cat "$LAST_NOTIFIED_FILE")
    [[ "$latest_snapshot" == "$last_notified" ]] && exit 0
fi

# get snapshot info
info_file="$SNAPSHOT_DIR/$latest_snapshot/info.xml"
if [[ -f "$info_file" ]]; then
    description=$(grep -oP '(?<=<description>).*(?=</description>)' "$info_file" 2>/dev/null || echo "")
    snap_type=$(grep -oP '(?<=<type>).*(?=</type>)' "$info_file" 2>/dev/null || echo "single")
else
    description=""
    snap_type="single"
fi

# send notification to logged-in user
for uid in $(loginctl list-users --no-legend | awk '{print $1}'); do
    user=$(id -nu "$uid" 2>/dev/null || continue)
    export DISPLAY=":0"
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus"

    title="Snapshot #$latest_snapshot created"
    body="${description:-$snap_type snapshot}"

    sudo -u "$user" notify-send -i drive-harddisk -a "Snapper" "$title" "$body" 2>/dev/null || true
done

echo "$latest_snapshot" > "$LAST_NOTIFIED_FILE"
"""


# backwards compatibility aliases
snapper_config = snapper_volume_config
SNAPPER_CONFIGS_CONF = SNAPPER_CONFIGS_LIST
SNAP_PAC_ROOT_CONF = SNAP_PAC_ROOT_CONFIG
SNAPPER_BOOT_ENTRIES_SERVICE = SNAPPER_BOOT_ENTRIES_REFRESH_SERVICE
SNAPPER_BOOT_ENTRIES_PATH = SNAPPER_BOOT_ENTRIES_WATCH_PATH
SNAPPER_PACMAN_HOOK = SNAPPER_UKI_REFRESH_PACMAN_HOOK
