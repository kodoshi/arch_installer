# Configuration Reference

All settings are in `config/config.yaml`. The installer has sensible defaults.

## Essential Configuration

These fields configure the base system. All can be set via config.yaml or prompted interactively:

| Field (yaml or env var)                              | Description                     | Required/Optional             |
| ---------------------------------------------------- | ------------------------------- | ----------------------------- |
| `system.hostname`                                    | System hostname                 | Interactive prompt if not set |
| `system.timezone`                                    | Timezone (e.g., `Europe/Paris`) | Interactive prompt if not set |
| `system.user.name`                                   | Primary user account name       | Interactive prompt if not set |
| `storage.*`                                          | LUKS/BTRFS settings             | Required (defaults provided)  |
| `boot.kernels`                                       | Kernel packages to install      | Required (defaults provided)  |
| `boot.hooks`                                         | mkinitcpio hooks                | Required (defaults provided)  |
| `TARGET_DISK` or `storage.target_disk`               | Target disk for installation    | Interactive prompt if not set |
| `LUKS_PASSWORD` or `secrets.encrypted_luks_password` | Disk encryption password        | Interactive prompt if not set |
| `USER_PASSWORD` or `secrets.encrypted_user_password` | User account password           | Interactive prompt if not set |

For a complete reference of available variables, see the [Development documentation](development.md).

### Minimal Working Configuration

You can start with an **empty** config and the installer will prompt for everything:

```yaml
# config/config.yaml - truly minimal, all else prompted
```

Or pre-configure just the essentials:

```yaml
system:
  hostname: myhost
  timezone: Europe/Paris
  user:
    name: myuser
```

Everything else has sensible defaults. The installer will prompt for:

- **System config** - hostname, username, timezone (if not set in config.yaml)
- **Target disk** - if not set via `storage.target_disk` or `TARGET_DISK` env var
- **LUKS password** - if not set via `LUKS_PASSWORD` env var or encrypted in config
- **User password** - if not set via `USER_PASSWORD` env var or encrypted in config
- **CPU vendor** - Intel or AMD (for microcode)
- **GPU vendor** - AMD, Intel, NVIDIA, or none
- **Desktop environment** - GNOME, KDE, Hyprland, all, or none
- **Swap size** - predefined sizes, match RAM, custom, or disabled
- **Hibernation** - enable/disable
- **Firewall (UFW)** - enable/disable
- **Bootable snapshots** - enable/disable
- **Docker** - enable/disable

For encrypted password storage, use:

- `make encrypt-secrets SECRETS_KEY=your-key` to encrypt passwords in config.yaml
- `make decrypt-secrets SECRETS_KEY=your-key` to decrypt passwords for viewing

## Interactive Prompts

When not running in `NON_INTERACTIVE=true` mode, the installer provides interactive prompts.

### Full Interactive Prompt Sequence

1. **System Configuration** (if minimal config detected - hostname, username, timezone not set)
   - Hostname
   - Username
   - Timezone
2. **Password Configuration** (if not provided via env vars or encrypted config)
3. **Disk Selection** (with double confirmation for safety)
4. **CPU Vendor** - for microcode selection
5. **GPU Vendor** - and NVIDIA driver if applicable
6. **Desktop Environment** - single, multiple, or none
7. **Swap Size** - 8/16/32/64GB, match RAM, custom, or disabled
8. **Hibernation** - y/n
9. **Firewall (UFW)** - y/n (default: yes)
10. **Bootable Snapshots** - y/n (default: no)
11. **Docker** - y/n (default: no)
12. **Final Confirmation** - review and proceed

### Disk Selection

If `TARGET_DISK` is not set and `storage.target_disk` is not defined in config.yaml:

```
============================================================
                    DISK SELECTION
============================================================

Available disks:

  [0] /dev/nvme0n1
      Model: Samsung SSD 980 PRO
      Size:  1T
      Partitions:
        nvme0n1     1T
        nvme0n1p1   512M vfat  /boot/efi
        nvme0n1p2   999.5G crypto_LUKS

  [1] /dev/sda
      Model: WDC WD40EZRZ
      Size:  4T
      Partitions:
        sda         4T
        sda1        4T ext4  /mnt/data

Select disk index to WIPE (enter the number): 0

Selected: /dev/nvme0n1
          Samsung SSD 980 PRO (1T)

*** WARNING: ALL DATA ON THIS DISK WILL BE DESTROYED ***

Type '/dev/nvme0n1' to confirm: /dev/nvme0n1
Type 'WIPE-DISK' to proceed: WIPE-DISK

Disk /dev/nvme0n1 selected for installation.
```

## Desktop Environments

The installer supports **multi-desktop** installation. You can install one or more desktop environments and switch between them at login via SDDM.

| Desktop  | Description                       |
| -------- | --------------------------------- |
| GNOME    | Wayland, modern, intuitive        |
| KDE      | Wayland, highly customizable      |
| Hyprland | Wayland tiling WM for power users |

During installation, select your preferred desktop(s):

- Single selection: `1` for GNOME, `2` for KDE, `3` for Hyprland
- Multiple: `1,2` for GNOME + KDE
- All three: `4` for all desktops

SDDM (display manager) is automatically included when any desktop is selected.

## System Settings

```yaml
system:
  hostname: archrog
  timezone: Europe/Paris
  locale: en_US.UTF-8
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

Or at runtime: `SKIP_SWAP=true make install`

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

  variants:
    - suffix: ''
      cmdline_extra: ''
    - suffix: '-no-dc'
      cmdline_extra: 'amdgpu.dc=0'
    - suffix: '-debug'
      cmdline_extra: 'debug loglevel=7'
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

## Optional Configuration Sections

The following sections are optional and can be omitted entirely from config.yaml:

- `docker` - If missing, Docker installation is disabled
- `dotfiles` - If missing, dotfiles sync is disabled
- `snapper` - If missing, Snapper snapshots are disabled
- `firewall` - If missing, UFW firewall is disabled
- `migration` - If missing, migration from existing install is disabled

When a section is omitted, the feature defaults to disabled. This allows minimal configurations where only needed features are defined.

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

Configure dotfiles sync to work with any git server:

```yaml
dotfiles:
  # supports any git server, local or cloud: Github, Gitlab, Gitea etc.
  remote_url: git@github.com:username/dotfiles.git
  repo_path: ~/.dotfiles-repo
```

## Environment Variables

All settings can be controlled via environment variables for automated or non-interactive installations.

### Core Installation Variables

| Variable            | Description                                            | Default     |
| ------------------- | ------------------------------------------------------ | ----------- |
| `TARGET_DISK`       | Override target disk (e.g., `/dev/nvme0n1`)            | From config |
| `WIPE_METHOD`       | Disk wipe method: 1=quick, 2=secure, 3=discard, 4=skip | Interactive |
| `SKIP_SWAP`         | Skip swapfile creation                                 | `false`     |
| `PACKAGE_PROFILE`   | Package profile: `minimal`, `base`, `desktop`          | `base`      |
| `NON_INTERACTIVE`   | Skip all interactive prompts                           | `false`     |
| `VERBOSE`           | Enable verbose command output                          | `false`     |
| `TEST_SWAP_SIZE_MB` | Override swap size in MB (for testing)                 | From config |

### Password and Secrets Variables

| Variable                     | Description                                                     | Default     |
| ---------------------------- | --------------------------------------------------------------- | ----------- |
| `LUKS_PASSWORD`              | Disk encryption password (plaintext)                            | Interactive |
| `USER_PASSWORD`              | User account password (plaintext)                               | Interactive |
| `ARCH_INSTALLER_SECRETS_KEY` | Symmetric key for decrypting encrypted secrets from config.yaml | None        |

### Encrypted Secrets in Config

For automated deployments, passwords can be stored encrypted in `config.yaml`:

```yaml
secrets:
  luks_password_encrypted: '<base64-encrypted-blob>'
  user_password_encrypted: '<base64-encrypted-blob>'
```

Encrypt passwords using AES-256-GCM:

```python
from arch_installer.core.secrets import encrypt_secret
key = "your-secret-key"
encrypted = encrypt_secret("your-password", key)
```

At runtime, set `ARCH_INSTALLER_SECRETS_KEY=your-secret-key` to decrypt.

### Feature Toggle Variables

| Variable               | Description                                   | Default |
| ---------------------- | --------------------------------------------- | ------- |
| `ENABLE_SNAPSHOT_BOOT` | Enable bootable BTRFS snapshots               | `false` |
| `ENABLE_UFW`           | Enable UFW firewall configuration             | `true`  |
| `ENABLE_MIGRATION`     | Migrate data from existing installation       | `false` |
| `SOURCE_LUKS_PASSWORD` | Password to decrypt existing LUKS (migration) | -       |

### Hardware Detection Variables

| Variable           | Description                                  | Default         |
| ------------------ | -------------------------------------------- | --------------- |
| `CPU_VENDOR`       | CPU vendor for microcode: `intel`, `amd`     | Auto-detected   |
| `GPU_VENDOR`       | GPU vendor: `nvidia`, `amd`, `intel`, `none` | Interactive     |
| `GPU_DRIVER`       | GPU driver selection (e.g., `nvidia-dkms`)   | Per vendor      |
| `SELECTED_KERNELS` | Comma-separated kernel packages to install   | All from config |

### Desktop Selection Variables

| Variable            | Description                                          | Default     |
| ------------------- | ---------------------------------------------------- | ----------- |
| `SELECTED_DESKTOPS` | Comma-separated desktops: `kde`, `gnome`, `hyprland` | Interactive |

## Dual Boot with Windows

This installer is **dual-boot friendly** with Windows. However, for best results:

> **Strongly recommended**: Install Windows and Arch Linux on separate physical drives.

### Why separate drives?

- Windows updates can overwrite the EFI partition and break Linux boot
- Separate drives allow independent boot management
- Easier recovery if one OS has issues
- Re-partitioning, expanding, shrinking becomes less risky

For a short guide to transitioning from Windows and setting up dual-boot, see [Windows Transition Guide](windows-transition-guide.md).
