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
| **Security Hardening**  | Kernel hardening, CPU mitigations, firewall                      |
| **Migration Support**   | Migrate existing Arch installs, preserve home & Secure Boot keys |
| **Multiple Kernels**    | linux-hardened, mainline, LTS with variants                      |
| **Multi-Desktop**       | GNOME, KDE, Hyprland - install one or all                        |
| **Dual-Boot Ready**     | Windows-friendly (separate drives recommended)                   |
| **Hibernation Support** | Resume from swapfile on encrypted root                           |
| **Dotfiles Sync**       | Git backups of config files                                      |

## Quick Start

```bash
# From Arch ISO live environment
pacman-key --init
pacman -Sy --noconfirm git python make
git clone https://github.com/kodoshi/arch_installer.git
cd arch_installer

# Edit config (recommended)
nano config/config.yaml

# Option 1: CLI installer with interactive prompts
make install

# Option 2: Non-interactive (pre-configure config/config.yaml, fill env vars)
NON_INTERACTIVE=true LUKS_PASSWORD=... USER_PASSWORD=... make install  # ARCH_INSTALLER_SECRETS_KEY can also be used if passwords are encrypted and stored in config.yaml
```

The installer will prompt for disk selection, passwords, and optional features. Values from `config/config.yaml` are shown as defaults - press Enter to accept them. All settings can be pre-configured for non-interactive installations.

At the end of the installation, you can find a final copy of your config file at `/home/<USER>/final_config.yaml` on the installed system.

### Secrets Management

Store encrypted passwords in your config file for automated installs:

```bash
# encrypt and save to config.yaml
make encrypt-secrets ARCH_INSTALLER_SECRETS_KEY=mysecretkey LUKS_PASSWORD=myluks USER_PASSWORD=myuser

# encrypt without writing to config (print only)
make encrypt-secrets ARCH_INSTALLER_SECRETS_KEY=mysecretkey LUKS_PASSWORD=myluks NO_WRITE=true

# decrypt from config.yaml
ARCH_INSTALLER_SECRETS_KEY=mysecretkey make decrypt-secrets

# use custom config path
make encrypt-secrets ARCH_INSTALLER_SECRETS_KEY=key LUKS_PASSWORD=pw CONFIG_PATH=/path/to/config.yaml
```

## Design Principles

**Config-driven and Declarative**: One YAML file declares everything - hostname, disk layout, packages, kernel parameters. Edit the config, run the installer, get consistent results.

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
- **Hardened defaults**: CPU mitigations enabled, firewall on, kernel locked down
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
| [Notifications](docs/notifications.md)                 | Build-in desktop notifications        |
| [Testing](docs/testing.md)                             | Running tests                         |
| [Troubleshooting](docs/troubleshooting.md)             | Common issues                         |

## Common Workflows

### Recover from a bad update

Boot menu → Select snapshot → System boots in previous state → `snapper rollback` to make permanent.

### Sync dotfiles across machines

```bash
dotfiles-sync init git@github.com:user/dotfiles.git
dotfiles-sync push   # from configured machine
dotfiles-sync pull   # on new machine
```

### Verify installation

```bash
sudo verify-install --fix
```

### Migrate existing Arch install

Already have a disk-encrypted Arch installation? Migrate it to this managed setup while preserving your data:

```bash
# From Arch ISO, after cloning this repo
SOURCE_LUKS_PASSWORD=your_old_password LUKS_PASSWORD=your_new_password ENABLE_MIGRATION=true make install
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

**Note:** Migration creates a completely fresh partition layout with new LUKS encryption. Your old data is copied to staging, the disk is wiped and reformatted, then your data is restored. This ensures a clean, optimized setup.

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
- GUI installer still a work in progress - use CLI installer for now.
- The `secure` disk wipe method still has edge cases of failures, especially on VMs. Use `quick` for testing or `discard` for SSDs.
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
