# USB Backup

`make backup_to_usb` copies dotfiles, password databases, browser profiles and system configuration to a partition you choose, together with the list of explicitly installed packages and this machine's `config.yaml` (without passwords).

The partition is yours: the backup mounts it and never formats it. It is not the [USB boot drive](usb-boot.md), whose partitions hold only what booting needs. The files are copied in plain text, so put the partition on an encrypted volume when the backup includes browser profiles or anything else sensitive.

## Usage

```bash
# a partition with a filesystem Linux can mount, ext4 for example
make backup_to_usb BACKUP_PARTITION=/dev/sdc1

# only some categories
make backup_to_usb BACKUP_PARTITION=/dev/sdc1 BACKUP_CATEGORIES=dotfiles,keepass
```

## Configuration

```yaml
sync:
  # the partition to back up to (also BACKUP_PARTITION)
  backup_partition: /dev/sdc1

  # built-in categories (also BACKUP_CATEGORIES)
  backup_categories: [dotfiles, keepass, browser, system]

  # custom items, in addition to the categories
  backup_items:
    - name: ssh_keys
      source_path: ~/.ssh
      description: SSH keys and config
```

Built-in categories:

- **dotfiles**: shell configs (zsh, bash), editor configs (nvim, vscode), desktop configs (hyprland, waybar)
- **keepass**: KeePassXC database and config files
- **browser**: Firefox and Chromium profiles
- **system**: pacman.conf, makepkg.conf, mkinitcpio.conf

## What the Partition Holds

```
manifest.yaml                 # time, host name, categories, package count, items
config/config.yaml            # this machine's configuration, without passwords
config/package_catalog.yaml   # explicitly installed packages
dotfiles/ keepass/ browser/ system/ custom/
```

`package_catalog.yaml` has the shape of `config.yaml`:

```yaml
packages:
  cataloged:
    - firefox
    - neovim
```

Copied into a new `config.yaml`, the cataloged packages are installed together with `packages.base`.
