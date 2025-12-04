#!/usr/bin/env bash

# BTRFS Snapshot UKI Manager for systemd-boot (Secure Boot Compatible)
#
# creates signed Unified Kernel Images (UKIs) for BTRFS snapshots.
# these UKIs are self-contained and work with Secure Boot when signed.
#
# usage:
#   ./manage_snapshot_entries.sh refresh [N]  # Generate UKIs for last N snapshots (default: 3)
#   ./manage_snapshot_entries.sh list         # List current snapshot UKIs
#   ./manage_snapshot_entries.sh cleanup      # Remove all snapshot UKIs
#
# requirements:
#   - BTRFS with snapper (snapshots in /.snapshots/N/snapshot format)
#   - systemd-boot installed
#   - sbctl for Secure Boot signing (or unsigned if SB disabled)
#   - systemd-ukify for UKI generation
# =============================================================================

set -Eeuo pipefail

# require root/sudo for all operations
if [[ $EUID -ne 0 ]]; then
    echo -e "\033[0;31mError:\033[0m This script must be run with sudo or as root."
    echo "Usage: sudo $0 [command]"
    exit 1
fi

SNAPSHOTS_DIR="/.snapshots"
EFI_DIR="/efi"
UKI_DIR="${EFI_DIR}/EFI/Linux"
SNAPSHOT_UKI_PREFIX="arch-snapshot"
DEFAULT_SNAPSHOT_COUNT=7  # keep last 7 bootable snapshots

# notification settings
NOTIFY_ENABLED="${SNAPSHOT_NOTIFY:-true}"
NOTIFY_ICON_SUCCESS="drive-harddisk"
NOTIFY_ICON_ERROR="dialog-error"
NOTIFY_ICON_INFO="dialog-information"
APP_NAME="Snapshot Manager"

# read configuration
CFG="config/config.yaml"
if [ ! -f "$CFG" ]; then
    CFG="/etc/arch-install/config.yaml"
fi

# colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}>>>>>${NC} $*"; }
log_warn() { echo -e "${YELLOW}Warning:${NC} $*"; }
log_error() { echo -e "${RED}Error:${NC} $*"; }

# notification functions

# send desktop notification
notify() {
    local urgency="$1"  # low, normal, critical
    local title="$2"
    local message="$3"
    local icon="${4:-$NOTIFY_ICON_INFO}"

    if [ "$NOTIFY_ENABLED" != "true" ]; then
        return 0
    fi

    # try notify-send (works on most Linux DEs)
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" -i "$icon" -a "$APP_NAME" "$title" "$message" 2>/dev/null || true
        return 0
    fi

    # fallback: Try kdialog for KDE
    if command -v kdialog &>/dev/null; then
        case "$urgency" in
            critical) kdialog --error "$message" --title "$title" 2>/dev/null &;;
            *) kdialog --passivepopup "$message" 5 --title "$title" 2>/dev/null &;;
        esac
        return 0
    fi

    # no notification tool available - silent fail
    return 0
}

notify_success() {
    notify "normal" "$APP_NAME" "$*" "$NOTIFY_ICON_SUCCESS"
}

notify_error() {
    notify "critical" "$APP_NAME - Error" "$*" "$NOTIFY_ICON_ERROR"
}

notify_info() {
    notify "low" "$APP_NAME" "$*" "$NOTIFY_ICON_INFO"
}

# function to get the currently running kernel package name
get_running_kernel() {
    # get the running kernel version from uname
    local running_version
    running_version=$(uname -r)

    # map uname output to kernel package name
    # examples:
    #   6.17.9-arch1-1              -> linux
    #   6.17.10-hardened1-1-hardened -> linux-hardened
    #   6.12.67-1-lts                -> linux-lts

    if [[ "$running_version" == *"-hardened"* ]]; then
        echo "linux-hardened"
    elif [[ "$running_version" == *"-lts"* ]]; then
        echo "linux-lts"
    elif [[ "$running_version" == *"-zen"* ]]; then
        echo "linux-zen"
    elif [[ "$running_version" == *"-rt"* ]]; then
        echo "linux-rt"
    else
        echo "linux"
    fi
}

# function to get the kernel to use for snapshots
# priority: running kernel > first installed kernel with initramfs > config > fallback
get_default_kernel() {
    # method 1: Use the currently running kernel if it has an initramfs
    local running_kernel
    running_kernel=$(get_running_kernel)
    if [ -f "/boot/vmlinuz-${running_kernel}" ] && [ -f "/boot/initramfs-${running_kernel}.img" ]; then
        echo "$running_kernel"
        return 0
    fi

    # method 2: Detect installed kernels from /boot
    local installed_kernels=()
    for vmlinuz in /boot/vmlinuz-*; do
        if [ -f "$vmlinuz" ]; then
            local kernel_name="${vmlinuz#/boot/vmlinuz-}"
            # verify matching initramfs exists
            if [ -f "/boot/initramfs-${kernel_name}.img" ]; then
                installed_kernels+=("$kernel_name")
            fi
        fi
    done

    # return first installed kernel with initramfs
    if [ ${#installed_kernels[@]} -gt 0 ]; then
        echo "${installed_kernels[0]}"
        return 0
    fi

    # method 3: Try config file
    if [ -f "$CFG" ]; then
        local config_kernel
        config_kernel=$(yq -r '.boot.kernels[0].package // ""' "$CFG" 2>/dev/null)
        if [ -n "$config_kernel" ]; then
            echo "$config_kernel"
            return 0
        fi
    fi

    # method 4: Fallback
    echo "linux"
}

# function to check if Secure Boot signing is available
check_secure_boot_signing() {
    if command -v sbctl &>/dev/null; then
        # check if sbctl is set up with keys
        if sbctl status 2>/dev/null | grep -q "Setup Mode.*Disabled"; then
            return 0  # Signing available
        fi
    fi
    return 1  # No signing available
}

# function to sign a file with sbctl
sign_file() {
    local file="$1"
    if check_secure_boot_signing; then
        log_info "Signing $file with sbctl..."
        sbctl sign -s "$file" 2>/dev/null || {
            log_warn "Could not sign $file - Secure Boot may reject it"
            return 1
        }
        return 0
    else
        log_warn "sbctl not available or not configured - UKI will be unsigned"
        return 1
    fi
}

# function to build snapshot cmdline
# parameters: snapshot_subvol, mode (ro|rw)
build_snapshot_cmdline() {
    local snapshot_subvol="$1"
    local mode="${2:-rw}"  # Default to read-write
    local base_cmdline=""

    # priority order for getting cmdline:
    # 1. /etc/kernel/cmdline-* files (generated by mkinitcpio.sh with correct LUKS params)
    # 2. Current running kernel cmdline
    # 3. Build from scratch using LUKS UUID detection

    # method 1: Find the most recently modified cmdline file
    local cmdline_file=""
    if [ -d "/etc/kernel" ]; then
        cmdline_file=$(find /etc/kernel -name 'cmdline-*-default' -type f 2>/dev/null | head -1)
        if [ -z "$cmdline_file" ]; then
            cmdline_file=$(find /etc/kernel -name 'cmdline*' -type f 2>/dev/null | head -1)
        fi
    fi

    if [ -n "$cmdline_file" ] && [ -f "$cmdline_file" ]; then
        log_info "Using cmdline from $cmdline_file" >&2
        base_cmdline=$(cat "$cmdline_file")
    elif [ -f "/etc/kernel/cmdline" ]; then
        log_info "Using cmdline from /etc/kernel/cmdline" >&2
        base_cmdline=$(cat "/etc/kernel/cmdline")
    elif [ -f "/proc/cmdline" ]; then
        # fallback to running kernel cmdline
        log_info "Using cmdline from /proc/cmdline" >&2
        base_cmdline=$(cat /proc/cmdline)
        # remove initrd= parameter if present (UKI has embedded initrd)
        base_cmdline=$(echo "$base_cmdline" | sed 's/initrd=[^ ]*//g' | tr -s ' ')
    else
        # last resort: Build minimal cmdline with LUKS detection
        log_warn "No existing cmdline found, building from scratch" >&2

        # try to get LUKS UUID
        local luks_uuid=""
        if [ -e /dev/mapper/cryptroot ]; then
            local backing_device
            backing_device=$(cryptsetup status cryptroot 2>/dev/null | grep "device:" | awk '{print $2}')
            if [ -n "$backing_device" ]; then
                luks_uuid=$(blkid -s UUID -o value "$backing_device" 2>/dev/null || true)
            fi
        fi
        if [ -z "$luks_uuid" ]; then
            luks_uuid=$(blkid -t TYPE=crypto_LUKS -s UUID -o value 2>/dev/null | head -1 || true)
        fi

        if [ -n "$luks_uuid" ]; then
            base_cmdline="rd.luks.name=${luks_uuid}=cryptroot root=/dev/mapper/cryptroot rw quiet"
        else
            log_error "Could not determine LUKS UUID - snapshot may not boot!" >&2
            base_cmdline="rw quiet"
        fi
    fi

    # validate cmdline has LUKS parameters
    if ! echo "$base_cmdline" | grep -q "rd.luks"; then
        log_warn "Cmdline missing LUKS parameters! Attempting to add them..." >&2
        local luks_uuid=""
        if [ -e /dev/mapper/cryptroot ]; then
            local backing_device
            backing_device=$(cryptsetup status cryptroot 2>/dev/null | grep "device:" | awk '{print $2}')
            if [ -n "$backing_device" ]; then
                luks_uuid=$(blkid -s UUID -o value "$backing_device" 2>/dev/null || true)
            fi
        fi
        if [ -z "$luks_uuid" ]; then
            luks_uuid=$(blkid -t TYPE=crypto_LUKS -s UUID -o value 2>/dev/null | head -1 || true)
        fi

        if [ -n "$luks_uuid" ]; then
            base_cmdline="rd.luks.name=${luks_uuid}=cryptroot root=/dev/mapper/cryptroot $base_cmdline"
            log_info "Added LUKS parameters: rd.luks.name=${luks_uuid}=cryptroot" >&2
        else
            log_error "Could not determine LUKS UUID!" >&2
        fi
    fi

    # replace rootflags with snapshot subvol (preserve other rootflags options if any)
    if echo "$base_cmdline" | grep -q "rootflags="; then
        # replace subvol= within rootflags, keeping other options
        base_cmdline=$(echo "$base_cmdline" | sed -E "s|rootflags=([^[:space:]]*)?subvol=[^,[:space:]]*|rootflags=\\1subvol=$snapshot_subvol|")
    else
        base_cmdline="$base_cmdline rootflags=subvol=$snapshot_subvol"
    fi

    # set read-write or read-only based on mode
    # remove any existing rw/ro flags first
    base_cmdline=$(echo "$base_cmdline" | sed 's/\brw\b//g; s/\bro\b//g' | tr -s ' ')

    if [ "$mode" = "ro" ]; then
        base_cmdline="$base_cmdline ro"
    else
        base_cmdline="$base_cmdline rw"
    fi

    echo "$base_cmdline"
}

# function to get snapshot info from snapper
get_snapshot_info() {
    local snapshot_num="$1"
    local info_file="$SNAPSHOTS_DIR/$snapshot_num/info.xml"

    local description="Snapshot $snapshot_num"
    local date="Unknown"
    local type="single"

    if [ -f "$info_file" ]; then
        description=$(grep -oP '(?<=<description>)[^<]*' "$info_file" 2>/dev/null | head -1) || description="Snapshot $snapshot_num"
        date=$(grep -oP '(?<=<date>)[^<]*' "$info_file" 2>/dev/null | head -1) || date="Unknown"
        type=$(grep -oP '(?<=<type>)[^<]*' "$info_file" 2>/dev/null | head -1) || type="single"

        [ -z "$description" ] && description="Snapshot $snapshot_num"
    fi

    echo "$description|$date|$type"
}

# function to create custom os-release for snapshot with timestamp and kernel in title
create_snapshot_osrelease() {
    local snapshot_num="$1"
    local description="$2"
    local date="$3"
    local kernel="$4"
    local osrelease_file=$(mktemp)

    # format date for display (e.g., "2025-12-07 14:30")
    local formatted_date
    if [[ "$date" != "Unknown" ]]; then
        # parse ISO date from snapper and format nicely
        formatted_date=$(date -d "$date" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$date" | cut -c1-16)
    else
        formatted_date=$(date '+%Y-%m-%d %H:%M')
    fi

    # format kernel name for display (e.g., "linux-hardened" -> "hardened")
    local kernel_short="${kernel#linux-}"
    [ "$kernel_short" = "linux" ] && kernel_short="mainline"

    # create custom os-release with snapshot info in the name
    # format: "Snapshot #N [kernel] (date)"
    cat > "$osrelease_file" <<EOF
NAME="Arch Linux"
PRETTY_NAME="Snapshot #${snapshot_num} [${kernel_short}] (${formatted_date})"
ID=arch
BUILD_ID=rolling
VERSION_ID=${snapshot_num}
ANSI_COLOR="38;2;23;147;209"
EOF
    echo "$osrelease_file"
}

# function to list available snapshots
list_snapshots() {
    log_info "Available BTRFS root snapshots:"
    if [ -d "$SNAPSHOTS_DIR" ]; then
        local count=0
        for dir in "$SNAPSHOTS_DIR"/*/snapshot; do
            if [ -d "$dir" ]; then
                local num=$(basename "$(dirname "$dir")")
                local info=$(get_snapshot_info "$num")
                local desc=$(echo "$info" | cut -d'|' -f1)
                local date=$(echo "$info" | cut -d'|' -f2)
                echo "    [$num] $desc ($date)"
                ((count++))
            fi
        done
        [ $count -eq 0 ] && echo "    No snapshots found in $SNAPSHOTS_DIR"
    else
        echo "    Snapshots directory $SNAPSHOTS_DIR not found."
        echo "    Configure snapper for root (/) to enable bootable snapshots."
    fi
}

# function to generate UKI for a snapshot
# parameters: snapshot_num, kernel, cmdline, description, snapshot_date
generate_snapshot_uki() {
    local snapshot_num="$1"
    local kernel="$2"
    local cmdline="$3"
    local description="$4"
    local snapshot_date="$5"

    local vmlinuz="/boot/vmlinuz-${kernel}"
    local initrd="/boot/initramfs-${kernel}.img"
    local uki_output="${UKI_DIR}/${SNAPSHOT_UKI_PREFIX}-${snapshot_num}-${kernel}.efi"

    # verify kernel exists
    if [ ! -f "$vmlinuz" ]; then
        log_error "Kernel $vmlinuz not found"
        return 1
    fi

    # verify initramfs exists
    if [ ! -f "$initrd" ]; then
        log_error "Initramfs $initrd not found - run: mkinitcpio -p $kernel"
        return 1
    fi

    # create custom os-release with snapshot timestamp and kernel name
    local osrelease_file
    osrelease_file=$(create_snapshot_osrelease "$snapshot_num" "$description" "$snapshot_date" "$kernel")

    # create temporary cmdline file
    local cmdline_file=$(mktemp)
    echo "$cmdline" > "$cmdline_file"

    log_info "Building UKI for snapshot #$snapshot_num ($kernel)..."

    # use ukify to build the UKI
    if command -v ukify &>/dev/null; then
        # build ukify command arguments
        local ukify_cmd=(ukify build --linux="$vmlinuz")

        # add microcode first, then main initrd
        [ -f "/boot/intel-ucode.img" ] && ukify_cmd+=("--initrd=/boot/intel-ucode.img")
        [ -f "/boot/amd-ucode.img" ] && ukify_cmd+=("--initrd=/boot/amd-ucode.img")
        ukify_cmd+=("--initrd=$initrd")
        ukify_cmd+=("--cmdline=@$cmdline_file")
        ukify_cmd+=("--os-release=@$osrelease_file")
        ukify_cmd+=("--output=$uki_output")

        if ! "${ukify_cmd[@]}"; then
            rm -f "$cmdline_file" "$osrelease_file"
            log_error "ukify failed to build UKI"
            return 1
        fi
    elif command -v objcopy &>/dev/null; then
        # fallback: manual UKI construction with objcopy
        local stub="/usr/lib/systemd/boot/efi/linuxx64.efi.stub"
        if [ ! -f "$stub" ]; then
            rm -f "$cmdline_file" "$osrelease_file"
            log_error "EFI stub not found at $stub"
            return 1
        fi

        # combine initrds (microcode first)
        local combined_initrd=$(mktemp)
        cat /boot/*-ucode.img "$initrd" > "$combined_initrd" 2>/dev/null || cat "$initrd" > "$combined_initrd"

        objcopy \
            --add-section .osrel="$osrelease_file" --change-section-vma .osrel=0x20000 \
            --add-section .cmdline="$cmdline_file" --change-section-vma .cmdline=0x30000 \
            --add-section .linux="$vmlinuz" --change-section-vma .linux=0x2000000 \
            --add-section .initrd="$combined_initrd" --change-section-vma .initrd=0x3000000 \
            "$stub" "$uki_output" || {
            rm -f "$cmdline_file" "$combined_initrd" "$osrelease_file"
            log_error "objcopy failed to build UKI"
            return 1
        }
        rm -f "$combined_initrd"
    else
        rm -f "$cmdline_file" "$osrelease_file"
        log_error "Neither ukify nor objcopy available for UKI generation"
        return 1
    fi

    rm -f "$cmdline_file" "$osrelease_file"

    # sign the UKI for Secure Boot (non-fatal if not available)
    sign_file "$uki_output" || true

    log_info "Created: $uki_output"
    return 0
}

# function to generate UKIs for snapshots
refresh_entries() {
    local max_snapshots="${1:-$DEFAULT_SNAPSHOT_COUNT}"
    log_info "Refreshing snapshot UKIs (last $max_snapshots)..."

    # create UKI directory if needed
    mkdir -p "$UKI_DIR"

    # remove existing snapshot UKIs
    rm -f "${UKI_DIR}/${SNAPSHOT_UKI_PREFIX}"*.efi 2>/dev/null || true

    if [ ! -d "$SNAPSHOTS_DIR" ]; then
        log_warn "Snapshots directory $SNAPSHOTS_DIR not found."
        echo ""
        echo "To enable bootable root snapshots:"
        echo "  1. Install snapper: pacman -S snapper"
        echo "  2. Create root config: snapper -c root create-config /"
        echo "  3. Take a snapshot: snapper -c root create -d 'Initial'"
        return 0
    fi

    # get default kernel
    local kernel
    kernel=$(get_default_kernel)
    log_info "Using kernel: $kernel"

    # check Secure Boot status
    if check_secure_boot_signing; then
        log_info "Secure Boot signing available - UKIs will be signed"
    else
        log_warn "Secure Boot signing not available - UKIs will be unsigned"
        log_warn "Boot may fail if Secure Boot is enabled in UEFI"
    fi

    # get sorted list of snapshot numbers (newest first)
    local snapshot_nums=()
    for dir in "$SNAPSHOTS_DIR"/*/snapshot; do
        if [ -d "$dir" ]; then
            snapshot_nums+=("$(basename "$(dirname "$dir")")")
        fi
    done

    if [ ${#snapshot_nums[@]} -eq 0 ]; then
        log_warn "No snapshots found in $SNAPSHOTS_DIR"
        return 0
    fi

    # sort numerically descending
    IFS=$'\n' sorted_nums=($(sort -rn <<<"${snapshot_nums[*]}")); unset IFS

    local count=0
    local success=0
    for snapshot_num in "${sorted_nums[@]}"; do
        if [ $count -ge $max_snapshots ]; then
            break
        fi

        local snapshot_dir="$SNAPSHOTS_DIR/$snapshot_num/snapshot"
        if [ ! -d "$snapshot_dir" ]; then
            continue
        fi

        # check current snapshot read-only status
        local is_readonly
        is_readonly=$(btrfs property get "$snapshot_dir" ro 2>/dev/null | grep -o "true\|false" || echo "unknown")

        local snapshot_subvol="@snapshots/${snapshot_num}/snapshot"

        # get snapshot info
        local info=$(get_snapshot_info "$snapshot_num")
        local description=$(echo "$info" | cut -d'|' -f1)
        local snap_date=$(echo "$info" | cut -d'|' -f2)

        # make snapshot writable if needed (required for booting)
        if [ "$is_readonly" = "true" ]; then
            log_info "Making snapshot #$snapshot_num writable for boot..."
            if ! btrfs property set "$snapshot_dir" ro false 2>/dev/null; then
                log_warn "Could not make snapshot #$snapshot_num writable - boot may fail"
            fi
        fi

        # generate bootable UKI for this snapshot
        log_info "Generating UKI for snapshot #$snapshot_num ($description)..."
        local cmdline
        cmdline=$(build_snapshot_cmdline "$snapshot_subvol" "rw")
        if generate_snapshot_uki "$snapshot_num" "$kernel" "$cmdline" "$description" "$snap_date"; then
            log_info "Successfully created UKI for snapshot #$snapshot_num"
            ((success++)) || true
        else
            log_error "Failed to create UKI for snapshot #$snapshot_num"
        fi

        ((count++)) || true
    done

    echo ""
    log_info "Created $success bootable snapshot UKIs in $UKI_DIR"

    # send desktop notification
    if [ $success -gt 0 ]; then
        notify_success "Created $success bootable snapshot entries"
        echo ""
        echo "Snapshot UKIs will appear in the systemd-boot menu automatically."
        echo "To boot: Hold Space during boot to access systemd-boot menu"
        echo ""
        echo "Look for entries like:"
        echo "  Snapshot #N [kernel] (date)"
    else
        notify_error "Failed to create bootable snapshot entries"
    fi
}

# function to list current snapshot UKIs
list_entries() {
    log_info "Current snapshot UKIs:"
    if ls "${UKI_DIR}/${SNAPSHOT_UKI_PREFIX}"*.efi &>/dev/null 2>&1; then
        for uki in "${UKI_DIR}/${SNAPSHOT_UKI_PREFIX}"*.efi; do
            local name=$(basename "$uki" .efi)
            local size=$(du -h "$uki" | cut -f1)
            local signed="unsigned"
            if check_secure_boot_signing && sbctl verify "$uki" &>/dev/null; then
                signed="signed"
            fi
            echo "    $name ($size, $signed)"
        done
    else
        echo "    No snapshot UKIs found in $UKI_DIR"
    fi
}

# function to clean up snapshot UKIs
cleanup_entries() {
    log_info "Cleaning up snapshot UKIs..."
    local count=0
    shopt -s nullglob
    for uki in "${UKI_DIR}/${SNAPSHOT_UKI_PREFIX}"*.efi; do
        if [ -f "$uki" ]; then
            rm -v "$uki"
            # remove from sbctl database if signed
            if check_secure_boot_signing; then
                sbctl remove-file "$uki" 2>/dev/null || true
            fi
            ((count++))
        fi
    done
    shopt -u nullglob
    log_info "Removed $count snapshot UKIs."
}

# function to show disk space usage
show_space() {
    log_info "EFI partition usage:"
    df -h "$EFI_DIR" | tail -1 | awk '{print "    Total: "$2", Used: "$3" ("$5"), Available: "$4}'
    echo ""
    log_info "UKI sizes in $UKI_DIR:"
    if ls "${UKI_DIR}"/*.efi &>/dev/null 2>&1; then
        du -h "${UKI_DIR}"/*.efi 2>/dev/null | while read size name; do
            echo "    $(basename "$name"): $size"
        done
        echo ""
        echo "    Total UKI space: $(du -sh "$UKI_DIR" | cut -f1)"
    else
        echo "    No UKIs found"
    fi
}

# main
case "${1:-}" in
    refresh)
        refresh_entries "${2:-$DEFAULT_SNAPSHOT_COUNT}"
        ;;
    list)
        list_snapshots
        echo ""
        list_entries
        ;;
    cleanup)
        cleanup_entries
        ;;
    space)
        show_space
        ;;
    *)
        echo "BTRFS Snapshot UKI Manager (Secure Boot Compatible)"
        echo ""
        echo "Usage: $0 {refresh|list|cleanup|space} [count]"
        echo ""
        echo "Commands:"
        echo "  refresh [N]  - Generate bootable UKIs for last N snapshots (default: 7)"
        echo "  list         - List available snapshots and current UKIs"
        echo "  cleanup      - Remove all snapshot UKIs"
        echo "  space        - Show EFI partition space usage"
        echo ""
        echo "Examples:"
        echo "  $0 refresh      # Create UKIs for last 7 snapshots"
        echo "  $0 refresh 10   # Create UKIs for last 10 snapshots"
        echo "  $0 list         # Show snapshots and UKIs"
        echo "  $0 space        # Check EFI partition space"
        echo ""
        echo "Notes:"
        echo "  - UKIs are ~50-100MB each, so limit snapshot count"
        echo "  - 2GB EFI partition supports ~15-20 UKIs"
        echo "  - UKIs are automatically signed if sbctl is configured"
        echo "  - Desktop notifications sent on success/failure"
        exit 1
        ;;
esac
