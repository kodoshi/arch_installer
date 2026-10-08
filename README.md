# Declarative Arch Linux Installer (or DALI)

An opinionated, declarative, idempotent Arch Linux desktop installer with a focus on security.

**Goal**: Define your system once in YAML, deploy anywhere, recover from anything, even yourself.

> **New to Linux?** See the [Windows Transition Guide](docs/windows-transition-guide.md) for a quick walkthrough.

## Features

| Feature                 | Description                                                      |
| ----------------------- | ---------------------------------------------------------------- |
| **LUKS2 Encryption**    | Full disk encryption with argon2id                               |
| **BTRFS Snapshots**     | 12 subvolumes, **BOOTABLE** snapshots, automatic cleanup         |
| **Secure Boot**         | Unified Kernel Images, systemd-boot, mkinitcpio, sbctl signing   |
| **Plausibly Deniable Encryption** | Optional USB boot drive: the disk holds only random-looking ciphertext, with no partition table, LUKS header or bootloader |
| **Security Hardening**  | Kernel hardening, CPU mitigations, firewall config                      |
| **Migration Support**   | Migrate existing Arch installs, preserve /home & Secure Boot keys |
| **Multiple Kernels**    | linux-hardened, mainline, LTS with variants                      |
| **Multi-Desktop**       | GNOME, KDE, Hyprland - install one or all                        |
| **Dual-Boot Ready**     | Windows-friendly (separate drives recommended)                   |
| **Hibernation Support** | Resume from swapfile on encrypted root                           |
| **Dotfiles Sync**       | Git backups of config files                                      |

## Plausibly Deniable Encryption

With the optional USB boot drive, nothing on the internal disk shows that it holds an encrypted system. The disk has no partition table, no LUKS header and no bootloader, only ciphertext in random data from the first byte to the last. To `fdisk`, `blkid` or a forensic scan it looks like a disk that was wiped with random data, and without the stick the machine has nothing to boot.

The USB stick carries everything needed to start and unlock the system:

- the EFI partition, with systemd-boot and the signed UKIs for every kernel, variant and bootable snapshot
- the detached LUKS2 header
- optionally, a signed Arch live system for recovery, which boots under Secure Boot

Kernel updates, bootable snapshots and Secure Boot work as usual, as long as the stick is plugged in. pacman refuses to touch boot files while it isn't. `make clone_usb_boot` makes a spare stick, since losing the only one loses the data.

To use it, answer "Yes" on the "USB boot drive" screen, or set `ENABLE_USB_BOOT=true` and `USB_BOOT_DEVICE`. It needs the `secure` wipe method. See [USB Boot Drive](docs/usb-boot.md) for how it works and what it doesn't hide.

## Quick Start

```bash
# From Arch ISO live environment
pacman-key --init
# glibc might need an upgrade for older ISOs
pacman -Sy --noconfirm --needed glibc git python make
git clone https://github.com/kodoshi/arch_installer.git
cd arch_installer

# Edit config (recommended)
nano config/config.yaml

# Option 1: interactive terminal UI (TUI)
make install

# Option 2: Non-interactive (pre-configure config/config.yaml with encrypted passwords,
# then give only the key, read without echo so it stays out of `ps` and the shell history)
read -rsp 'Secrets key: ' ARCH_INSTALLER_SECRETS_KEY && export ARCH_INSTALLER_SECRETS_KEY
NON_INTERACTIVE=true make install
```

The installer will prompt for disk selection, passwords, and optional features. Each screen shows the value it inherited from the environment or `config/config.yaml`, and where that value came from. Enter keeps it, any other choice overrides it. All settings can be pre-configured for non-interactive installations.

At the end of the installation, you can find a final copy of your config file at `/home/<USER>/final_config.yaml` on the installed system.

### Secrets Management

Store encrypted passwords in your config file for automated installs:

```bash
# encrypt and save to config.yaml: asks for the key and both passwords, without echo
make encrypt-secrets

# encrypt without writing to config (print only)
make encrypt-secrets NO_WRITE=true

# decrypt from config.yaml (asks for the key)
make decrypt-secrets

# use custom config path
make encrypt-secrets CONFIG_PATH=/path/to/config.yaml
```

The Makefile refuses secrets given as `make` arguments, because every user can read those through `ps` and they stay in your shell history. See [Keeping secrets out of ps and shell history](docs/configuration.md#keeping-secrets-out-of-ps-and-shell-history).

## Design Principles

**Config-driven and Declarative**: One YAML file declares everything: hostname, disk layout, packages, kernel parameters. The same file gives you the same system on every run.

**Secure by Default**: Most vanilla linux installs are actually insecure. This installer enables full disk encryption, Secure Boot, UKI usage, kernel hardening, basic firewalling, and strong suggestions + guides on secrets management out of the box.

**Idempotent**: Run it multiple times safely. Already-configured components are detected and skipped. Failed installs can be resumed.

**Recoverable System**: Bootable and signed snapshots let you boot into any previous system state. Broke something? Nvidia trolling again and releasing broken drivers? Just pick a working snapshot from the boot menu.

**Migration-friendly**: Migrate existing Arch installs to encrypted, snapshot-enabled systems without losing data.

**Testable**: Every component is unit-tested. Full installations are verified in QEMU VMs with real UEFI firmware, simulating bare metal installs.

## What You Get

After installation, you have (by default, unless configured otherwise):

- **Multiple boot entries**: Multiple kernels, possibility to boot **WRITEABLE** snapshots
- **Boot into snapshots**: In boot menu, select a snapshot entry, et voila system restored
- **Automatic snapshots**: Before/after package operations, hourly/daily/weekly
- **Signed boot chain**: Secure Boot with your own keys, UKI usage, mkinitcpio hooks, secure snapshots
- **Hardened configuration**: the shipped config.yaml enables CPU mitigations, the firewall and kernel lockdown
- **BTRFS subvolumes**: Separate subvolumes for `/`, `/home`, `/var`, `/tmp`, etc.
- **Hibernation**: Able to securely hibernate your system (if swap file enabled)
- **Dotfiles sync**: `dotfiles-sync` CLI tool to push/pull config files via Git
- **Verification tool**: `verify-install` checks system integrity post-install

## Documentation

| Topic                                                  | Description                           |
| ------------------------------------------------------ | ------------------------------------- |
| [Windows Transition](docs/windows-transition-guide.md) | Beginner's guide coming from Windows  |
| [Configuration](docs/configuration.md)                 | All config.yaml options               |
| [Secrets Management](docs/secrets-management.md)       | KeePassXC, SSH, Syncthing             |
| [BTRFS Layout](docs/btrfs-layout.md)                   | Subvolume structure                   |
| [Bootable Snapshots](docs/bootable-snapshots.md)       | Recovery via snapshots                |
| [Secure Boot](docs/secure-boot.md)                     | Key enrollment and signing            |
| [Firewall](docs/firewall.md)                           | UFW setup                             |
| [Threat Model](docs/threat-model.md)                   | Security analysis                     |
| [Dotfiles Sync](docs/dotfiles-sync.md)                 | Config file backups                   |
| [Development](docs/development.md)                     | Project structure, testing, code flow |
| [Notifications](docs/notifications.md)                 | Built-in desktop notifications        |
| [Testing](docs/testing.md)                             | Running tests                         |
| [Troubleshooting](docs/troubleshooting.md)             | Common issues                         |
| [Verification](docs/verification.md)                   | What `verify-install` checks          |
| [USB Boot Drive](docs/usb-boot.md)                     | Boot files and LUKS header on a stick |
| [USB Backup](docs/usb-backup.md)                       | Backups to a partition you choose     |

## Common Workflows

### Recover from a bad update

Boot menu → Select snapshot → System boots in previous state → `snapper rollback` to make permanent.

### Sync dotfiles across machines

```bash
dotfiles-sync init git@github.com:user/dotfiles.git
dotfiles-sync push   # from configured machine
dotfiles-sync pull   # on new machine
```

### Install with a USB boot drive

```bash
# from the Arch ISO, with the stick plugged in (here /dev/sdb); /dev/sr0 is the live medium
ENABLE_USB_BOOT=true USB_BOOT_DEVICE=/dev/sdb WIPE_METHOD=secure \
  ENABLE_RECOVERY_SYSTEM=true ISO_PATH=/dev/sr0 make install

# afterwards, a spare stick
make clone_usb_boot USB_DEVICE=/dev/sdb SPARE_DEVICE=/dev/sdc
```

### Verify installation

```bash
sudo verify-install --fix
```

### Migrate existing Arch install

Already have a disk-encrypted Arch installation? Migrate it to this managed setup while preserving your data:

```bash
# From Arch ISO, after cloning this repo: choose "Migration" on the first TUI screen,
# the old and new LUKS passwords are asked for without echo
make install
```

**What gets preserved:**

- `/home` directory and all user data
- SSH keys (`~/.ssh/`)
- Secure Boot keys (if already enrolled)

**What gets re-created:**

- Disk partitions (EFI + root)
- LUKS encryption (with your new password)
- BTRFS subvolume layout (optimized for snapshots)
- Snapper configuration
- UKI-based Secure Boot setup
- Kernel hardening parameters

Migration doesn't convert the disk in place. It copies your data to a staging area, wipes and repartitions the disk, creates a new LUKS volume with your new password, and copies the data back.

## Security Hardening

### Encryption

| Feature            | Implementation                                           |
| ------------------ | -------------------------------------------------------- |
| **LUKS2**          | Full disk encryption with `aes-xts-plain64`, 512-bit key |
| **Key Derivation** | argon2id PBKDF (1GB memory, 4 threads, 4000ms)           |

### Kernel Parameters

| Parameter                      | Purpose                   |
| ------------------------------ | ------------------------- |
| `lockdown=integrity`           | Kernel lockdown mode      |
| `iommu=force`                  | DMA protection            |
| `pti=on`                       | Meltdown mitigation       |
| `spectre_v2=on`                | Spectre v2 mitigation     |
| `spec_store_bypass_disable=on` | Spectre v4 mitigation     |
| `init_on_alloc=1`              | Zero memory on allocation |
| `init_on_free=1`               | Zero memory on free       |

### Secure Boot

| Feature             | Implementation                          |
| ------------------- | --------------------------------------- |
| **UKI Signing**     | Unified Kernel Images signed with sbctl |
| **Key Management**  | Custom Secure Boot keys                 |
| **Boot Protection** | Only signed kernels can boot            |

### Firewall

| Setting          | Value       |
| ---------------- | ----------- |
| Default incoming | **deny**    |
| Default outgoing | **allow**   |
| ICMP             | **blocked** |
| Logging          | **enabled** |

For detailed threat analysis, see [docs/threat-model.md](docs/threat-model.md).

### Known Issues being worked on
- The `secure` disk wipe method still has edge cases of failures, especially on VMs. Use `quick` for testing or `discard` for SSDs, except with a USB boot drive, which requires `secure` (or `skip`).
- `dotfiles-sync` needs more testing with private repos and SSH keys.

## References

- [Arch Wiki - Installation Guide](https://wiki.archlinux.org/title/Installation_guide)
- [Arch Wiki - BTRFS](https://wiki.archlinux.org/title/Btrfs)
- [Arch Wiki - Unified Kernel Image](https://wiki.archlinux.org/title/Unified_kernel_image)
- [Arch Wiki - Secure Boot](https://wiki.archlinux.org/title/Secure_Boot)
- [Helpful minimalist tutorial](https://walian.co.uk/arch-install-with-secure-boot-btrfs-tpm2-luks-encryption-unified-kernel-images.html)
- [Script-based installer project](https://www.github.com/ShellCode33/ArchLinux-Hardened)

## License

See [LICENSE](LICENSE) for details.
