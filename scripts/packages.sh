#!/usr/bin/env bash
set -Eeuo pipefail

# package installation - base system, kernels, desktop environments

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${SCRIPT_DIR}/../config/config.yaml"

# read package list from config.yaml
read_packages_from_config() {
    local path="$1"
    yq -r "$path[]" "$CFG" 2>/dev/null | grep -v '^null$' || true
}

setup_packages() {
    echo ">>>>> Reading package lists from config..."

    # determine package profile
    local profile="${PACKAGE_PROFILE:-}"
    if [[ -z "$profile" ]]; then
        if [ -f "$CFG" ]; then
            profile=$(yq -r '.packages.profile' "$CFG")
        fi
    fi
    if [[ -z "$profile" || "$profile" == "null" ]]; then
        echo "Error: Package profile not set"
        exit 1
    fi
    echo "    Profile: $profile"

    # base packages from config
    mapfile -t base_pkgs < <(read_packages_from_config ".packages.$profile")

    # filter microcode based on CPU_VENDOR selection
    local cpu_vendor="${CPU_VENDOR:-}"
    if [[ -n "$cpu_vendor" ]]; then
        echo "    CPU vendor: $cpu_vendor"
        local filtered_pkgs=()
        for pkg in "${base_pkgs[@]}"; do
            case "$pkg" in
                intel-ucode)
                    [[ "$cpu_vendor" == "intel" ]] && filtered_pkgs+=("$pkg")
                    ;;
                amd-ucode)
                    [[ "$cpu_vendor" == "amd" ]] && filtered_pkgs+=("$pkg")
                    ;;
                *)
                    filtered_pkgs+=("$pkg")
                    ;;
            esac
        done
        base_pkgs=("${filtered_pkgs[@]}")
    fi

    # filter kernels based on SELECTED_KERNELS selection
    if [[ ${#SELECTED_KERNELS[@]} -gt 0 ]]; then
        echo "    Selected kernels: ${SELECTED_KERNELS[*]}"
        local kernel_filtered_pkgs=()
        for pkg in "${base_pkgs[@]}"; do
            case "$pkg" in
                linux|linux-headers)
                    # include only if "linux" is in SELECTED_KERNELS
                    for k in "${SELECTED_KERNELS[@]}"; do
                        if [[ "$k" == "linux" ]]; then
                            kernel_filtered_pkgs+=("$pkg")
                            break
                        fi
                    done
                    ;;
                linux-hardened|linux-hardened-headers)
                    for k in "${SELECTED_KERNELS[@]}"; do
                        if [[ "$k" == "linux-hardened" ]]; then
                            kernel_filtered_pkgs+=("$pkg")
                            break
                        fi
                    done
                    ;;
                linux-lts|linux-lts-headers)
                    for k in "${SELECTED_KERNELS[@]}"; do
                        if [[ "$k" == "linux-lts" ]]; then
                            kernel_filtered_pkgs+=("$pkg")
                            break
                        fi
                    done
                    ;;
                *)
                    kernel_filtered_pkgs+=("$pkg")
                    ;;
            esac
        done
        base_pkgs=("${kernel_filtered_pkgs[@]}")
    fi

    # desktop packages based on SELECTED_DESKTOPS (skip for minimal)
    local desktop_pkgs=()
    local dm_pkgs=()

    if [[ "$profile" != "minimal" && ${#SELECTED_DESKTOPS[@]} -gt 0 ]]; then
        echo "    Selected desktops: ${SELECTED_DESKTOPS[*]}"
        for desktop in "${SELECTED_DESKTOPS[@]}"; do
            case "$desktop" in
                gnome)
                    mapfile -t gnome_pkgs < <(read_packages_from_config ".packages.desktops.gnome")
                    desktop_pkgs+=("${gnome_pkgs[@]}")
                    ;;
                kde)
                    mapfile -t kde_pkgs < <(read_packages_from_config ".packages.desktops.kde")
                    desktop_pkgs+=("${kde_pkgs[@]}")
                    ;;
                hyprland)
                    mapfile -t hypr_pkgs < <(read_packages_from_config ".packages.desktops.hyprland")
                    desktop_pkgs+=("${hypr_pkgs[@]}")
                    ;;
            esac
        done
        # always include display manager when desktops are selected
        mapfile -t dm_pkgs < <(read_packages_from_config ".packages.display_manager")
    else
        echo "    (Skipping desktop packages)"
    fi

    # add GPU packages based on selection (read from config)
    local gpu_pkgs=()
    local gpu_vendor="${GPU_VENDOR:-none}"
    local gpu_driver="${GPU_DRIVER:-}"

    if [[ "$gpu_vendor" != "none" ]]; then
        echo "    GPU vendor: $gpu_vendor${gpu_driver:+ ($gpu_driver)}"

        # determine config key for driver packages
        local driver_key=""
        case "$gpu_vendor" in
            amd)
                driver_key="amd"
                ;;
            intel)
                driver_key="intel"
                ;;
            nvidia)
                case "$gpu_driver" in
                    nouveau)
                        driver_key="nouveau"
                        ;;
                    nvidia-open)
                        driver_key="nvidia_open"
                        ;;
                    nvidia-dkms)
                        driver_key="nvidia_dkms"
                        ;;
                esac
                ;;
        esac

        # read packages from config
        if [[ -n "$driver_key" ]]; then
            mapfile -t gpu_pkgs < <(yq -r ".gpu.drivers.$driver_key[]" "$CFG" 2>/dev/null || true)
            if [[ ${#gpu_pkgs[@]} -eq 0 || "${gpu_pkgs[0]}" == "null" ]]; then
                echo "    Warning: No GPU packages found in config for $driver_key"
                gpu_pkgs=()
            fi
        fi
    fi

    echo ">>>>> Installing packages..."
    echo "    Count: base=${#base_pkgs[@]}, desktop=${#desktop_pkgs[@]}, dm=${#dm_pkgs[@]}, gpu=${#gpu_pkgs[@]}"

    # clear stale locks
    rm -f /var/lib/pacman/db.lck
    mkdir -p /mnt/var/lib/pacman
    rm -f /mnt/var/lib/pacman/db.lck

    # cI workaround: disable locking
    if ! grep -q "DisableLocking" /etc/pacman.conf; then
        echo "DisableLocking" >> /etc/pacman.conf
    fi

    # combine all packages
    local all_pkgs=("${base_pkgs[@]}")
    [[ ${#desktop_pkgs[@]} -gt 0 ]] && all_pkgs+=("${desktop_pkgs[@]}")
    [[ ${#dm_pkgs[@]} -gt 0 ]] && all_pkgs+=("${dm_pkgs[@]}")
    [[ ${#gpu_pkgs[@]} -gt 0 ]] && all_pkgs+=("${gpu_pkgs[@]}")

    # create vconsole.conf BEFORE pacstrap to prevent mkinitcpio errors
    mkdir -p /mnt/etc
    if [ ! -f /mnt/etc/vconsole.conf ]; then
        echo ">>>>> Creating /etc/vconsole.conf (pre-pacstrap)..."
        local keymap
        keymap=$(yq -r '.system.keymap' "$CFG" 2>/dev/null || echo "us")
        if [[ -z "$keymap" || "$keymap" == "null" ]]; then
            keymap="us"
        fi
        echo "KEYMAP=$keymap" > /mnt/etc/vconsole.conf
    fi

    pacstrap -K /mnt --noconfirm "${all_pkgs[@]}"

    # ensure pacman.conf exists
    if [ ! -f /mnt/etc/pacman.conf ]; then
        echo ">>>>> Copying pacman.conf..."
        cp /etc/pacman.conf /mnt/etc/pacman.conf
    fi

    echo ">>>>> Generating fstab..."
    if ! grep -q "^[^#]" /mnt/etc/fstab 2>/dev/null; then
        genfstab -U /mnt >> /mnt/etc/fstab
        echo "    fstab generated."
    else
        echo "    fstab already has entries."
    fi

    echo ">>>>> Installing pacman hooks..."
    mkdir -p /mnt/etc/pacman.d/hooks
    mkdir -p /mnt/usr/local/bin

    # only install snapshot UKI refresh hooks if bootable snapshots are enabled
    local enable_snapshot_boot="${ENABLE_SNAPSHOT_BOOT:-false}"
    if [[ "$enable_snapshot_boot" == "true" ]]; then
        echo "    Installing bootable snapshot hooks..."
        if [ -f "config/pacman/hooks/95-snapshot-uki-refresh.hook" ]; then
            cp config/pacman/hooks/95-snapshot-uki-refresh.hook /mnt/etc/pacman.d/hooks/
        fi

        if [ -f "config/pacman/scripts/refresh-snapshot-ukis" ]; then
            cp config/pacman/scripts/refresh-snapshot-ukis /mnt/usr/local/bin/
            chmod +x /mnt/usr/local/bin/refresh-snapshot-ukis
        fi

        if [ -f "scripts/manage_snapshot_entries.sh" ]; then
            cp scripts/manage_snapshot_entries.sh /mnt/usr/local/bin/manage-snapshot-ukis
            chmod +x /mnt/usr/local/bin/manage-snapshot-ukis
        fi
    else
        echo "    Bootable snapshots disabled, skipping snapshot UKI hooks."
    fi

    echo ">>>>> Enabling services..."
    arch-chroot /mnt systemctl enable NetworkManager.service || exit 1

    if [[ ${#dm_pkgs[@]} -gt 0 ]] && arch-chroot /mnt pacman -Q sddm &>/dev/null; then
        arch-chroot /mnt systemctl enable sddm.service || echo "    Warning: Failed to enable sddm"
    fi

    echo ">>>>> Package installation complete."
}
