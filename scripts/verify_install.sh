#!/usr/bin/env bash
# post-installation verification script
# verifies Secure Boot, UKI, encryption, BTRFS, firewall, and system configuration
# usage: sudo ./verify_install.sh [--fix] [--verbose] [--expected-state FILE]
# the expected values come from the file the installer writes (see expected_state.py)

set -Euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

EXPECTED_STATE_FILE="/etc/dali/expected-state.env"
EFI_VARIABLES_DIRECTORY="/sys/firmware/efi/efivars"
# the UEFI specification's global variables, SecureBoot among them
EFI_GLOBAL_VARIABLE_GUID="8be4df61-93ca-11d2-aa0d-00e098032b8c"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

FIX_MODE=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --fix)
            FIX_MODE=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --expected-state)
            EXPECTED_STATE_FILE="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# helper functions

log_section() {
    echo ""
    echo -e "${BLUE}── $1 ──${NC}"
}

log_pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    ((PASS_COUNT++))
}

log_fail() {
    echo -e "  ${RED}✗${NC} $1"
    ((FAIL_COUNT++))
}

log_warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
    ((WARN_COUNT++))
}

log_info() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo -e "  ${BLUE}ℹ${NC} $1"
    fi
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}Error: This script must be run as root${NC}"
        exit 1
    fi
}

# this script runs as root and sources the file, so a file that anyone but the running
# user (root) could have written is refused rather than executed
load_expectations() {
    if [[ ! -f "$EXPECTED_STATE_FILE" ]]; then
        log_warn "No expectations file at $EXPECTED_STATE_FILE: config-dependent checks are skipped"
        return
    fi
    local owner_and_mode
    owner_and_mode=$(stat -c '%u %a' "$EXPECTED_STATE_FILE")
    local owner="${owner_and_mode%% *}" mode="${owner_and_mode##* }"
    if [[ "$owner" != "$EUID" ]] || (( 8#$mode & 8#022 )); then
        log_fail "$EXPECTED_STATE_FILE must be owned by uid $EUID and writable only by it (owner $owner, mode $mode)"
        return
    fi
    # shellcheck source=/dev/null
    source "$EXPECTED_STATE_FILE"
    log_info "Using expectations from $EXPECTED_STATE_FILE"
}


verify_secure_boot() {
    log_section "SECURE BOOT"

    if [[ -d "$EFI_VARIABLES_DIRECTORY" ]]; then
        log_pass "System booted in UEFI mode"

        local secure_boot_variable="$EFI_VARIABLES_DIRECTORY/SecureBoot-$EFI_GLOBAL_VARIABLE_GUID"
        if [[ -r "$secure_boot_variable" ]]; then
            # four bytes of attributes, then the value: 1 when Secure Boot is enforced
            local secure_boot_value
            secure_boot_value=$(od -An -t u1 -j 4 -N 1 "$secure_boot_variable" | tr -d ' ')
            if [[ "$secure_boot_value" == "1" ]]; then
                log_pass "Secure Boot is ENABLED"
            else
                log_fail "Secure Boot is DISABLED"
                echo "         Enable Secure Boot in BIOS/UEFI settings"
            fi
        else
            log_warn "Could not determine Secure Boot state: $secure_boot_variable is missing"
        fi
    else
        log_fail "System NOT booted in UEFI mode"
    fi

    # check sbctl status
    if command -v sbctl &>/dev/null; then
        log_info "sbctl is installed"

        # check if keys are enrolled
        if sbctl status 2>/dev/null | grep -q "Secure Boot.*enabled"; then
            log_pass "sbctl reports Secure Boot enabled"
        fi

        # verify all signed files
        echo ""
        echo "  Checking signed binaries:"
        local unsigned_count=0
        while IFS= read -r line; do
            if echo "$line" | grep -q "not signed"; then
                log_fail "Unsigned: $line"
                ((unsigned_count++))
            elif echo "$line" | grep -q "signed"; then
                if [[ "$VERBOSE" == "true" ]]; then
                    log_pass "Signed: $(echo "$line" | awk '{print $1}')"
                fi
            fi
        done < <(sbctl verify 2>/dev/null || true)

        if [[ $unsigned_count -eq 0 ]]; then
            log_pass "All registered binaries are properly signed"
        else
            log_fail "$unsigned_count binary(ies) are NOT signed"
            if [[ "$FIX_MODE" == "true" ]]; then
                echo "         Attempting to sign unsigned binaries..."
                sbctl sign-all 2>/dev/null || log_warn "Failed to sign all binaries"
            fi
        fi
    else
        log_fail "sbctl is not installed"
    fi
}


verify_uki() {
    log_section "UNIFIED KERNEL IMAGE (UKI) VERIFICATION"

    local efi_path="/efi"
    local uki_dir="$efi_path/EFI/Linux"

    # check if EFI is mounted
    if mountpoint -q "$efi_path"; then
        log_pass "EFI partition is mounted at $efi_path"
    else
        log_fail "EFI partition is NOT mounted at $efi_path"
        return
    fi

    # check for UKI directory
    if [[ -d "$uki_dir" ]]; then
        log_pass "UKI directory exists: $uki_dir"
    else
        log_fail "UKI directory does NOT exist: $uki_dir"
        return
    fi

    # list and verify UKIs
    echo ""
    echo "  Found UKIs:"
    local uki_count=0
    for uki in "$uki_dir"/*.efi; do
        if [[ -f "$uki" ]]; then
            local uki_name
            uki_name=$(basename "$uki")
            local uki_size
            uki_size=$(du -h "$uki" | cut -f1)
            log_pass "$uki_name ($uki_size)"
            ((uki_count++))

            # verify it's a valid PE binary
            if file "$uki" | grep -q "PE32+ executable"; then
                log_info "  Valid PE32+ executable"
            else
                log_warn "  May not be a valid UKI (not PE32+ format)"
            fi
        fi
    done

    if [[ $uki_count -eq 0 ]]; then
        log_fail "No UKI files found in $uki_dir"
    else
        log_pass "Found $uki_count UKI file(s)"
    fi

    # every configured kernel package has UKIs named arch-<package>-<variant>.efi
    echo ""
    echo "  Verifying configured kernels have UKIs:"
    local kernel_package
    for kernel_package in ${EXPECTED_KERNEL_PACKAGES:-}; do
        if compgen -G "$uki_dir/arch-${kernel_package}-*.efi" >/dev/null; then
            log_pass "UKI exists for kernel: $kernel_package"
        else
            log_fail "No UKI found for kernel: $kernel_package"
        fi
    done
}


verify_bootloader() {
    log_section "BOOTLOADER (systemd-boot) VERIFICATION"

    # check if systemd-boot is installed
    if bootctl is-installed &>/dev/null; then
        log_pass "systemd-boot is installed"
    else
        log_fail "systemd-boot is NOT installed"
        return
    fi

    # check bootctl status
    echo ""
    echo "  Boot loader status:"
    local bootctl_output
    bootctl_output=$(bootctl status 2>/dev/null || true)

    if echo "$bootctl_output" | grep -q "Product.*systemd-boot"; then
        log_pass "Running systemd-boot"
        local version
        version=$(echo "$bootctl_output" | grep "Product" | head -1)
        log_info "$version"
    fi

    # check loader.conf
    local loader_conf="/efi/loader/loader.conf"
    if [[ -f "$loader_conf" ]]; then
        log_pass "loader.conf exists"

        # verify editor is disabled (security)
        if grep -q "^editor no" "$loader_conf" || grep -q "^editor false" "$loader_conf"; then
            log_pass "Boot menu editor is DISABLED (secure)"
        else
            log_warn "Boot menu editor may be ENABLED (security risk)"
        fi

        # check timeout
        local timeout
        timeout=$(grep "^timeout" "$loader_conf" | awk '{print $2}')
        log_info "Boot timeout: ${timeout:-default}s"
    else
        log_fail "loader.conf does NOT exist"
    fi

    # list boot entries
    echo ""
    echo "  Boot entries:"
    bootctl list 2>/dev/null | grep -E "title:|id:" | while read -r line; do
        log_info "$line"
    done
}


verify_encryption() {
    log_section "DISK ENCRYPTION (LUKS2) VERIFICATION"

    # find LUKS devices
    local luks_devices
    luks_devices=$(lsblk -f | grep -i "crypto_LUKS" | awk '{print $1}' | sed 's/[├└─]//g')

    if [[ -n "$luks_devices" ]]; then
        log_pass "LUKS encrypted device(s) found"

        for device in $luks_devices; do
            local full_device="/dev/$device"
            [[ -b "$full_device" ]] || full_device=$(lsblk -rno NAME,TYPE | grep "$device.*part" | awk '{print "/dev/"$1}' | head -1)

            if [[ -b "$full_device" ]]; then
                echo ""
                echo "  Checking $full_device:"

                # get LUKS info
                local luks_info
                luks_info=$(cryptsetup luksDump "$full_device" 2>/dev/null || true)

                # check LUKS version
                if echo "$luks_info" | grep -q "Version:.*2"; then
                    log_pass "Using LUKS2 format"
                else
                    log_warn "Not using LUKS2 format"
                fi

                # check cipher
                local cipher
                cipher=$(echo "$luks_info" | grep "Cipher:" | head -1 | awk '{print $2}')
                if [[ "$cipher" == "aes-xts-plain64" ]]; then
                    log_pass "Cipher: $cipher (recommended)"
                else
                    log_info "Cipher: $cipher"
                fi

                # check PBKDF
                if echo "$luks_info" | grep -qi "argon2id"; then
                    log_pass "PBKDF: argon2id (recommended)"
                elif echo "$luks_info" | grep -qi "argon2i"; then
                    log_pass "PBKDF: argon2i"
                else
                    log_warn "PBKDF: Not using Argon2"
                fi

                # check key size
                local key_size
                key_size=$(echo "$luks_info" | grep "MK bits:" | awk '{print $3}')
                if [[ "$key_size" -ge 512 ]]; then
                    log_pass "Key size: $key_size bits"
                else
                    log_info "Key size: $key_size bits"
                fi
            fi
        done
    else
        log_fail "No LUKS encrypted devices found"
    fi

    # check if root is encrypted
    local root_device
    root_device=$(findmnt -n -o SOURCE /)
    if echo "$root_device" | grep -q "mapper"; then
        log_pass "Root filesystem is on encrypted device"
    else
        log_fail "Root filesystem is NOT encrypted"
    fi
}


verify_btrfs() {
    log_section "BTRFS FILESYSTEM VERIFICATION"

    # check if root is BTRFS
    local root_fstype
    root_fstype=$(findmnt -n -o FSTYPE /)

    if [[ "$root_fstype" == "btrfs" ]]; then
        log_pass "Root filesystem is BTRFS"
    else
        log_fail "Root filesystem is NOT BTRFS (found: $root_fstype)"
        return
    fi

    # list subvolumes
    echo ""
    echo "  BTRFS Subvolumes:"
    local expected_subvolumes=("@" "@home" "@home-snapshots" "@srv" "@var" "@var-log" "@cache-pacman-pkgs" "@var-tmp" "@snapshots" "@swap" "@docker" "@libvirt")

    local mounted_subvolumes
    mounted_subvolumes=$(findmnt -n -t btrfs -o TARGET,OPTIONS | grep "subvol=" || true)

    for subvolume in "${expected_subvolumes[@]}"; do
        # match both subvolume=/@name and subvolume=@name patterns
        if echo "$mounted_subvolumes" | grep -qE "subvol=/?${subvolume}(,|$|[[:space:]])"; then
            log_pass "Subvolume mounted: $subvolume"
        else
            log_warn "Subvolume NOT mounted: $subvolume"
        fi
    done

    # check compression
    echo ""
    echo "  Mount options:"
    if findmnt -n -o OPTIONS / | grep -q "compress=zstd"; then
        log_pass "Compression enabled (zstd)"
    else
        log_warn "Compression not using zstd"
    fi

    if findmnt -n -o OPTIONS / | grep -q "noatime"; then
        log_pass "noatime enabled (performance)"
    else
        log_info "atime updates enabled"
    fi

    # check nocow directories
    echo ""
    echo "  NoCoW directories:"
    for directory in /var /var/lib/docker /var/lib/libvirt /.swap; do
        if [[ -d "$directory" ]]; then
            local attributes
            attributes=$(lsattr -d "$directory" 2>/dev/null | awk '{print $1}')
            if echo "$attributes" | grep -q "C"; then
                log_pass "$directory has NoCoW attribute"
            else
                log_info "$directory does not have NoCoW attribute"
            fi
        fi
    done
}


verify_swap() {
    log_section "SWAP VERIFICATION"

    local swap_path="${EXPECTED_SWAP_PATH:-/.swap/swapfile}"

    if [[ "${EXPECTED_SWAP_ENABLED:-}" == "false" ]]; then
        log_info "Swap is disabled in config"
        return
    fi

    if [[ -f "$swap_path" ]]; then
        log_pass "Swapfile exists at $swap_path"

        # check fstab entry
        if grep -q "$swap_path" /etc/fstab 2>/dev/null; then
            log_pass "Swapfile in fstab"
        else
            log_fail "Swapfile NOT in fstab - add: $swap_path none swap defaults 0 0"
            if [[ "$FIX_MODE" == "true" ]]; then
                echo "         Adding swap to fstab..."
                echo "$swap_path none swap defaults 0 0" >> /etc/fstab
                log_pass "Swap entry added to fstab"
            fi
        fi

        # check if swap is active
        if swapon --show | grep -q "$swap_path"; then
            log_pass "Swapfile is active"
            local swap_size
            swap_size=$(swapon --show=SIZE --noheadings "$swap_path" 2>/dev/null || echo "unknown")
            log_info "Swap size: $swap_size"
        else
            log_fail "Swapfile exists but is NOT active"
            if [[ "$FIX_MODE" == "true" ]]; then
                echo "         Attempting to activate swap..."
                swapon "$swap_path" 2>/dev/null && log_pass "Swap activated" || log_fail "Failed to activate swap"
            fi
        fi

        # check permissions
        local permissions
        permissions=$(stat -c %a "$swap_path")
        if [[ "$permissions" == "600" ]]; then
            log_pass "Swapfile permissions correct (600)"
        else
            log_warn "Swapfile permissions: $permissions (should be 600)"
        fi
    else
        log_fail "Swapfile does NOT exist at $swap_path"
    fi

    # check total swap
    local total_swap
    total_swap=$(free -h | grep Swap | awk '{print $2}')
    log_info "Total swap available: $total_swap"
}


verify_system_config() {
    log_section "SYSTEM CONFIGURATION VERIFICATION"

    # hostname
    local expected_hostname="${EXPECTED_HOSTNAME:-}"
    local actual_hostname
    # use hostname command if available, otherwise read from /etc/hostname
    if command -v hostname &>/dev/null; then
        actual_hostname=$(hostname)
    elif [[ -f /etc/hostname ]]; then
        actual_hostname=$(cat /etc/hostname)
    else
        actual_hostname="unknown"
    fi

    if [[ -n "$expected_hostname" ]]; then
        if [[ "$actual_hostname" == "$expected_hostname" ]]; then
            log_pass "Hostname: $actual_hostname"
        else
            log_fail "Hostname mismatch: expected '$expected_hostname', got '$actual_hostname'"
        fi
    else
        log_info "Hostname: $actual_hostname"
    fi

    # timezone
    local expected_timezone="${EXPECTED_TIMEZONE:-}"
    local actual_timezone
    actual_timezone=$(timedatectl show --property=Timezone --value 2>/dev/null || readlink /etc/localtime | sed 's|.*/zoneinfo/||')

    if [[ -n "$expected_timezone" ]]; then
        if [[ "$actual_timezone" == "$expected_timezone" ]]; then
            log_pass "Timezone: $actual_timezone"
        else
            log_fail "Timezone mismatch: expected '$expected_timezone', got '$actual_timezone'"
        fi
    else
        log_info "Timezone: $actual_timezone"
    fi

    # locale
    local expected_locale="${EXPECTED_LOCALE:-}"
    local actual_locale
    actual_locale=$(localectl status | grep "System Locale" | sed 's/.*LANG=//')

    if [[ -n "$expected_locale" ]]; then
        if [[ "$actual_locale" == "$expected_locale" ]]; then
            log_pass "Locale: $actual_locale"
        else
            log_fail "Locale mismatch: expected '$expected_locale', got '$actual_locale'"
        fi
    else
        log_info "Locale: $actual_locale"
    fi

    # keymap
    local expected_keymap="${EXPECTED_KEYMAP:-}"
    local actual_keymap
    actual_keymap=$(localectl status | grep "VC Keymap" | awk '{print $3}')

    if [[ -n "$expected_keymap" ]]; then
        if [[ "$actual_keymap" == "$expected_keymap" ]]; then
            log_pass "Keymap: $actual_keymap"
        else
            log_fail "Keymap mismatch: expected '$expected_keymap', got '$actual_keymap'"
        fi
    else
        log_info "Keymap: $actual_keymap"
    fi

    # user
    local expected_user="${EXPECTED_USER:-}"
    if [[ -n "$expected_user" ]]; then
        if id "$expected_user" &>/dev/null; then
            log_pass "User exists: $expected_user"
            local user_groups
            user_groups=$(groups "$expected_user" 2>/dev/null | cut -d: -f2)
            log_info "User groups:$user_groups"

            # check wheel group for sudo
            if echo "$user_groups" | grep -q "wheel"; then
                log_pass "User is in wheel group (sudo access)"
            else
                log_warn "User is NOT in wheel group"
            fi
        else
            log_fail "User does NOT exist: $expected_user"
        fi
    fi
}


verify_network() {
    log_section "NETWORK VERIFICATION"

    # check NetworkManager
    if systemctl is-active --quiet NetworkManager; then
        log_pass "NetworkManager is running"
    else
        log_fail "NetworkManager is NOT running"
        if [[ "$FIX_MODE" == "true" ]]; then
            echo "         Attempting to start NetworkManager..."
            systemctl start NetworkManager && log_pass "NetworkManager started" || log_fail "Failed to start"
        fi
    fi

    if systemctl is-enabled --quiet NetworkManager; then
        log_pass "NetworkManager is enabled on boot"
    else
        log_warn "NetworkManager is NOT enabled on boot"
        if [[ "$FIX_MODE" == "true" ]]; then
            systemctl enable NetworkManager && log_pass "NetworkManager enabled" || log_fail "Failed to enable"
        fi
    fi

    # check network connectivity
    if ping -c 1 -W 3 archlinux.org &>/dev/null; then
        log_pass "Internet connectivity: OK"
    else
        log_warn "Internet connectivity: FAILED (may be offline)"
    fi
}


verify_services() {
    log_section "SERVICES VERIFICATION"

    # essential services
    local essential_services=("systemd-resolved" "systemd-timesyncd")

    for service in "${essential_services[@]}"; do
        if systemctl is-active --quiet "$service"; then
            log_pass "$service is running"
        else
            log_warn "$service is NOT running"
        fi
    done

    # every configured display manager should be enabled
    local display_manager
    for display_manager in ${EXPECTED_DISPLAY_MANAGERS:-}; do
        if systemctl is-enabled --quiet "$display_manager"; then
            log_pass "Display manager ($display_manager) is enabled"
        else
            log_fail "Display manager ($display_manager) is NOT enabled"
        fi
    done

    # check snapper if enabled
    if [[ "${EXPECTED_SNAPPER_ENABLED:-}" == "true" ]]; then
        echo ""
        echo "  Snapper services:"
        if systemctl is-enabled --quiet snapper-timeline.timer; then
            log_pass "snapper-timeline.timer is enabled"
        else
            log_warn "snapper-timeline.timer is NOT enabled"
        fi

        if systemctl is-enabled --quiet snapper-cleanup.timer; then
            log_pass "snapper-cleanup.timer is enabled"
        else
            log_warn "snapper-cleanup.timer is NOT enabled"
        fi

        # check snapper configs
        if [[ -f /etc/snapper/configs/root ]]; then
            log_pass "Snapper root config exists"
        else
            log_fail "Snapper root config missing"
        fi

        if [[ -f /etc/snapper/configs/home ]]; then
            log_pass "Snapper home config exists"
        else
            log_warn "Snapper home config missing"
        fi
    fi
}


verify_kernel_params() {
    log_section "KERNEL PARAMETERS (HARDENING) VERIFICATION"

    local cmdline
    cmdline=$(cat /proc/cmdline)
    log_info "Current cmdline: $cmdline"

    echo ""
    echo "  Security parameters:"

    # check lockdown
    if echo "$cmdline" | grep -q "lockdown="; then
        local lockdown
        lockdown=$(echo "$cmdline" | grep -oP 'lockdown=\K\w+')
        log_pass "Lockdown mode: $lockdown"
    else
        log_info "Lockdown not set"
    fi

    # check IOMMU
    if echo "$cmdline" | grep -q "iommu="; then
        log_pass "IOMMU parameter set"
    else
        log_info "IOMMU parameter not set"
    fi

    # check PTI (Meltdown mitigation)
    if echo "$cmdline" | grep -q "pti=on"; then
        log_pass "PTI (Page Table Isolation) enabled"
    else
        log_info "PTI parameter not explicitly set"
    fi

    # check spectre mitigations
    if echo "$cmdline" | grep -q "spectre_v2=on"; then
        log_pass "Spectre v2 mitigation enabled"
    fi

    # check memory init
    if echo "$cmdline" | grep -q "init_on_alloc=1"; then
        log_pass "Memory initialization on alloc enabled"
    fi

    if echo "$cmdline" | grep -q "init_on_free=1"; then
        log_pass "Memory initialization on free enabled"
    fi

    # check root filesystem params
    echo ""
    echo "  Root filesystem parameters:"
    if echo "$cmdline" | grep -q "rootflags=subvol=@"; then
        log_pass "Root subvolume correctly set (@)"
    fi

    if echo "$cmdline" | grep -q "rw"; then
        log_pass "Root mounted read-write"
    fi
}


verify_gpu() {
    log_section "GPU DRIVER VERIFICATION"

    # always show detected GPU first
    if command -v lspci &>/dev/null; then
        local gpu_info
        gpu_info=$(lspci 2>/dev/null | grep -iE "vga|3d|display" || true)
        if [[ -n "$gpu_info" ]]; then
            log_info "Detected GPU: $gpu_info"
        else
            log_info "No GPU detected (may be VM or headless)"
        fi
    fi

    local gpu_vendor="${EXPECTED_GPU_VENDOR:-none}"
    local gpu_driver="${EXPECTED_GPU_DRIVER:-}"

    if [[ "$gpu_vendor" == "none" ]]; then
        log_info "No GPU vendor configured"
        return
    fi

    echo ""
    echo "  Expected: $gpu_vendor / ${gpu_driver:-vendor default}"

    # check loaded kernel modules
    echo ""
    echo "  GPU kernel modules:"

    # an NVIDIA card without an explicit driver gets the proprietary one
    case "$gpu_vendor/$gpu_driver" in
        nvidia/nouveau)
            if lsmod | grep -q "nouveau"; then
                log_pass "nouveau module loaded"
            else
                log_fail "nouveau module NOT loaded"
            fi
            ;;
        nvidia/*)
            if lsmod | grep -q "nvidia"; then
                log_pass "nvidia module loaded"
            else
                log_fail "nvidia module NOT loaded"
            fi
            ;;
    esac

    # check if GPU is detected
    if command -v lspci &>/dev/null; then
        local gpu_info
        gpu_info=$(lspci | grep -iE "vga|3d|display" || true)
        if [[ -n "$gpu_info" ]]; then
            log_info "Detected GPU: $gpu_info"
        fi
    fi
}


verify_fstab() {
    log_section "FSTAB VERIFICATION"

    if [[ -f /etc/fstab ]]; then
        log_pass "/etc/fstab exists"

        # check for essential entries
        local essential_mounts=("/" "/efi" "/home" "/.snapshots")

        for mount in "${essential_mounts[@]}"; do
            if grep -qE "^\s*[^#].*\s+${mount}\s+" /etc/fstab; then
                log_pass "fstab entry for $mount"
            else
                log_warn "No fstab entry for $mount"
            fi
        done

        if grep -q "swapfile" /etc/fstab || grep -q "swap" /etc/fstab; then
            log_pass "fstab has swap entry"
        else
            log_info "No swap entry in fstab"
        fi
    else
        log_fail "/etc/fstab does NOT exist"
    fi
}


verify_usb_boot_drive() {
    [[ "${EXPECTED_USB_BOOT:-}" == "true" ]] || return 0
    log_section "USB BOOT DRIVE VERIFICATION"

    # the header lives on the drive: the internal disk holds ciphertext from its first
    # byte to its last, with no partition table, filesystem or LUKS signature
    local data_device data_type partition_table partition_count
    data_device=$(cryptsetup status cryptroot 2>/dev/null | awk '$1 == "device:" {print $2}')
    data_type=$(blkid -p -o value -s TYPE "$data_device" 2>/dev/null)
    partition_table=$(blkid -p -o value -s PTTYPE "$data_device" 2>/dev/null)
    partition_count=$(lsblk -nlo TYPE "$data_device" 2>/dev/null | grep -c part)
    if [[ -z "$data_device" ]]; then
        log_fail "cryptroot is not open"
    elif [[ -n "$partition_table" || "$partition_count" -gt 0 ]]; then
        log_fail "$data_device has a partition table (${partition_table:-partitions})"
    elif [[ -n "$data_type" ]] || cryptsetup isLuks "$data_device" 2>/dev/null; then
        log_fail "$data_device identifies as ${data_type:-LUKS}"
    else
        log_pass "$data_device carries no partition table, LUKS header or other signature"
    fi

    if grep -qE '^\s*[^#]\S*\s+/efi\s.*x-systemd\.automount' /etc/fstab; then
        log_pass "/efi is mounted on demand from the USB boot drive"
    else
        log_fail "/efi is not mounted on demand (no x-systemd.automount in fstab)"
    fi

    if [[ -f /etc/pacman.d/hooks/00-usb-boot-drive.hook && -x /usr/local/bin/check-usb-boot-drive ]]; then
        log_pass "pacman refuses boot file updates while the drive is unplugged"
    else
        log_fail "The USB boot drive guard (pacman hook) is missing"
    fi

    if [[ "${EXPECTED_RECOVERY_SYSTEM:-}" == "true" ]]; then
        local recovery_uki="/efi/EFI/recovery/arch-recovery.efi"
        if [[ -f "$recovery_uki" && -f /efi/loader/entries/arch-recovery.conf ]]; then
            log_pass "Recovery system UKI and boot entry present"
            if sbctl verify "$recovery_uki" 2>/dev/null | grep -q "is signed"; then
                log_pass "Recovery system UKI is signed"
            else
                log_fail "Recovery system UKI is not signed"
            fi
        else
            log_fail "Recovery system UKI or boot entry missing"
        fi
    fi
}


# ufw prints every direction on one line: "Default: deny (incoming), allow (outgoing), deny (routed)"
ufw_default_policy() {
    local direction="$1"
    local ufw_verbose="$2"
    grep -m1 '^Default:' <<<"$ufw_verbose" | grep -oP "\w+(?= \($direction\))"
}

verify_firewall() {
    log_section "FIREWALL (UFW)"

    if [[ "${EXPECTED_FIREWALL_ENABLED:-}" == "false" ]]; then
        log_info "Firewall is disabled in the configuration"
        return
    fi

    if ! command -v ufw &>/dev/null; then
        log_fail "UFW is not installed"
        return
    fi

    log_pass "UFW is installed"

    if systemctl is-enabled --quiet ufw.service; then
        log_pass "UFW service is enabled"
    else
        log_fail "UFW service is NOT enabled"
        if [[ "$FIX_MODE" == "true" ]]; then
            systemctl enable ufw.service && log_pass "UFW enabled" || log_fail "Failed to enable"
        fi
    fi

    if systemctl is-active --quiet ufw.service; then
        log_pass "UFW service is running"
    else
        log_fail "UFW service is NOT running"
        if [[ "$FIX_MODE" == "true" ]]; then
            systemctl start ufw.service && log_pass "UFW started" || log_fail "Failed to start"
        fi
    fi

    local ufw_status
    ufw_status=$(ufw status 2>/dev/null || echo "inactive")

    if echo "$ufw_status" | grep -qi "Status: active"; then
        log_pass "UFW firewall is active"
    else
        log_fail "UFW firewall is NOT active"
    fi

    # check default policies
    local ufw_verbose
    ufw_verbose=$(ufw status verbose 2>/dev/null || echo "")

    local incoming_policy outgoing_policy
    incoming_policy=$(ufw_default_policy incoming "$ufw_verbose")
    outgoing_policy=$(ufw_default_policy outgoing "$ufw_verbose")

    if [[ "$incoming_policy" == "deny" ]]; then
        log_pass "Default incoming: DENY"
    else
        log_warn "Default incoming policy is ${incoming_policy:-unknown}, expected deny"
    fi

    if [[ "$outgoing_policy" == "allow" ]]; then
        log_pass "Default outgoing: ALLOW"
    else
        log_warn "Default outgoing policy is ${outgoing_policy:-unknown}, expected allow"
    fi

    # check logging
    if echo "$ufw_verbose" | grep -qi "Logging: on"; then
        log_pass "UFW logging is enabled"
    else
        log_info "UFW logging status unknown"
    fi
}


verify_packages() {
    log_section "ESSENTIAL PACKAGES"

    local essential_packages=("base" "linux-firmware" "btrfs-progs" "efibootmgr" "networkmanager" "sbctl" "sudo" "ufw")

    for package in "${essential_packages[@]}"; do
        if pacman -Q "$package" &>/dev/null; then
            log_pass "$package is installed"
        else
            log_fail "$package is NOT installed"
        fi
    done

    echo ""
    echo "  Installed kernels:"
    for kernel in linux linux-hardened linux-lts linux-zen; do
        if pacman -Q "$kernel" &>/dev/null; then
            local version
            version=$(pacman -Q "$kernel" | awk '{print $2}')
            log_pass "$kernel ($version)"
        fi
    done

    echo ""
    echo "  CPU Microcode:"
    if pacman -Q intel-ucode &>/dev/null; then
        log_pass "intel-ucode installed"
    elif pacman -Q amd-ucode &>/dev/null; then
        log_pass "amd-ucode installed"
    else
        log_warn "No CPU microcode package found"
    fi
}


print_summary() {
    log_section "SUMMARY"

    echo ""
    echo -e "  ${GREEN}Passed:${NC}   $PASS_COUNT"
    echo -e "  ${RED}Failed:${NC}   $FAIL_COUNT"
    echo -e "  ${YELLOW}Warnings:${NC} $WARN_COUNT"
    echo ""

    if [[ $FAIL_COUNT -eq 0 ]]; then
        echo -e "  ${GREEN}✓ All critical checks passed!${NC}"
        echo ""
        return 0
    else
        echo -e "  ${RED}✗ Some checks failed.${NC}"
        echo ""

        if [[ "$FIX_MODE" == "false" ]]; then
            echo "  Tip: Run with --fix to attempt automatic fixes"
        fi
        return 1
    fi
}


main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║          Arch Installer - Post-Installation Verification            ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    check_root
    load_expectations

    verify_secure_boot
    verify_uki
    verify_bootloader
    verify_encryption
    verify_btrfs
    verify_swap
    verify_system_config
    verify_network
    verify_services
    verify_firewall
    verify_kernel_params
    verify_gpu
    verify_fstab
    verify_usb_boot_drive
    verify_packages

    print_summary
}

# sourcing the script (as the unit tests do) defines the functions without running them
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
