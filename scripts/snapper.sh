#!/usr/bin/env bash
# snapper configuration for automated BTRFS snapshots
# configures root and home snapshots with timeline-based retention
# note: We create configs manually because snapper create-config requires D-Bus

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${SCRIPT_DIR}/../config/config.yaml"

setup_snapper() {
    echo ">>>>> Configuring snapper..."

    # read allow_groups from config
    local allow_groups
    allow_groups=$(yq -r '.snapper.allow_groups | join(",")' "$CFG" 2>/dev/null || echo "wheel")
    if [[ -z "$allow_groups" || "$allow_groups" == "null" ]]; then
        allow_groups="wheel"
    fi
    echo "    Allow groups: $allow_groups"

    # read retention settings from config
    local root_hourly root_daily root_weekly root_monthly root_yearly
    root_hourly=$(yq -r '.snapper.root.retention.hourly // 5' "$CFG" 2>/dev/null || echo "5")
    root_daily=$(yq -r '.snapper.root.retention.daily // 7' "$CFG" 2>/dev/null || echo "7")
    root_weekly=$(yq -r '.snapper.root.retention.weekly // 4' "$CFG" 2>/dev/null || echo "4")
    root_monthly=$(yq -r '.snapper.root.retention.monthly // 6' "$CFG" 2>/dev/null || echo "6")
    root_yearly=$(yq -r '.snapper.root.retention.yearly // 2' "$CFG" 2>/dev/null || echo "2")

    local home_hourly home_daily home_weekly home_monthly home_yearly
    home_hourly=$(yq -r '.snapper.home.retention.hourly // 5' "$CFG" 2>/dev/null || echo "5")
    home_daily=$(yq -r '.snapper.home.retention.daily // 7' "$CFG" 2>/dev/null || echo "7")
    home_weekly=$(yq -r '.snapper.home.retention.weekly // 4' "$CFG" 2>/dev/null || echo "4")
    home_monthly=$(yq -r '.snapper.home.retention.monthly // 3' "$CFG" 2>/dev/null || echo "3")
    home_yearly=$(yq -r '.snapper.home.retention.yearly // 1' "$CFG" 2>/dev/null || echo "1")

    # ensure snapper is installed
    if ! arch-chroot /mnt pacman -Q snapper &>/dev/null; then
        echo "    Installing snapper and snap-pac..."
        arch-chroot /mnt pacman -S --noconfirm snapper snap-pac
    fi

    # create snapper config directories
    mkdir -p /mnt/etc/snapper/configs
    mkdir -p /mnt/etc/conf.d

    # read mount options from config
    local mount_opts
    mount_opts=$(yq -r '.storage.btrfs.mount_options // "compress=zstd,noatime"' "$CFG" 2>/dev/null || echo "compress=zstd,noatime")

    # root Configuration
    local root_config="/mnt/etc/snapper/configs/root"

    if [ ! -f "$root_config" ]; then
        echo "    Creating snapper config for root..."

        # handle .snapshots directory/subvolume
        if mountpoint -q /mnt/.snapshots 2>/dev/null; then
            umount /mnt/.snapshots
        fi

        # remove any auto-created .snapshots subvolume (snapper would create this)
        if [ -d /mnt/.snapshots ]; then
            # check if it's a subvolume
            if btrfs subvolume show /mnt/.snapshots &>/dev/null; then
                btrfs subvolume delete /mnt/.snapshots 2>/dev/null || true
            else
                rmdir /mnt/.snapshots 2>/dev/null || true
            fi
        fi

        # create .snapshots directory and mount our pre-created subvolume
        mkdir -p /mnt/.snapshots
        mount -o "subvol=@snapshots,$mount_opts" /dev/mapper/cryptroot /mnt/.snapshots

        # set permissions
        chmod 750 /mnt/.snapshots
    else
        echo "    Root config already exists."
    fi

    echo "    Configuring root snapshot settings..."
    cat > "$root_config" <<EOF
# snapper config for root filesystem
# created manually (D-Bus not available in chroot)

SUBVOLUME="/"
FSTYPE="btrfs"
ALLOW_USERS=""
ALLOW_GROUPS="$allow_groups"
SYNC_ACL="yes"
BACKGROUND_COMPARISON="yes"

NUMBER_CLEANUP="yes"
NUMBER_MIN_AGE="1800"
NUMBER_LIMIT="10"
NUMBER_LIMIT_IMPORTANT="5"

TIMELINE_CREATE="yes"
TIMELINE_CLEANUP="yes"
TIMELINE_MIN_AGE="1800"
TIMELINE_LIMIT_HOURLY="$root_hourly"
TIMELINE_LIMIT_DAILY="$root_daily"
TIMELINE_LIMIT_WEEKLY="$root_weekly"
TIMELINE_LIMIT_MONTHLY="$root_monthly"
TIMELINE_LIMIT_YEARLY="$root_yearly"

EMPTY_PRE_POST_CLEANUP="yes"
EMPTY_PRE_POST_MIN_AGE="1800"
EOF

    # home Configuration
    local home_config="/mnt/etc/snapper/configs/home"

    if [ ! -f "$home_config" ]; then
        echo "    Creating snapper config for home..."

        # handle home/.snapshots directory/subvolume
        if mountpoint -q /mnt/home/.snapshots 2>/dev/null; then
            umount /mnt/home/.snapshots
        fi

        # remove any existing .snapshots in home
        if [ -d /mnt/home/.snapshots ]; then
            if btrfs subvolume show /mnt/home/.snapshots &>/dev/null; then
                btrfs subvolume delete /mnt/home/.snapshots 2>/dev/null || true
            else
                rmdir /mnt/home/.snapshots 2>/dev/null || true
            fi
        fi

        # create .snapshots directory and mount our pre-created subvolume
        mkdir -p /mnt/home/.snapshots
        mount -o "subvol=@home-snapshots,$mount_opts" /dev/mapper/cryptroot /mnt/home/.snapshots

        # set permissions
        chmod 750 /mnt/home/.snapshots
    else
        echo "    Home config already exists."
    fi

    echo "    Configuring home snapshot settings..."
    cat > "$home_config" <<EOF
# snapper config for home directory
# created manually (D-Bus not available in chroot)

SUBVOLUME="/home"
FSTYPE="btrfs"
ALLOW_USERS=""
ALLOW_GROUPS="$allow_groups"
SYNC_ACL="yes"
BACKGROUND_COMPARISON="yes"

NUMBER_CLEANUP="yes"
NUMBER_MIN_AGE="1800"
NUMBER_LIMIT="5"
NUMBER_LIMIT_IMPORTANT="3"

TIMELINE_CREATE="yes"
TIMELINE_CLEANUP="yes"
TIMELINE_MIN_AGE="1800"
TIMELINE_LIMIT_HOURLY="$home_hourly"
TIMELINE_LIMIT_DAILY="$home_daily"
TIMELINE_LIMIT_WEEKLY="$home_weekly"
TIMELINE_LIMIT_MONTHLY="$home_monthly"
TIMELINE_LIMIT_YEARLY="$home_yearly"

EMPTY_PRE_POST_CLEANUP="yes"
EMPTY_PRE_POST_MIN_AGE="1800"
EOF

    # register snapper configs in /etc/conf.d/snapper
    echo "    Registering snapper configs..."
    cat > /mnt/etc/conf.d/snapper <<'EOF'
SNAPPER_CONFIGS="root home"
EOF

    # enable timers
    echo "    Enabling snapper timers..."
    arch-chroot /mnt systemctl enable snapper-timeline.timer
    arch-chroot /mnt systemctl enable snapper-cleanup.timer

    # configure snap-pac
    echo "    Configuring snap-pac hooks..."
    mkdir -p /mnt/etc/snap-pac.d

    cat > /mnt/etc/snap-pac.d/root.conf <<'EOF'
[root]
snapshot = yes
cleanup = timeline
EOF

    # install snapshot boot entry refresh hook (only if enabled)
    local enable_snapshot_boot="${ENABLE_SNAPSHOT_BOOT:-false}"

    if [[ "$enable_snapshot_boot" == "true" ]]; then
        echo "    Installing bootable snapshot support..."
        mkdir -p /mnt/etc/snapper/scripts

        if [ -f "scripts/manage_snapshot_entries.sh" ]; then
            cp scripts/manage_snapshot_entries.sh /mnt/etc/snapper/scripts/refresh-boot-entries.sh
            chmod +x /mnt/etc/snapper/scripts/refresh-boot-entries.sh
        fi

        cat > /mnt/etc/systemd/system/snapper-boot-entries.service <<'EOF'
[Unit]
Description=Refresh systemd-boot entries for BTRFS snapshots
After=snapper-cleanup.service snapper-timeline.service

[Service]
Type=oneshot
ExecStart=/etc/snapper/scripts/refresh-boot-entries.sh refresh 5
EOF

        cat > /mnt/etc/systemd/system/snapper-boot-entries.path <<'EOF'
[Unit]
Description=Watch for snapper snapshot changes

[Path]
PathChanged=/.snapshots
Unit=snapper-boot-entries.service

[Install]
WantedBy=multi-user.target
EOF

        arch-chroot /mnt systemctl enable snapper-boot-entries.path
        echo "    Bootable snapshots enabled."
    else
        echo "    Bootable snapshots disabled (snapshots will be created but not bootable)."
    fi

    echo ">>>>> Snapper configuration complete."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    setup_snapper
fi
