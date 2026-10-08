# Configuration Reference

All settings are in `config/config.yaml`. No setting has a default in code: every value the installer uses comes from an environment variable, `config/config.yaml` or the TUI, and a setting none of them provides is reported instead of being made up.

## Where Values Come From

| Mode                            | Priority, highest first                                                                                          |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Interactive (default)           | environment variables > `config.yaml`, then the TUI shows that inherited value and may replace it              |
| Non-interactive (`NON_INTERACTIVE=true`) | environment variables > `config.yaml`                                                                  |

The order is written in one place, `INHERITANCE_ORDER` in `src/arch_installer/config/value_precedence.py`.

- **Non-interactive:** every setting needs a value from the environment or `config.yaml`. If any is missing, the installer stops before touching a disk and lists each missing setting with the sources that can provide it, for example `storage.target_disk (config.yaml or TARGET_DISK)`.
- **Interactive:** the TUI asks for the settings it has screens for; everything else must come from the environment or `config.yaml`, as above.

## A Complete Configuration

`config.yaml` must state every setting, including list-item details such as each subvolume's `nocow` and each snapper volume's limits. Unknown keys are refused with their full path, so a typo fails loudly instead of being ignored.

- `config/config.yaml` is complete except for `storage.target_disk`, which is machine-specific: give it with `TARGET_DISK` or pick it in the TUI.
- `tests/data/minimal_config.yaml` is the smallest complete configuration (no desktop, snapper, firewall or Docker); the QEMU tests install it.

Passwords are never written in plain text: they come from the environment, the TUI, or the encrypted `secrets` section below.

### Encrypted Passwords

Passwords can be stored encrypted in config.yaml, so an installation needs only the key. Each password is encrypted with AES-256-GCM under a key derived from your secrets key with Argon2id (256 MiB, 3 iterations, a random salt per password), and is bound to its setting so the two cannot be swapped:

```bash
make encrypt-secrets   # asks for the key (twice) and both passwords, without echo
make decrypt-secrets   # asks for the key and prints the passwords
```

Secrets encrypted before October 2026 used a fast unsalted SHA-256 key and are refused with a request to run `make encrypt-secrets` again.

`encrypt-secrets` edits only the two `secrets` lines and keeps the rest of the file, comments included. Pressing Enter at a password prompt keeps the stored one, which is only allowed when it was encrypted with the same key. Add `NO_WRITE=true` to print the encrypted values without touching the file, and `CONFIG_PATH=...` to target another file.

### Keeping Secrets Out of ps and Shell History

A secret typed into a command line leaks twice: `/proc/<pid>/cmdline` is readable by every user (that is what `ps` shows), and the line is saved in your shell history. A process environment (`/proc/<pid>/environ`) is readable only by its owner and root, so environment variables are safe as long as their value is not typed on the command line.

- The Makefile refuses `ARCH_INSTALLER_SECRETS_KEY`, `LUKS_PASSWORD`, `USER_PASSWORD` and `SOURCE_LUKS_PASSWORD` given as make arguments (`make install LUKS_PASSWORD=...`).
- `make encrypt-secrets`, `make decrypt-secrets` and an interactive `make install` ask for what they need, without echo.
- For non-interactive installs, read the value without echo into the environment, or take it from a password manager:

```bash
read -rsp 'Secrets key: ' ARCH_INSTALLER_SECRETS_KEY && export ARCH_INSTALLER_SECRETS_KEY
export ARCH_INSTALLER_SECRETS_KEY="$(keepassxc-cli show -s -a Password vault.kdbx dali)"
NON_INTERACTIVE=true make install
```

The installer itself hands every password to `cryptsetup` and `chpasswd` on stdin, never as an argument, and does not log it.

## Interactive Prompts

Unless `NON_INTERACTIVE=true` is set, the installer opens a curses TUI after reading the environment and `config.yaml`. Every screen starts on the inherited value and says where it came from, for example `(inherited from environment)`: Enter keeps it, any other choice replaces it. A setting without an inherited value starts with nothing selected; a feature toggle without one shows `?` and must be set before continuing. The summary screen lists every value with its source (`environment`, `config.yaml` or `TUI`).

### Screen Sequence

1. **Installation Type** - fresh installation or migration from an existing Arch install
2. **System Configuration** - hostname, username, timezone, keymap. Typing replaces the inherited value, Esc restores it
3. **Password Setup** - LUKS and user passwords. An inherited password (encrypted secrets or environment) is never shown: keep it or enter a new one
4. **Disk Selection** - the detected disks, starting on `storage.target_disk`
5. **Disk Wipe Method** - quick, secure (random fill), SSD discard, or skip
6. **USB Boot Drive** - and the USB device when enabled
7. **CPU Vendor** - for microcode
8. **GPU Vendor** - and the NVIDIA driver when applicable
9. **Desktop Environments** - any combination, Space toggles
10. **Swap Size** - presets from 4 to 64 GB, the inherited size even when it is not a preset, or no swap
11. **Features** - hibernation, firewall, bootable snapshots, Docker, desktop notifications. A toggle you flip shows its inherited state next to it
12. **Source disk password** - migration only
13. **Configuration Summary** - review, then `y` to install or `n` to cancel

Ctrl+C quits from any screen (`q` also quits from menus); a cancelled setup exits with status 130 and installs nothing.

## Desktop Environments

The installer supports **multi-desktop** installation. You can install one or more desktop environments and switch between them at login via SDDM.

| Desktop  | Description                       |
| -------- | --------------------------------- |
| GNOME    | Wayland, modern, intuitive        |
| KDE      | Wayland, highly customizable      |
| Hyprland | Wayland tiling WM for power users |

The desktops to install are listed in `packages.selected_desktops` (each needs packages under `packages.desktops`; an empty list installs none). `SELECTED_DESKTOPS=gnome,kde` overrides that list, and the **Desktop Environments** screen of the TUI shows it ticked and lets you change it (Space toggles, Enter confirms).

The packages in `packages.display_manager` (SDDM by default) are installed and enabled alongside them.

## System Settings

```yaml
system:
  hostname: archrog
  timezone: Europe/Paris
  cpu_vendor: amd # or intel, for microcode
  locale:
    language: en_US
    encoding: UTF-8
    keymap: us
  user:
    name: user
    groups: [wheel]
```

## Storage Configuration

```yaml
storage:
  target_disk: /dev/nvme0n1 # Or use TARGET_DISK env var
  efi_size_mb: 2048 # 2GB for UKIs + snapshots

  luks:
    type: luks2
    cipher: aes-xts-plain64
    key_size: 512
    hash: sha512
    pbkdf: argon2id
    pbkdf_memory: 1048576 # 1GB memory cost
    pbkdf_parallel: 4
    pbkdf_time_ms: 4000

  btrfs:
    label: archroot
    mount_options: compress=zstd,noatime
    subvolumes:
      - name: '@'
        mountpoint: /
      # See config.yaml for full list

  swap:
    enabled: true
    size_mb: 32768 # 32GB for hibernation
    path: /.swap/swapfile
```

### Disabling Swapfile

```yaml
storage:
  swap:
    enabled: false
```

Or at runtime: `ENABLE_SWAP=false make install`

## Boot Configuration

```yaml
boot:
  kernels:
    - name: hardened
      package: linux-hardened
    - name: mainline
      package: linux
    - name: lts
      package: linux-lts

  # every kernel gets a "default" UKI; each extra variant adds one more UKI per kernel
  # (about 40 MB each on the ESP) with the extra cmdline parameters
  variants:
    - suffix: default
      params: ''
    - suffix: no-dc
      params: 'amdgpu.dc=0'
    - suffix: debug
      params: 'debug loglevel=7'
```

## GPU Configuration

GPU drivers are selected during installation. Packages are defined in `config.yaml`:

```yaml
gpu:
  enabled: false
  vendor: nvidia # nvidia, amd, intel, none
  driver: nouveau # nvidia: nouveau/nvidia-dkms/nvidia-open

  # Packages per driver
  drivers:
    amd:
      - mesa
      - vulkan-radeon
      - libva-mesa-driver
      - mesa-vdpau
      - xf86-video-amdgpu
    intel:
      - mesa
      - vulkan-intel
      - intel-media-driver
      - libva-intel-driver
    nouveau:
      - mesa
      - xf86-video-nouveau
    nvidia_dkms:
      - nvidia-dkms
      - nvidia-utils
      - nvidia-settings
      - libva-nvidia-driver
    nvidia_open:
      - nvidia-open-dkms
      - nvidia-utils
      - nvidia-settings
```

## Package Configuration

All packages are declared in `config.yaml`. The installer supports two profiles (you can also define your own):

- **base**: Full installation with all utilities, audio, apps

```yaml
packages:
  profile: base

  base:
    # Core system, kernels, firmware, utilities, apps
    - base
    - linux
    - linux-headers
    # ...

  desktops:
    kde:
      - plasma
      - kde-applications
    gnome:
      - gnome
    hyprland:
      - hyprland
      - waybar
      - dunst
      # ...

  display_manager:
    - sddm
```

Packages are filtered at install time based on:

- `CPU_VENDOR`: Include only matching microcode (intel-ucode or amd-ucode)
- `GPU_VENDOR`: Include only matching GPU drivers
- `SELECTED_KERNELS`: Include only selected kernel packages
- `SELECTED_DESKTOPS`: Include packages for selected desktop environments

## Docker Configuration

Docker is configured to use the dedicated `@docker` subvolume:

```yaml
docker:
  enabled: true
  storage_driver: overlay2
  data_root: /var/lib/docker # matches @docker subvolume
```

## Firewall Configuration

UFW (Uncomplicated Firewall) is configured via the `firewall` section:

```yaml
firewall:
  enabled: true
  default_incoming: deny
  default_outgoing: allow
  logging: true
  block_icmp: false

  ssh:
    enabled: false
    port: 22
    allowed_from: null # or specific IP like "192.168.1.0/24"

  allow_rules:
    - port: 8080
      protocol: tcp
```

### Firewall Options

| Field              | Description                         | Default |
| ------------------ | ----------------------------------- | ------- |
| `enabled`          | Enable UFW firewall                 | `true`  |
| `default_incoming` | Default policy for incoming traffic | `deny`  |
| `default_outgoing` | Default policy for outgoing traffic | `allow` |
| `logging`          | Enable firewall logging             | `true`  |
| `block_icmp`       | Block ICMP (ping) requests          | `false` |
| `ssh.enabled`      | Allow incoming SSH connections      | `false` |
| `ssh.port`         | SSH port number                     | `22`    |
| `ssh.allowed_from` | Restrict SSH to specific IP/subnet  | `null`  |
| `allow_rules`      | Additional ports to allow           | `[]`    |

### SSH Access

To enable SSH access:

```yaml
firewall:
  ssh:
    enabled: true
    port: 22
```

To restrict SSH to a specific network:

```yaml
firewall:
  ssh:
    enabled: true
    port: 22
    allowed_from: '192.168.1.0/24'
```

### Custom Port Rules

Add custom allow rules for specific applications:

```yaml
firewall:
  allow_rules:
    - port: 80
      protocol: tcp
    - port: 443
      protocol: tcp
    - port: 8080
      protocol: tcp
```

## Sections

Every section must be present in `config.yaml` (`system`, `storage`, `boot`, `packages`, `gpu`, `snapper`, `firewall`, `docker`, `notifications`, `sync`, `migration`, `usb_boot`, `secrets`). To turn a feature off, keep its section and set `enabled: false`. A snapper volume can be turned off with `root: null` or `home: null`.

## Snapper Configuration

Snapper settings are fully declarative:

```yaml
snapper:
  enabled: true
  allow_groups: [wheel] # groups that can manage snapshots

  root:
    subvolume: /
    timeline: true
    retention:
      hourly: 5
      daily: 7
      weekly: 4
      monthly: 6
      yearly: 2

  home:
    subvolume: /home
    timeline: true
    retention:
      hourly: 5
      daily: 7
      weekly: 4
      monthly: 3
      yearly: 1
```

## Dotfiles Sync

Dotfiles sync is not part of config.yaml: the installed `dotfiles-sync` tool keeps its own settings in `~/.config/dotfiles-sync/config.yaml`, and `dotfiles-sync init <repo-url>` points it at any git server. See [Dotfiles Sync](dotfiles-sync.md).

## Environment Variables

The settings below can also come from environment variables. A variable that is set beats `config.yaml`; in interactive mode the TUI then shows it as the inherited value, marked `(inherited from environment)`. Each variable and the setting it provides are listed in one table, `ENVIRONMENT_SETTINGS` in `config/environment.py`. The last column names what is used when the variable is not set.

### Installer Switches

| Variable          | Description                                        | When not set                     |
| ----------------- | -------------------------------------------------- | -------------------------------- |
| `CONFIG_PATH`     | Path to config.yaml                                | the repository's `config/config.yaml` |
| `NON_INTERACTIVE` | `true` skips the TUI                               | interactive (TUI)                |
| `VERBOSE`         | `true` for debug output, `quiet` for warnings only | normal output                    |

### Storage and Kernel Settings

| Variable           | Description                                                     | When not set             |
| ------------------ | --------------------------------------------------------------- | ------------------------ |
| `TARGET_DISK`      | Target disk (e.g., `/dev/nvme0n1`)                              | `storage.target_disk`    |
| `WIPE_METHOD`      | Disk wipe method: `quick`, `secure`, `discard`, `skip`          | `storage.wipe_method`    |
| `SWAP_SIZE_MB`     | Swapfile size in MB                                             | `storage.swap.size_mb`   |
| `ENABLE_SWAP`      | `false` creates no swapfile                                     | `storage.swap.enabled`   |
| `SELECTED_KERNELS` | Comma-separated kernel packages from `boot.kernels` (e.g., `linux-lts`) | `boot.selected_kernels` |

### Passwords and Secrets

| Variable                     | Description                                             | When not set                                   |
| ---------------------------- | ------------------------------------------------------- | ---------------------------------------------- |
| `LUKS_PASSWORD`              | Disk encryption password                                | `secrets.luks_password_encrypted`, or the TUI  |
| `USER_PASSWORD`              | User account password                                   | `secrets.user_password_encrypted`, or the TUI  |
| `SOURCE_LUKS_PASSWORD`       | Password of the existing LUKS volume (migration)        | the TUI                                        |
| `ARCH_INSTALLER_SECRETS_KEY` | Key that unlocks the encrypted passwords in config.yaml | asked for when interactive                     |
| `NO_WRITE`                   | `true`: `make encrypt-secrets` prints instead of writing | config.yaml is updated                        |

### Feature Settings

| Variable               | Description                           | When not set                 |
| ---------------------- | ------------------------------------- | ---------------------------- |
| `ENABLE_FIREWALL`      | UFW firewall                          | `firewall.enabled`           |
| `ENABLE_DOCKER`        | Docker                                | `docker.enabled`             |
| `ENABLE_HIBERNATION`   | Hibernation to the swapfile           | `storage.swap.hibernation`   |
| `ENABLE_SNAPSHOT_BOOT` | Bootable BTRFS snapshots              | `boot.enable_snapshot_boot`  |
| `ENABLE_NOTIFICATIONS` | Desktop notifications for snapshots   | `notifications.enabled`      |
| `ENABLE_MIGRATION`     | Migrate data from an existing install | `migration.enabled`          |

### Hardware Settings

| Variable     | Description                                            | When not set        |
| ------------ | ------------------------------------------------------ | ------------------- |
| `CPU_VENDOR` | CPU vendor for microcode: `intel`, `amd`               | `system.cpu_vendor` |
| `GPU_VENDOR` | GPU vendor: `amd`, `intel`, `nvidia`, `none`           | `gpu.vendor`        |
| `GPU_DRIVER` | NVIDIA driver: `nouveau`, `nvidia-open`, `nvidia-dkms` | `gpu.driver`        |

### Desktop Selection

| Variable            | Description                                          | When not set                 |
| ------------------- | ---------------------------------------------------- | ---------------------------- |
| `SELECTED_DESKTOPS` | Comma-separated desktops: `kde`, `gnome`, `hyprland` | `packages.selected_desktops` |

### USB Settings

| Variable            | Description                                                  | When not set              |
| ------------------- | ------------------------------------------------------------ | ------------------------- |
| `ENABLE_USB_BOOT`   | EFI partition and LUKS header on a USB drive                 | `usb_boot.enabled`        |
| `USB_BOOT_DEVICE`   | USB device (e.g., `/dev/sdb`)                                | `usb_boot.device`         |
| `ISO_PATH`          | Arch ISO copied to the USB drive                             | `usb_boot.iso_path`       |
| `BACKUP_CATEGORIES` | Comma-separated: `dotfiles`, `keepass`, `browser`, `system`  | `sync.backup_categories`  |

## Dual Boot with Windows

This installer is **dual-boot friendly** with Windows. However, for best results:

> **Strongly recommended**: Install Windows and Arch Linux on separate physical drives.

### Why separate drives?

- Windows updates can overwrite the EFI partition and break Linux boot
- Separate drives allow independent boot management
- Easier recovery if one OS has issues
- Re-partitioning, expanding, shrinking becomes less risky

For a short guide to transitioning from Windows and setting up dual-boot, see [Windows Transition Guide](windows-transition-guide.md).
