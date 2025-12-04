#!/usr/bin/env bash
set -Eeuo pipefail

# trap errors
trap 'echo "Error: Script failed on line $LINENO"; exit 1' ERR

# Arch Installer
# orchestrates installation and configuration of Arch Linux.
# designed to be idempotent - can be run multiple times safely.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CFG="${SCRIPT_DIR}/../config/config.yaml"

# source all component scripts
source "$SCRIPT_DIR/choose_disk.sh"
source "$SCRIPT_DIR/storage.sh"
source "$SCRIPT_DIR/system.sh"
source "$SCRIPT_DIR/packages.sh"
source "$SCRIPT_DIR/gpu.sh"
source "$SCRIPT_DIR/mkinitcpio.sh"
source "$SCRIPT_DIR/boot.sh"
source "$SCRIPT_DIR/snapper.sh"

# global variables for credentials and settings (set during initial setup)
export LUKS_PASSWORD=""
export USER_PASSWORD=""
export INSTALL_HOSTNAME=""
export INSTALL_USERNAME=""
export INSTALL_TIMEZONE=""
export SWAP_SIZE_MB=""
export CPU_VENDOR=""      # intel or amd
export GPU_VENDOR=""      # amd, intel, nvidia, or none
export GPU_DRIVER=""      # For nvidia: nouveau, nvidia-open, nvidia-dkms

# kernel and UKI selections
declare -a SELECTED_KERNELS=()        # linux, linux-hardened, linux-lts
declare -a SELECTED_DEBUG_FLAGS=()    # no-dc, no-runpm, no-dc-no-runpm
declare -a SELECTED_DESKTOPS=()       # gnome, kde, hyprland (can install multiple)
export ENABLE_SNAPSHOT_BOOT="${ENABLE_SNAPSHOT_BOOT:-false}"   # whether to enable bootable snapshot UKIs
export ENABLE_UFW="${ENABLE_UFW:-true}"                        # whether to enable UFW firewall

# steps that can be excluded (array of step numbers)
declare -a EXCLUDED_STEPS=()

# step definitions
declare -A STEP_NAMES=(
    [1]="Disk Selection & Storage Setup"
    [2]="Package Installation"
    [3]="System Configuration"
    [4]="GPU Driver Setup"
    [5]="mkinitcpio & UKI Generation"
    [6]="Bootloader Setup"
    [7]="Snapper Configuration"
    [8]="Firewall Configuration"
)

declare -A STEP_DESCRIPTIONS=(
    [1]="Select disk, partition (EFI + LUKS), format (BTRFS), mount subvolumes. WARNING: Can be destructive."
    [2]="Install base packages, kernels, firmware, and desktop environment."
    [3]="Configure hostname, username, timezone, locale, keymap."
    [4]="Install GPU drivers based on configuration."
    [5]="Configure mkinitcpio, generate and sign UKIs for Secure Boot."
    [6]="Install systemd-boot and sign bootloader binaries."
    [7]="Configure automated BTRFS snapshots with snapper."
    [8]="Enable UFW firewall with secure defaults."
)

# steps that cannot be excluded (dependencies)
declare -A STEP_REQUIRED=(
    [1]=true   # Storage is required for everything
    [2]=true   # Packages are required for system to function
    [3]=true   # System config is required
    [4]=false  # GPU is optional
    [5]=true   # UKI generation required for boot
    [6]=true   # Bootloader required for boot
    [7]=false  # Snapper is optional
    [8]=false  # Firewall is optional
)

# Menu option definitions
# these maps define the relationship between menu numbers and values.
# format: [menu_number]="value|description"
# TODO: find cleaner way to map to the actual values used in config.yaml, without having to duplicate here.

declare -A SWAP_OPTIONS=(
    [1]="8192|8 GB"
    [2]="16384|16 GB"
    [3]="32768|32 GB"
    [4]="65536|64 GB"
    [5]="ram|Match RAM"
    [6]="custom|Custom size"
    [7]="0|No swap"
)

declare -A GPU_OPTIONS=(
    [1]="amd|AMD (AMDGPU, open-source)"
    [2]="intel|Intel (integrated graphics)"
    [3]="nvidia|NVIDIA (proprietary/nouveau)"
    [4]="none|None (VM or generic)"
)

declare -A NVIDIA_DRIVER_OPTIONS=(
    [1]="nouveau|open-source, limited features"
    [2]="nvidia-open|official open kernel modules, RTX 20+"
    [3]="nvidia-dkms|proprietary, best compatibility"
)

declare -A KERNEL_OPTIONS=(
    [1]="linux|Mainline kernel (latest features)"
    [2]="linux-hardened|Security-focused kernel"
    [3]="linux-lts|Long-term support kernel (stability)"
)

declare -A DEBUG_VARIANT_OPTIONS=(
    [1]="no-dc|Disable AMD Display Core (amdgpu.dc=0)"
    [2]="no-runpm|Disable AMD runtime power management (amdgpu.runpm=0)"
    [3]="no-dc-no-runpm|Both flags combined"
    [0]="none|Only default UKIs"
)

declare -A TIMEZONE_OPTIONS=(
    [1]="Europe/Paris|Paris"
    [2]="Europe/London|London"
    [3]="Europe/Berlin|Berlin"
    [4]="America/New_York|New York"
    [5]="America/Los_Angeles|Los Angeles"
    [6]="Asia/Tokyo|Tokyo"
    [7]="UTC|UTC"
    [8]="custom|Custom timezone"
)

declare -A CPU_OPTIONS=(
    [1]="intel|Intel"
    [2]="amd|AMD"
)

declare -A DESKTOP_OPTIONS=(
    [1]="gnome|GNOME (Wayland, modern, intuitive)"
    [2]="kde|KDE Plasma (Wayland, highly customizable)"
    [3]="hyprland|Hyprland (Wayland tiling WM, power users)"
    [4]="all|All three (choose at login via SDDM)"
    [0]="none|None (headless server / minimal)"
)

# Menu helper functions
# get menu number for a given value from an options map
# usage: get_menu_number "GPU_OPTIONS" "nvidia" -> returns "3"
get_menu_number() {
    local -n map_ref=$1
    local value="$2"
    for key in "${!map_ref[@]}"; do
        local entry_value="${map_ref[$key]%%|*}"
        if [[ "$entry_value" == "$value" ]]; then
            echo "$key"
            return 0
        fi
    done
    echo ""
}

# get value for a given menu number from an options map
# usage: get_menu_value "GPU_OPTIONS" "3" -> returns "nvidia"
get_menu_value() {
    local -n map_ref=$1
    local num="$2"
    if [[ -n "${map_ref[$num]:-}" ]]; then
        echo "${map_ref[$num]%%|*}"
    fi
}

# get description for a given menu number from an options map
# usage: get_menu_description "GPU_OPTIONS" "3" -> returns "NVIDIA (proprietary/nouveau)"
get_menu_description() {
    local -n map_ref=$1
    local num="$2"
    if [[ -n "${map_ref[$num]:-}" ]]; then
        echo "${map_ref[$num]#*|}"
    fi
}

# print menu options from an options map
# usage: print_menu_options "GPU_OPTIONS"
print_menu_options() {
    local -n map_ref=$1
    local keys=($(echo "${!map_ref[@]}" | tr ' ' '\n' | sort -n))
    for key in "${keys[@]}"; do
        local desc="${map_ref[$key]#*|}"
        echo "  $key) $desc"
    done
}

# check if a step is excluded
is_step_excluded() {
    local step_num="$1"
    for excluded in "${EXCLUDED_STEPS[@]}"; do
        if [[ "$excluded" == "$step_num" ]]; then
            return 0
        fi
    done
    return 1
}

# securely read a password with confirmation
read_password() {
    local prompt="$1"
    local var_name="$2"
    local password=""
    local password_confirm=""

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        eval "$var_name='password'"
        return 0
    fi

    while true; do
        echo ""
        read -rs -p "$prompt: " password
        echo ""
        read -rs -p "Confirm $prompt: " password_confirm
        echo ""

        if [[ "$password" != "$password_confirm" ]]; then
            echo "Passwords do not match. Please try again."
            continue
        fi

        if [[ -z "$password" ]]; then
            echo "Password cannot be empty. Please try again."
            continue
        fi

        if [[ ${#password} -lt 8 ]]; then
            echo "Warning: Password is less than 8 characters."
        fi

        break
    done

    eval "$var_name=\$password"
}

# read a value with a default
read_with_default() {
    local prompt="$1"
    local var_name="$2"
    local default="$3"

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        eval "$var_name=\$default"
        return 0
    fi

    local input=""
    read -rp "$prompt [$default]: " input
    if [[ -z "$input" ]]; then
        eval "$var_name=\$default"
    else
        eval "$var_name=\$input"
    fi
}

# select timezone interactively
select_timezone() {
    local config_default="$1"

    # hierarchy: ENV > Config
    local default_tz="${INSTALL_TIMEZONE:-$config_default}"

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        INSTALL_TIMEZONE="$default_tz"
        return 0
    fi

    echo ""
    echo "===== Timezone Selection ====="
    echo "Config default: $config_default"
    echo ""
    echo "Common timezones:"
    print_menu_options TIMEZONE_OPTIONS
    echo ""

    # determine which option matches the default
    local default_choice
    default_choice=$(get_menu_number TIMEZONE_OPTIONS "$default_tz")
    [[ -z "$default_choice" ]] && default_choice="8"  # Custom if not found

    local choice
    read -rp "Select timezone [1-8, default=$default_choice ($default_tz)]: " choice
    choice="${choice:-$default_choice}"

    local tz_value
    tz_value=$(get_menu_value TIMEZONE_OPTIONS "$choice")

    case "$tz_value" in
        "custom")
            read -rp "Enter timezone (e.g., Europe/Paris) [$default_tz]: " INSTALL_TIMEZONE
            [[ -z "$INSTALL_TIMEZONE" ]] && INSTALL_TIMEZONE="$default_tz"
            ;;
        "")
            INSTALL_TIMEZONE="$default_tz"
            ;;
        *)
            INSTALL_TIMEZONE="$tz_value"
            ;;
    esac

    echo "Selected timezone: $INSTALL_TIMEZONE"
}

# detect system RAM and present swap size menu
select_swap_size() {
    # get config default
    local config_swap_mb
    config_swap_mb=$(yq -r '.storage.swap.size_mb // 8192' "$CFG" 2>/dev/null || echo "8192")

    # hierarchy: ENV > Config
    local default_swap="${SWAP_SIZE_MB:-$config_swap_mb}"

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        SWAP_SIZE_MB="$default_swap"
        return 0
    fi

    if [[ "${SKIP_SWAP:-false}" == "true" ]]; then
        SWAP_SIZE_MB="0"
        return 0
    fi

    # detect RAM
    local ram_kb
    ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local ram_mb=$((ram_kb / 1024))
    local ram_gb=$((ram_mb / 1024))

    echo ""
    echo "===== Swap Size Selection ====="
    echo "Detected RAM: ${ram_gb}GB (${ram_mb}MB)"
    echo "Config default: ${config_swap_mb}MB ($((config_swap_mb / 1024))GB)"
    echo ""
    echo "Recommended: equal to RAM for hibernation support"
    echo ""
    print_menu_options SWAP_OPTIONS
    echo ""

    # determine which option matches config default
    local default_choice
    default_choice=$(get_menu_number SWAP_OPTIONS "$config_swap_mb")
    [[ -z "$default_choice" ]] && default_choice="6"  # Custom if not found
    # if config matches RAM, prefer that
    if [[ "$config_swap_mb" -eq "$ram_mb" ]]; then
        default_choice="5"
    fi

    local choice
    read -rp "Select [1-7, default=$default_choice]: " choice
    choice="${choice:-$default_choice}"

    local swap_value
    swap_value=$(get_menu_value SWAP_OPTIONS "$choice")

    case "$swap_value" in
        "ram") SWAP_SIZE_MB=$ram_mb ;;
        "custom")
            read -rp "Enter swap size in MB [$config_swap_mb]: " SWAP_SIZE_MB
            [[ -z "$SWAP_SIZE_MB" ]] && SWAP_SIZE_MB="$config_swap_mb"
            ;;
        "")
            SWAP_SIZE_MB="$config_swap_mb"
            ;;
        *)
            SWAP_SIZE_MB="$swap_value"
            ;;
    esac

    if [[ "$SWAP_SIZE_MB" -gt 0 ]]; then
        echo "Selected: ${SWAP_SIZE_MB}MB ($((SWAP_SIZE_MB / 1024))GB)"
    else
        echo "Swap disabled."
    fi
}

# select CPU vendor for microcode
select_cpu_vendor() {
    # no config default for CPU - must be detected or specified
    # hierarchy: ENV > auto-detect
    local default_cpu="${CPU_VENDOR:-}"

    # try to auto-detect if not set
    if [[ -z "$default_cpu" ]]; then
        if grep -qi "intel" /proc/cpuinfo 2>/dev/null; then
            default_cpu="intel"
        elif grep -qi "amd" /proc/cpuinfo 2>/dev/null; then
            default_cpu="amd"
        else
            default_cpu="intel"
        fi
    fi

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        CPU_VENDOR="$default_cpu"
        return 0
    fi

    echo ""
    echo "===== CPU Vendor Selection ====="
    echo "Auto-detected: $default_cpu"
    echo ""
    echo "Select your CPU vendor for microcode updates:"
    echo ""
    print_menu_options CPU_OPTIONS
    echo ""

    local default_choice
    default_choice=$(get_menu_number CPU_OPTIONS "$default_cpu")
    [[ -z "$default_choice" ]] && default_choice="1"

    local choice
    read -rp "Select [1-2, default=$default_choice ($default_cpu)]: " choice
    choice="${choice:-$default_choice}"

    CPU_VENDOR=$(get_menu_value CPU_OPTIONS "$choice")
    [[ -z "$CPU_VENDOR" ]] && CPU_VENDOR="$default_cpu"

    echo "Selected CPU: $CPU_VENDOR"
}

# select GPU vendor and driver
select_gpu_vendor() {
    # get config defaults
    local config_gpu_enabled
    local config_gpu_vendor
    local config_gpu_driver
    config_gpu_enabled=$(yq -r '.gpu.enabled // false' "$CFG" 2>/dev/null || echo "false")
    config_gpu_vendor=$(yq -r '.gpu.vendor // "none"' "$CFG" 2>/dev/null || echo "none")
    config_gpu_driver=$(yq -r '.gpu.driver // ""' "$CFG" 2>/dev/null || echo "")

    # hierarchy: ENV > Config
    local default_vendor="${GPU_VENDOR:-}"
    local default_driver="${GPU_DRIVER:-}"

    if [[ -z "$default_vendor" ]]; then
        if [[ "$config_gpu_enabled" == "true" ]]; then
            default_vendor="$config_gpu_vendor"
            default_driver="$config_gpu_driver"
        else
            default_vendor="none"
        fi
    fi

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        GPU_VENDOR="$default_vendor"
        GPU_DRIVER="$default_driver"
        return 0
    fi

    echo ""
    echo "===== GPU Vendor Selection ====="
    echo "Config default: $default_vendor${default_driver:+ ($default_driver)}"
    echo ""
    echo "Select your GPU vendor:"
    echo ""
    print_menu_options GPU_OPTIONS
    echo ""

    local default_choice
    default_choice=$(get_menu_number GPU_OPTIONS "$default_vendor")
    [[ -z "$default_choice" ]] && default_choice="4"

    local choice
    read -rp "Select [1-4, default=$default_choice]: " choice
    choice="${choice:-$default_choice}"

    GPU_VENDOR=$(get_menu_value GPU_OPTIONS "$choice")
    [[ -z "$GPU_VENDOR" ]] && GPU_VENDOR="$default_vendor"

    # handle driver selection for specific vendors
    case "$GPU_VENDOR" in
        nvidia)
            select_nvidia_driver "$default_driver"
            ;;
        *)
            GPU_DRIVER=""
            ;;
    esac

    if [[ "$GPU_VENDOR" != "none" ]]; then
        echo "Selected GPU: $GPU_VENDOR"
        [[ -n "$GPU_DRIVER" ]] && echo "Selected driver: $GPU_DRIVER"
    else
        echo "GPU drivers: None (using generic/VM drivers)"
    fi
}

# select NVIDIA driver variant
select_nvidia_driver() {
    local config_default="${1:-nvidia-dkms}"

    echo ""
    echo "===== NVIDIA Driver Selection ====="
    echo "Config default: $config_default"
    echo ""
    echo "Select NVIDIA driver:"
    echo ""
    print_menu_options NVIDIA_DRIVER_OPTIONS
    echo ""

    local default_choice
    default_choice=$(get_menu_number NVIDIA_DRIVER_OPTIONS "$config_default")
    [[ -z "$default_choice" ]] && default_choice="3"

    local choice
    read -rp "Select [1-3, default=$default_choice]: " choice
    choice="${choice:-$default_choice}"

    GPU_DRIVER=$(get_menu_value NVIDIA_DRIVER_OPTIONS "$choice")
    [[ -z "$GPU_DRIVER" ]] && GPU_DRIVER="$config_default"
}

# select kernels to install
select_kernels() {
    # get config defaults - read kernel packages from config
    local config_kernels
    config_kernels=$(yq -r '.boot.kernels[].package' "$CFG" 2>/dev/null | tr '\n' ' ' || echo "linux")

    # hierarchy: ENV (SELECTED_KERNELS already set) > Config
    if [[ ${#SELECTED_KERNELS[@]} -eq 0 ]]; then
        # parse config kernels into array
        for kernel in $config_kernels; do
            SELECTED_KERNELS+=("$kernel")
        done
    fi

    # default if still empty
    if [[ ${#SELECTED_KERNELS[@]} -eq 0 ]]; then
        SELECTED_KERNELS=("linux")
    fi

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        return 0
    fi

    echo ""
    echo "===== Kernel Selection ====="
    echo "Config default: ${SELECTED_KERNELS[*]}"
    echo ""
    echo "Select which kernels to install (comma-separated numbers):"
    echo ""
    print_menu_options KERNEL_OPTIONS
    echo ""
    echo "Example: 1,2,3 for all, or 1,3 for mainline and LTS"
    echo ""

    # determine default choice based on config
    local default_nums=""
    for kernel in "${SELECTED_KERNELS[@]}"; do
        local num
        num=$(get_menu_number KERNEL_OPTIONS "$kernel")
        [[ -n "$num" ]] && default_nums="${default_nums}${num},"
    done
    default_nums="${default_nums%,}"  # Remove trailing comma

    local choice
    read -rp "Select kernels [1-3, default=$default_nums]: " choice

    # use config default if no input
    if [[ -z "$choice" ]]; then
        echo "Selected kernels: ${SELECTED_KERNELS[*]}"
        return 0
    fi

    # parse user selection
    SELECTED_KERNELS=()
    IFS=',' read -ra selections <<< "$choice"
    for sel in "${selections[@]}"; do
        sel=$(echo "$sel" | tr -d ' ')
        local kernel_value
        kernel_value=$(get_menu_value KERNEL_OPTIONS "$sel")
        [[ -n "$kernel_value" ]] && SELECTED_KERNELS+=("$kernel_value")
    done

    # ensure at least one kernel
    if [[ ${#SELECTED_KERNELS[@]} -eq 0 ]]; then
        SELECTED_KERNELS=("linux")
    fi

    echo "Selected kernels: ${SELECTED_KERNELS[*]}"
}

# select desktop environments (can install multiple)
select_desktop_environments() {
    # default: kde only
    if [[ ${#SELECTED_DESKTOPS[@]} -eq 0 ]]; then
        SELECTED_DESKTOPS=("kde")
    fi

    # skip for minimal profile
    if [[ "${PACKAGE_PROFILE:-base}" == "minimal" ]]; then
        SELECTED_DESKTOPS=()
        return 0
    fi

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        return 0
    fi

    echo ""
    echo "===== Desktop Environment Selection ====="
    echo "Current selection: ${SELECTED_DESKTOPS[*]:-none}"
    echo ""
    echo "Select which desktop environment(s) to install:"
    echo "(you can install multiple and choose at login via SDDM)"
    echo ""
    print_menu_options DESKTOP_OPTIONS
    echo ""
    echo "Examples: 1 for GNOME only, 1,2 for GNOME+KDE, 4 for all three"
    echo ""

    local choice
    read -rp "Select desktop(s) [0-4, default=2 (KDE)]: " choice
    choice="${choice:-2}"

    # handle special cases
    case "$choice" in
        0)
            SELECTED_DESKTOPS=()
            echo "No desktop environment selected (headless/minimal)."
            return 0
            ;;
        4)
            SELECTED_DESKTOPS=("gnome" "kde" "hyprland")
            echo "Selected desktops: all (GNOME, KDE, Hyprland)"
            return 0
            ;;
    esac

    # parse comma-separated selections
    SELECTED_DESKTOPS=()
    IFS=',' read -ra selections <<< "$choice"
    for sel in "${selections[@]}"; do
        sel=$(echo "$sel" | tr -d ' ')
        local desktop_value
        desktop_value=$(get_menu_value DESKTOP_OPTIONS "$sel")
        if [[ -n "$desktop_value" && "$desktop_value" != "all" && "$desktop_value" != "none" ]]; then
            SELECTED_DESKTOPS+=("$desktop_value")
        fi
    done

    if [[ ${#SELECTED_DESKTOPS[@]} -eq 0 ]]; then
        SELECTED_DESKTOPS=("kde")
    fi

    echo "Selected desktops: ${SELECTED_DESKTOPS[*]}"
}

# select debug UKI variants (for GPU/system troubleshooting)
select_debug_variants() {
    # get config defaults - read non-default variant suffixes
    local config_variants
    config_variants=$(yq -r '.boot.variants[].suffix | select(. != "default")' "$CFG" 2>/dev/null | tr '\n' ' ' || echo "")

    # hierarchy: ENV (SELECTED_DEBUG_FLAGS already set) > Config
    if [[ ${#SELECTED_DEBUG_FLAGS[@]} -eq 0 && -n "$config_variants" ]]; then
        for variant in $config_variants; do
            SELECTED_DEBUG_FLAGS+=("$variant")
        done
    fi

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        return 0
    fi

    echo ""
    echo "===== Debug UKI Variants ====="
    if [[ ${#SELECTED_DEBUG_FLAGS[@]} -gt 0 ]]; then
        echo "Config default: ${SELECTED_DEBUG_FLAGS[*]}"
    else
        echo "Config default: none"
    fi
    echo ""
    echo "Debug variants add extra boot entries with troubleshooting flags."
    echo "These are multiplied across all selected kernels."
    echo ""
    echo "Useful for AMD APU/GPU issues (freezes, crashes, display problems)."
    echo ""
    echo "Select variants to include (comma-separated, or Enter for config default):"
    echo ""
    print_menu_options DEBUG_VARIANT_OPTIONS
    echo ""
    echo "Example: 1,2 or 0 for none"
    echo ""

    # determine default choice based on config
    local default_nums=""
    if [[ ${#SELECTED_DEBUG_FLAGS[@]} -eq 0 ]]; then
        default_nums="none"
    else
        for flag in "${SELECTED_DEBUG_FLAGS[@]}"; do
            local num
            num=$(get_menu_number DEBUG_VARIANT_OPTIONS "$flag")
            [[ -n "$num" ]] && default_nums="${default_nums}${num},"
        done
        default_nums="${default_nums%,}"
    fi

    local choice
    read -rp "Select debug variants [0-3, default=$default_nums]: " choice

    # use config default if no input
    if [[ -z "$choice" ]]; then
        if [[ ${#SELECTED_DEBUG_FLAGS[@]} -gt 0 ]]; then
            echo "Selected debug variants: ${SELECTED_DEBUG_FLAGS[*]}"
        else
            echo "No debug variants selected (only default UKIs will be created)"
        fi
        return 0
    fi

    # parse user selection
    SELECTED_DEBUG_FLAGS=()
    if [[ "$choice" != "0" ]]; then
        IFS=',' read -ra selections <<< "$choice"
        for sel in "${selections[@]}"; do
            sel=$(echo "$sel" | tr -d ' ')
            local variant_value
            variant_value=$(get_menu_value DEBUG_VARIANT_OPTIONS "$sel")
            [[ -n "$variant_value" && "$variant_value" != "none" ]] && SELECTED_DEBUG_FLAGS+=("$variant_value")
        done
    fi

    if [[ ${#SELECTED_DEBUG_FLAGS[@]} -gt 0 ]]; then
        echo "Selected debug variants: ${SELECTED_DEBUG_FLAGS[*]}"
    else
        echo "No debug variants selected (only default UKIs will be created)"
    fi
}

# select whether to enable bootable snapshots
select_snapshot_boot() {
    # get config default - check if snapper is enabled
    local config_snapper_enabled
    config_snapper_enabled=$(yq -r '.snapper.enabled // true' "$CFG" 2>/dev/null || echo "true")

    # hierarchy: ENV > Config
    local default_enabled="${ENABLE_SNAPSHOT_BOOT:-$config_snapper_enabled}"

    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        ENABLE_SNAPSHOT_BOOT="$default_enabled"
        return 0
    fi

    echo ""
    echo "===== Bootable Snapshots ====="
    echo "Config default: $default_enabled"
    echo ""
    echo "Enable bootable BTRFS snapshots in the boot menu?"
    echo ""
    echo "This creates additional UKI entries that can boot into previous"
    echo "system snapshots (useful for recovery after bad updates)."
    echo ""
    echo "Note: Requires ~50-100MB per snapshot UKI in EFI partition."
    echo ""

    local default_choice="N"
    [[ "$default_enabled" == "true" ]] && default_choice="Y"

    local choice
    read -rp "Enable bootable snapshots? [y/n, default=$default_choice]: " choice

    if [[ -z "$choice" ]]; then
        ENABLE_SNAPSHOT_BOOT="$default_enabled"
    elif [[ "${choice,,}" == "y" ]]; then
        ENABLE_SNAPSHOT_BOOT="true"
    else
        ENABLE_SNAPSHOT_BOOT="false"
    fi

    echo "Bootable snapshots: $ENABLE_SNAPSHOT_BOOT"
}

# configure UFW firewall
setup_firewall() {
    echo ">>>>> Configuring UFW firewall..."

    # install ufw if not present
    if ! arch-chroot /mnt pacman -Q ufw &>/dev/null; then
        echo "    Installing ufw..."
        arch-chroot /mnt pacman -S --noconfirm ufw
    fi

    echo "    Setting default policies..."
    arch-chroot /mnt ufw default deny incoming
    arch-chroot /mnt ufw default allow outgoing

    echo "    Enabling logging..."
    arch-chroot /mnt ufw logging on

    echo "    Blocking ICMP (ping)..."
    local before_rules="/mnt/etc/ufw/before.rules"
    if [ -f "$before_rules" ] && ! grep -q "block icmp" "$before_rules"; then
        sed -i '/-A ufw-before-input -p icmp --icmp-type destination-unreachable -j ACCEPT/d' "$before_rules"
        sed -i '/-A ufw-before-input -p icmp --icmp-type time-exceeded -j ACCEPT/d' "$before_rules"
        sed -i '/-A ufw-before-input -p icmp --icmp-type parameter-problem -j ACCEPT/d' "$before_rules"
        sed -i '/-A ufw-before-input -p icmp --icmp-type echo-request -j ACCEPT/d' "$before_rules"
    fi

    echo "    Enabling UFW..."
    arch-chroot /mnt bash -c "echo 'y' | ufw enable" || true
    arch-chroot /mnt systemctl enable ufw.service

    echo ">>>>> UFW firewall configured."
}

# install utility scripts to /usr/local/bin for system-wide access
install_utility_scripts() {
    echo ">>>>> Installing utility scripts to /usr/local/bin..."

    local scripts_dir="/mnt/usr/local/bin"
    mkdir -p "$scripts_dir"

    # verify_install.sh
    if [ -f "$SCRIPT_DIR/verify_install.sh" ]; then
        cp "$SCRIPT_DIR/verify_install.sh" "$scripts_dir/verify-install"
        chmod +x "$scripts_dir/verify-install"
        echo "    Installed: verify-install"
    fi

    # manage_snapshot_entries.sh -> refresh-snapshot-ukis
    if [ -f "$SCRIPT_DIR/manage_snapshot_entries.sh" ]; then
        cp "$SCRIPT_DIR/manage_snapshot_entries.sh" "$scripts_dir/refresh-snapshot-ukis"
        chmod +x "$scripts_dir/refresh-snapshot-ukis"
        echo "    Installed: refresh-snapshot-ukis"
    fi

    # dotfiles-sync.sh
    if [ -f "$SCRIPT_DIR/dotfiles-sync.sh" ]; then
        cp "$SCRIPT_DIR/dotfiles-sync.sh" "$scripts_dir/dotfiles-sync"
        chmod +x "$scripts_dir/dotfiles-sync"
        echo "    Installed: dotfiles-sync"
    fi

    echo ">>>>> Utility scripts installed."
}

# display step list and allow exclusions
select_steps_to_run() {
    if [[ "${NON_INTERACTIVE:-false}" == "true" ]]; then
        return 0
    fi

    echo ""
    echo "===== Installation Steps Selection ====="
    echo ""
    echo "The following steps will be executed:"
    echo ""

    for i in {1..8}; do
        local required_marker=""
        if [[ "${STEP_REQUIRED[$i]}" == "true" ]]; then
            required_marker=" [REQUIRED]"
        fi
        echo "  $i) ${STEP_NAMES[$i]}${required_marker}"
        echo "     ${STEP_DESCRIPTIONS[$i]}"
        echo ""
    done

    echo "Enter step numbers to EXCLUDE (comma-separated), or press Enter to run all."
    echo "Note: Required steps (1,2,3,5,6) cannot be excluded."
    echo ""

    local exclude_input
    read -rp "Steps to exclude (e.g., 4,7,8): " exclude_input

    if [[ -n "$exclude_input" ]]; then
        IFS=',' read -ra exclude_array <<< "$exclude_input"
        for step in "${exclude_array[@]}"; do
            step=$(echo "$step" | tr -d ' ')
            if [[ "$step" =~ ^[1-8]$ ]]; then
                if [[ "${STEP_REQUIRED[$step]}" == "true" ]]; then
                    echo "Warning: Step $step (${STEP_NAMES[$step]}) is required and cannot be excluded."
                else
                    EXCLUDED_STEPS+=("$step")
                    echo "Excluding step $step: ${STEP_NAMES[$step]}"
                fi
            fi
        done
    fi

    echo ""
}

# initial setup - collect all inputs upfront
initial_setup() {
    echo ""
    echo "================================================================================"
    echo "                    ARCH INSTALLER - INITIAL SETUP                         "
    echo "================================================================================"
    echo ""
    echo "This installer will set up Arch Linux with:"
    echo "  - Full disk encryption (LUKS2 with argon2id)"
    echo "  - BTRFS filesystem with snapshots"
    echo "  - Secure Boot support (signed UKIs)"
    echo "  - UFW firewall (optional)"
    echo ""
    echo "All inputs will be collected now. The installation will then run unattended."
    echo ""
    echo "Press Enter to continue or Ctrl+C to abort..."
    if [[ "${NON_INTERACTIVE:-false}" != "true" ]]; then
        read -r
    fi

    # get defaults from config
    local default_hostname
    local default_username
    local default_timezone
    default_hostname=$(yq -r '.system.hostname // "archlinux"' "$CFG" 2>/dev/null || echo "archlinux")
    default_username=$(yq -r '.system.user.name // "user"' "$CFG" 2>/dev/null || echo "user")
    default_timezone=$(yq -r '.system.timezone // "Europe/Paris"' "$CFG" 2>/dev/null || echo "Europe/Paris")

    echo ""
    echo "===== Basic Configuration ====="
    echo "Config default: $default_hostname"
    read_with_default "Hostname" INSTALL_HOSTNAME "${INSTALL_HOSTNAME:-$default_hostname}"
    echo "Config default: $default_username"
    read_with_default "Username" INSTALL_USERNAME "${INSTALL_USERNAME:-$default_username}"

    select_timezone "$default_timezone"

    echo ""
    echo "===== Disk Encryption Password ====="
    echo "This password encrypts your entire disk. Required at every boot."
    read_password "Disk encryption password" LUKS_PASSWORD

    echo ""
    echo "===== User Account Password ====="
    echo "Password for user '$INSTALL_USERNAME' (used for login and sudo)."
    read_password "User password" USER_PASSWORD

    select_swap_size

    select_cpu_vendor

    select_gpu_vendor

    select_kernels

    select_desktop_environments

    select_debug_variants

    select_snapshot_boot

    select_steps_to_run

    # export for use by other scripts
    export LUKS_PASSWORD USER_PASSWORD INSTALL_HOSTNAME INSTALL_USERNAME INSTALL_TIMEZONE SWAP_SIZE_MB
    export CPU_VENDOR GPU_VENDOR GPU_DRIVER
    export ENABLE_SNAPSHOT_BOOT

    echo ""
    echo "================================================================================"
    echo "                         CONFIGURATION SUMMARY                                  "
    echo "================================================================================"
    echo ""
    echo "  Hostname:  $INSTALL_HOSTNAME"
    echo "  Username:  $INSTALL_USERNAME"
    echo "  Timezone:  $INSTALL_TIMEZONE"
    echo "  Swap:      ${SWAP_SIZE_MB}MB ($((SWAP_SIZE_MB / 1024))GB)"
    echo "  CPU:       $CPU_VENDOR (${CPU_VENDOR}-ucode)"
    if [[ "$GPU_VENDOR" != "none" ]]; then
        echo "  GPU:       $GPU_VENDOR${GPU_DRIVER:+ ($GPU_DRIVER)}"
    else
        echo "  GPU:       None (generic drivers)"
    fi
    echo ""
    echo "  Kernels:   ${SELECTED_KERNELS[*]}"
    if [[ ${#SELECTED_DESKTOPS[@]} -gt 0 ]]; then
        echo "  Desktops:  ${SELECTED_DESKTOPS[*]}"
    else
        echo "  Desktops:  None (headless)"
    fi
    if [[ ${#SELECTED_DEBUG_FLAGS[@]} -gt 0 ]]; then
        echo "  Debug UKIs: ${SELECTED_DEBUG_FLAGS[*]}"
    else
        echo "  Debug UKIs: None"
    fi
    echo "  Snapshot boot: $ENABLE_SNAPSHOT_BOOT"
    echo ""

    if [[ ${#EXCLUDED_STEPS[@]} -gt 0 ]]; then
        echo "  Excluded steps:"
        for step in "${EXCLUDED_STEPS[@]}"; do
            echo "    - $step) ${STEP_NAMES[$step]}"
        done
        echo ""
    fi

    echo "  Steps to run:"
    for i in {1..8}; do
        if ! is_step_excluded "$i"; then
            echo "    $i) ${STEP_NAMES[$i]}"
        fi
    done
    echo ""

    if [[ "${NON_INTERACTIVE:-false}" != "true" ]]; then
        echo "Press Enter to begin installation or Ctrl+C to abort..."
        read -r
    fi
}

# main script starts here
echo ""
echo "================================================================================"
echo "                               ARCH INSTALLER                                   "
echo "                      Arch Linux Installation as Code                           "
echo "================================================================================"
echo ""

# check for required tools
if ! command -v yq &> /dev/null; then
    echo ">>>>> Installing yq (required for config parsing)..."
    pacman -Sy --noconfirm yq || { echo "Failed to install yq."; exit 1; }
fi

# run initial setup to collect all credentials and settings
initial_setup

# determine target disk
TARGET_DISK="${TARGET_DISK:-}"

echo ""
echo "================================================================================"
echo "                         BEGINNING INSTALLATION                                 "
echo "================================================================================"

# STEP 1: disk selection & storage setup
echo ""
echo "===== STEP 1/8: ${STEP_NAMES[1]} ====="
if ! is_step_excluded "1"; then
    if mountpoint -q /mnt; then
        MOUNT_SOURCE=$(findmnt -n -o SOURCE /mnt)
        echo ">>>>> /mnt is already mounted from $MOUNT_SOURCE"
        echo ">>>>> Skipping disk selection as filesystem is mounted."

        if ! mountpoint -q /mnt/efi; then
            echo "Warning: /mnt/efi not mounted. Please ensure it is mounted."
        fi
    else
        choose_target_disk
        wipe_disk "$TARGET_DISK"
        setup_storage "$TARGET_DISK"
    fi
else
    echo ">>>>> Step excluded by user."
fi

# create vconsole.conf early to prevent mkinitcpio errors
if [ -d /mnt/etc ] && [ ! -f /mnt/etc/vconsole.conf ]; then
    echo ">>>>> Creating /etc/vconsole.conf early..."
    keymap=$(yq -r '.system.keymap // "us"' "$CFG" 2>/dev/null || echo "us")
    echo "KEYMAP=$keymap" > /mnt/etc/vconsole.conf
fi

# STEP 2: package installation
echo ""
echo "===== STEP 2/8: ${STEP_NAMES[2]} ====="
if ! is_step_excluded "2"; then
    setup_packages
else
    echo ">>>>> Step excluded by user."
fi

# STEP 3: system configuration
echo ""
echo "===== STEP 3/8: ${STEP_NAMES[3]} ====="
if ! is_step_excluded "3"; then
    setup_system
    configure_docker
else
    echo ">>>>> Step excluded by user."
fi

# STEP 4: GPU driver setup
echo ""
echo "===== STEP 4/8: ${STEP_NAMES[4]} ====="
if ! is_step_excluded "4"; then
    setup_gpu
else
    echo ">>>>> Step excluded by user."
fi

# STEP 5: mkinitcpio & UKI generation
echo ""
echo "===== STEP 5/8: ${STEP_NAMES[5]} ====="
if ! is_step_excluded "5"; then
    setup_mkinitcpio
else
    echo ">>>>> Step excluded by user."
fi

# STEP 6: bootloader setup
echo ""
echo "===== STEP 6/8: ${STEP_NAMES[6]} ====="
if ! is_step_excluded "6"; then
    setup_boot
else
    echo ">>>>> Step excluded by user."
fi

# STEP 7: snapper configuration
echo ""
echo "===== STEP 7/8: ${STEP_NAMES[7]} ====="
if ! is_step_excluded "7"; then
    if [[ "${PACKAGE_PROFILE:-base}" != "minimal" ]]; then
        setup_snapper
    else
        echo ">>>>> Skipped (minimal profile selected)."
    fi
else
    echo ">>>>> Step excluded by user."
fi

# STEP 8: firewall configuration
echo ""
echo "===== STEP 8/8: ${STEP_NAMES[8]} ====="
if ! is_step_excluded "8"; then
    if [[ "${ENABLE_UFW:-true}" == "true" ]]; then
        setup_firewall
    else
        echo ">>>>> Skipped (ENABLE_UFW=false)."
    fi
else
    echo ">>>>> Step excluded by user."
fi

# install utility scripts to /usr/local/bin
echo ""
echo ">>>>> Installing utility scripts..."
install_utility_scripts

echo ""
echo "================================================================================"
echo "                    ARCH INSTALLER - INSTALLATION COMPLETE                      "
echo "================================================================================"
echo ""
echo "Installation finished successfully!"
echo ""
echo "Next steps:"
echo "  1. Set root password (optional): arch-chroot /mnt passwd"
echo "  2. Unmount and reboot:"
echo "       swapoff -a"
echo "       umount -R /mnt"
echo "       reboot"
echo "  3. After reboot, run verification:"
echo "       verify-install"
echo ""
echo "Installed utilities (available after reboot):"
echo "  - verify-install         Run post-install checks"
echo "  - refresh-snapshot-ukis  Regenerate bootable snapshot UKIs"
echo "  - dotfiles-sync          Backup/restore dotfiles to GitHub"
echo ""
echo "Notes:"
echo "  - If Secure Boot was not in Setup Mode, enter BIOS/UEFI setup."
echo "    Put it into Setup Mode explicitly, or delete existing PK key (Platform Key), depending on your firmware."
echo "    After rebooting into your fresh OS install, re-run setup_mkinitcpio step to sign UKIs and enroll keys."
echo ""
