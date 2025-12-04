# BTRFS Subvolume Layout

## Subvolumes

| Subvolume            | Mountpoint              | Purpose                        | NoCoW |
| -------------------- | ----------------------- | ------------------------------ | ----- |
| `@`                  | `/`                     | Root filesystem                | No    |
| `@home`              | `/home`                 | User data                      | No    |
| `@snapshots`         | `/.snapshots`           | Root snapshots (bootable)      | No    |
| `@home-snapshots`    | `/home/.snapshots`      | Home snapshots (data recovery) | No    |
| `@var`               | `/var`                  | Variable data                  | Yes   |
| `@var-log`           | `/var/log`              | System logs                    | No    |
| `@cache-pacman-pkgs` | `/var/cache/pacman/pkg` | Package cache                  | No    |
| `@var-tmp`           | `/var/tmp`              | Temporary files                | No    |
| `@srv`               | `/srv`                  | Server data                    | No    |
| `@swap`              | `/.swap`                | Swapfile                       | Yes   |
| `@docker`            | `/var/lib/docker`       | Docker data                    | Yes   |
| `@libvirt`           | `/var/lib/libvirt`      | VM images                      | Yes   |

## Why Two Snapshot Subvolumes?

- **`@snapshots`** (root): System snapshots - packages, configs, services. **Bootable, writable**.
- **`@home-snapshots`** (home): User file snapshots - documents, downloads. **Not bootable**.

This separation allows:

1. Rolling back a system update while keeping recent user files
2. Recovering deleted user files without affecting system state
3. Independent snapshot schedules for system vs user data

## Managing Snapshot Storage

### Viewing Space Usage

```bash
snapper -c root list                          # List snapshots
btrfs filesystem du -s /.snapshots/*/snapshot # Actual disk usage
btrfs subvolume list / | grep snapshots       # Snapshot subvolumes
manage-snapshot-ukis space                    # EFI partition usage
btrfs filesystem usage /                      # Detailed BTRFS usage
```

### Cleanup

```bash
snapper -c root delete 10           # Delete specific snapshot
snapper -c root delete 10-20        # Delete range
snapper -c root cleanup timeline    # Clean based on limits
```

### Storage Recommendations

| Component                     | Typical Size | Notes                           |
| ----------------------------- | ------------ | ------------------------------- |
| Single UKI                    | ~100 MB      | Kernel + initramfs + microcode  |
| Root snapshot (after updates) | 100-500 MB   | Depends on changes              |
| EFI partition                 | 2 GB         | Fits ~15-20 UKIs                |
