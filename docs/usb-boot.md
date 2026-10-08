# USB Boot Drive (Plausible Deniability Encryption)

## Overview

The USB boot drive feature stores the EFI bootloader and LUKS headers on a separate, removable USB drive. When the USB is unplugged, the internal disk contains no encryption markers — it appears as random data. The UEFI firmware falls through to the next boot entry (typically Windows), making it impossible to prove the disk contains an encrypted operating system.

This is commonly known as **Plausible Deniability Encryption (PDE)**.

## How It Works

### Boot Flow

```
USB plugged in  → UEFI boots from USB EFI → systemd-boot → UKI → Arch Linux
USB unplugged   → UEFI skips USB → falls through to 2nd entry → Windows
```

### USB Drive Layout

| Partition | Label      | Filesystem | Contents                                                     |
| --------- | ---------- | ---------- | ------------------------------------------------------------ |
| 1         | USBBOOT    | vfat       | systemd-boot, UKIs, loader entries, signed EFIs              |
| 2         | ARCHISO    | ext4       | Live Arch Linux ISO for recovery                             |
| 3         | LUKSHEADER | ext4       | Detached LUKS header backup from the main disk               |
| 4         | USBBACKUP  | ext4       | Dotfiles, configs, KeePass DB, browser data, package catalog |

### Detached LUKS Header

The LUKS2 header (first ~16 MB of the encrypted partition) contains all the metadata needed to identify and unlock the volume. By moving the header to the USB drive and overwriting the original with random data:

- The internal disk becomes indistinguishable from a randomly-wiped drive
- No tools (including `cryptsetup`, `file`, `blkid`) can detect encryption
- The kernel command line references the external header via `rd.luks.options=<uuid>=header=/luks_header.img:LABEL=LUKSHEADER`

### Live ISO Recovery Entry

The USB EFI partition includes a signed live Arch ISO bootloader extracted from the ISO at `EFI/recovery/archiso.efi`. This creates a systemd-boot menu entry (`Arch Linux Live ISO (Recovery)`) that boots directly into the live environment without needing a separate USB stick.

All EFI binaries on the USB — including the recovery ISO, the systemd-boot binaries, and the fallback BOOTX64.EFI — are signed with the system's secure boot keys via `sbctl`. This ensures the USB drive is fully bootable with Secure Boot enabled. The signed files are:

- `EFI/BOOT/BOOTX64.EFI` — fallback bootloader
- `EFI/systemd/systemd-bootx64.efi` — systemd-boot
- `EFI/recovery/archiso.efi` — live ISO bootloader
- `EFI/Linux/*.efi` — all UKI files (relocated from internal disk)

## Configuration

### config.yaml

```yaml
usb_boot:
  enabled: true
  device: /dev/sdb # the USB drive (overridable via USB_BOOT_DEVICE env var)
  efi_size_mb: 512 # EFI partition size
  iso_partition_size_mb: 1024 # must fit the Arch ISO (~800 MB)
  iso_path: /path/to/archlinux.iso
  detached_luks_header: true # move LUKS header from main disk to USB
  backup_partition_size_mb: 0 # 0 = use remaining space
```

### Backup Configuration

The backup is configured in the `sync` section:

```yaml
sync:
  # built-in categories to back up (all four when omitted)
  backup_categories: [dotfiles, keepass, browser, system]

  # custom items, in addition to the categories
  backup_items:
    - name: ssh_keys
      source_path: ~/.ssh
      description: SSH keys and config
    - name: gpg_keys
      source_path: ~/.gnupg
      description: GPG keyring
```

Built-in backup categories:

- **dotfiles**: shell configs (zsh, bash), editor configs (nvim, vscode), DE configs (hyprland, waybar)
- **keepass**: KeePassXC database and config files
- **browser**: Firefox and Chromium profiles
- **system**: pacman.conf, makepkg.conf, mkinitcpio.conf

### Package Catalog

When backing up, the system's explicitly installed packages are cataloged by name in `package_catalog.yaml`, in the same shape as config.yaml:

```yaml
packages:
  cataloged:
    - firefox
    - neovim
```

Copied into config.yaml, the cataloged packages are installed together with `packages.base`.

### Environment Variables

| Variable            | Description                                    |
| ------------------- | ---------------------------------------------- |
| `ENABLE_USB_BOOT`   | Override `usb_boot.enabled` (`true` / `false`) |
| `USB_BOOT_DEVICE`   | Override `usb_boot.device` (e.g., `/dev/sdb`)  |
| `USB_DEVICE`        | USB device for `init_to_usb` / `backup_to_usb` |
| `ISO_PATH`          | ISO path for `init_to_usb`                     |
| `BACKUP_CATEGORIES` | Comma-separated categories for backup          |

### Standalone USB Operations

These operations work independently of installation:

```bash
# initialize a USB drive from scratch (partitions + ISO + backup)
make init_to_usb USB_DEVICE=/dev/sdb ISO_PATH=/path/to/archlinux.iso

# update backup on existing USB (no reformatting - append only)
make backup_to_usb USB_DEVICE=/dev/sdb

# back up only specific categories
make backup_to_usb USB_DEVICE=/dev/sdb BACKUP_CATEGORIES=dotfiles,keepass
```

Both commands open an interactive TUI by default. Set `NON_INTERACTIVE=true` to skip the TUI.

### TUI Selection

When running the interactive TUI installer, a menu appears after disk wipe method selection:

1. **USB Boot Drive** — choose whether to enable USB-based PDE boot
2. **USB Device Selection** — if enabled, select which drive to use (the target disk is filtered out)

## Prerequisites

- A spare USB drive (2+ GB recommended)
- An Arch Linux ISO (for the recovery partition)
- Secure Boot in Setup Mode (for key enrollment and signing)
- The USB drive must **not** be the installation target disk

## Security Considerations

### What PDE Protects Against

- **Casual forensic inspection**: the internal disk shows no partition table entries for LUKS, no recognizable encryption headers, and no boot entries
- **Border crossing / customs**: a machine that boots straight into Windows with an internal disk that passes casual inspection
- **Disk seizure**: without the USB drive, the encrypted data is cryptographically inaccessible (no header = no key slots)

### What PDE Does NOT Protect Against

- **Sophisticated forensic analysis**: entropy analysis of the full disk will show it contains high-entropy data (indistinguishable from a secure-wiped drive, but suspicious to an expert with a warrant)
- **Rubber hose attacks**: no technical solution protects against coercion
- **USB drive seizure**: if both the USB and the laptop are seized, PDE is defeated
- **UEFI boot order inspection**: a forensics team examining NVRAM variables may find a USB boot entry (mitigated by `--no-variables` flag during `bootctl install`)

### Recommendations

- Keep the USB drive separate from the laptop physically
- The `wipe_method: secure` option fills the entire disk with random data before encryption, which prevents distinguishing encrypted data from wiped space

## Troubleshooting

### System Won't Boot After USB Setup

1. Verify the USB drive is plugged in and the UEFI boot order lists it first
2. Check that the USB EFI partition contains the expected files:
   ```bash
   mount /dev/sdX1 /mnt/usb-efi
   ls /mnt/usb-efi/EFI/Linux/      # UKIs
   ls /mnt/usb-efi/EFI/BOOT/       # BOOTX64.EFI
   ls /mnt/usb-efi/loader/          # loader.conf + entries/
   ```
3. Verify secure boot signing: `sbctl verify /mnt/usb-efi/EFI/Linux/*.efi`

### LUKS Header Not Found

If the kernel fails to find the detached LUKS header during boot:

1. Check the LUKS header partition is labeled `LUKSHEADER`:
   ```bash
   blkid /dev/sdX3
   ```
2. Verify the header file exists:
   ```bash
   mount /dev/sdX3 /mnt/usb-header
   ls -la /mnt/usb-header/luks_header.img
   ```
3. Check the kernel command line includes the header reference:
   ```bash
   cat /proc/cmdline | grep rd.luks.options
   ```

### Recovery ISO Won't Boot

1. Check the recovery EFI exists: `ls /mnt/usb-efi/EFI/recovery/archiso.efi`
2. Verify it is signed: `sbctl verify /mnt/usb-efi/EFI/recovery/archiso.efi`
3. Check the boot entry: `cat /mnt/usb-efi/loader/entries/archiso-recovery.conf`

### Backup Partition Issues

1. Verify the backup partition exists and is labeled correctly:
   ```bash
   blkid /dev/sdX4  # should show LABEL="USBBACKUP"
   ```
2. Mount and inspect the backup contents:
   ```bash
   mount /dev/sdX4 /mnt/usb-backup
   ls /mnt/usb-backup/          # dotfiles/ keepass/ browser/ config/ manifest.yaml
   cat /mnt/usb-backup/manifest.yaml
   ```
3. Check the package catalog:
   ```bash
   cat /mnt/usb-backup/config/package_catalog.yaml
   ```
