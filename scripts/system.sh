#!/usr/bin/env bash
set -Eeuo pipefail

# system configuration - hostname, timezone, locale, user creation

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${SCRIPT_DIR}/../config/config.yaml"

setup_system() {
    echo ">>>>> Converging system configuration..."

    if [ ! -f "$CFG" ]; then
        echo "Error: Config file not found at $CFG"
        exit 1
    fi

    # use values from environment (set in install.sh) or fall back to config
    local hostname="${INSTALL_HOSTNAME:-}"
    local username="${INSTALL_USERNAME:-}"
    local user_pass="${USER_PASSWORD:-}"
    local timezone="${INSTALL_TIMEZONE:-}"

    # read remaining values from config
    local locale
    local keymap
    local user_groups

    if [[ -z "$hostname" ]]; then
        hostname=$(yq -r '.system.hostname' "$CFG")
    fi
    if [[ -z "$username" ]]; then
        username=$(yq -r '.system.user.name // ""' "$CFG")
    fi
    if [[ -z "$timezone" ]]; then
        timezone=$(yq -r '.system.timezone' "$CFG")
    fi

    locale=$(yq -r '.system.locale' "$CFG")
    keymap=$(yq -r '.system.keymap' "$CFG")
    user_groups=$(yq -r '.system.user.groups | join(",")' "$CFG" 2>/dev/null || echo "wheel")

    # validate required values
    if [[ -z "$hostname" || "$hostname" == "null" ]]; then
        echo "Error: Hostname not set"
        exit 1
    fi
    if [[ -z "$timezone" || "$timezone" == "null" ]]; then
        echo "Error: system.timezone not set in config"
        exit 1
    fi
    if [[ -z "$locale" || "$locale" == "null" ]]; then
        echo "Error: system.locale not set in config"
        exit 1
    fi
    if [[ -z "$keymap" || "$keymap" == "null" ]]; then
        echo "Error: system.keymap not set in config"
        exit 1
    fi

    # hostname
    echo "    Setting hostname: $hostname"
    echo "$hostname" > /mnt/etc/hostname

    cat > /mnt/etc/hosts << EOF
127.0.0.1   localhost
::1         localhost
127.0.1.1   ${hostname}.localdomain ${hostname}
EOF

    # timezone
    echo "    Setting timezone: $timezone"
    rm -f /mnt/etc/localtime
    ln -sf "/usr/share/zoneinfo/${timezone}" /mnt/etc/localtime
    arch-chroot /mnt hwclock --systohc 2>/dev/null || true

    # locale
    echo "    Setting locale: $locale"
    local locale_line="${locale} UTF-8"
    if [ -f /mnt/etc/locale.gen ]; then
        sed -i "s/^#\s*\(${locale}\s\)/\1/" /mnt/etc/locale.gen
        sed -i "s/^#\s*\(${locale_line}\)/\1/" /mnt/etc/locale.gen
    fi

    echo "LANG=${locale}" > /mnt/etc/locale.conf
    arch-chroot /mnt locale-gen 2>/dev/null || echo "    Warning: locale-gen failed"

    # keymap
    echo "    Setting keymap: $keymap"
    echo "KEYMAP=${keymap}" > /mnt/etc/vconsole.conf

    # user Creation
    if [[ -n "$username" ]]; then
        echo "    Creating user: $username"

        if arch-chroot /mnt id "$username" &>/dev/null; then
            echo "    User $username already exists"
        else
            arch-chroot /mnt useradd -m -G "$user_groups" -s /bin/bash "$username"
            echo "    User $username created (groups: $user_groups)"

            # enable sudo for wheel group
            if [ -f /mnt/etc/sudoers ]; then
                sed -i 's/^#\s*\(%wheel ALL=(ALL:ALL) ALL\)/\1/' /mnt/etc/sudoers
            fi
        fi

        # set user password if provided
        if [[ -n "$user_pass" ]]; then
            echo "    Setting password for $username..."
            echo "$username:$user_pass" | arch-chroot /mnt chpasswd
        else
            echo "    Note: Set password with: arch-chroot /mnt passwd $username"
        fi

        # add user to docker group if docker is enabled
        local docker_enabled
        docker_enabled=$(yq -r '.docker.enabled // false' "$CFG" 2>/dev/null || echo "false")
        if [[ "$docker_enabled" == "true" ]] && arch-chroot /mnt pacman -Q docker &>/dev/null; then
            if ! arch-chroot /mnt groups "$username" 2>/dev/null | grep -q docker; then
                echo "    Adding $username to docker group..."
                arch-chroot /mnt usermod -aG docker "$username"
            fi
        fi
    else
        echo "    No user configured, skipping user creation"
    fi

    echo ">>>>> System configuration complete."
}

configure_docker() {
    echo ">>>>> Configuring Docker..."

    # check if docker configuration is enabled
    local docker_enabled
    docker_enabled=$(yq -r '.docker.enabled // false' "$CFG")

    if [[ "$docker_enabled" != "true" ]]; then
        echo "    Docker configuration disabled, skipping."
        return 0
    fi

    # check if docker is installed
    if ! arch-chroot /mnt pacman -Q docker &>/dev/null; then
        echo "    Docker not installed, skipping configuration."
        return 0
    fi

    local storage_driver data_root
    storage_driver=$(yq -r '.docker.storage_driver // "overlay2"' "$CFG")
    data_root=$(yq -r '.docker.data_root // "/var/lib/docker"' "$CFG")

    echo "    Storage driver: $storage_driver"
    echo "    Data root: $data_root"

    # create docker config directory
    mkdir -p /mnt/etc/docker

    # create daemon.json
    cat > /mnt/etc/docker/daemon.json << EOF
{
    "storage-driver": "$storage_driver",
    "data-root": "$data_root"
}
EOF

    # enable docker service
    arch-chroot /mnt systemctl enable docker.service || echo "    Warning: Failed to enable docker"

    echo ">>>>> Docker configuration complete."
}
