# Bootable Snapshots

Boot directly into a previous system state if an update breaks your system.

## How It Works

1. **Snapper** creates BTRFS snapshots in `/.snapshots/`
2. **manage-snapshot-ukis** generates signed UKIs for each snapshot (up to 7)
3. **Pacman hook** auto-refreshes UKIs after kernel/microcode updates
4. **systemd-boot** displays snapshot entries in the boot menu
5. **Desktop notifications** alert when snapshot UKIs are created

## Boot Menu

Depending on your boot order, you will enter your boot menu and see entries like:

```
Arch Linux (linux-hardened)                 ← Current system
Arch Linux (linux-lts)                      ← LTS variant
Snapshot #11 [hardened] (2025-12-08 14:30)  ← Bootable snapshot
Snapshot #10 [hardened] (2025-12-07 10:15)  ← Older bootable snapshot
```

## Booting Into a Snapshot

1. Turn on/reboot your system
2. Select a snapshot entry from the boot menu
3. System boots into that snapshot - fully functional, writable
4. Verify you are in the right snapshot (see below)
5. If all good, make it permanent (see below)

## Verifying You're in a Snapshot

```bash
# Check current root subvolume
btrfs subvolume show /
# Snapshot: Name: @snapshots/10/snapshot
# Normal:   Name: @

# Quick check via kernel cmdline
cat /proc/cmdline | grep -o 'subvol=[^ ]*'
# Snapshot: subvol=@snapshots/10/snapshot
# Normal:   subvol=@
```

## Making a Snapshot Permanent (Rollback)

```bash
# Option 1: Use snapper rollback (recommended)
snapper -c root rollback <snapshot_number>

# Option 2: Manual approach
# 1. Note snapshot number: cat /proc/cmdline
# 2. Reboot into normal system
# 3. Replace current root
sudo btrfs subvolume delete /@
sudo btrfs subvolume snapshot /.snapshots/<N>/snapshot /@
```

## Automatic UKI Refresh

Pacman hook regenerates snapshot UKIs when:

- Kernel packages upgraded
- Microcode updated
- mkinitcpio presets change

Log: `/var/log/snapshot-uki-refresh.log`

## Manual Snapshot Management

```bash
snapper -c root create -d "Before upgrade"  # Create snapshot
snapper -c root list                        # List snapshots
manage-snapshot-ukis refresh                # Generate bootable UKIs (last 7)
manage-snapshot-ukis refresh 10             # Generate more if space allows
manage-snapshot-ukis space                  # Check EFI partition space
manage-snapshot-ukis list                   # List bootable snapshot UKIs
manage-snapshot-ukis cleanup                # Remove all snapshot UKIs
```

## Desktop Notifications

| Event           | Message                                      |
| --------------- | -------------------------------------------- |
| UKIs created    | "Created N bootable snapshot entries"        |
| Creation failed | "Failed to create bootable snapshot entries" |

How to disable Notifications: `SNAPSHOT_NOTIFY=false manage-snapshot-ukis refresh`

## Configuration

Enable bootable snapshots in `config.yaml`:

```yaml
boot:
  # enable bootable snapshots (creates UKIs for each btrfs snapshot)
  enable_snapshot_boot: true
```

Or via environment variable during installation:

```bash
ENABLE_SNAPSHOT_BOOT=true make install
```

**Prerequisites for bootable snapshots:**

- Snapper must be enabled with a root config (`snapper.enabled: true` and `snapper.root` defined)
- Boot cmdline must be read-write (`boot.cmdline.rw: true`)

If `enable_snapshot_boot` is enabled but prerequisites are not met, the installer will emit a warning.
