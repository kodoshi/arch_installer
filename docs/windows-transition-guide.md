# Windows Transition Guide

Guide for dual-booting Arch Linux alongside Windows. Assumes familiarity with partitioning, UEFI, and command-line basics.

**Not covered**: Partitioning fundamentals, basic terminal usage, creating bootable USBs, VM setup.

## Preparation

### Recommended: Separate Drives

Using separate physical drives for Windows and Arch is the safest approach:

- Windows updates cannot affect Linux boot
- Use BIOS boot menu to select OS
- Password-protect your BIOS settings

### Same Drive: not supported

The installer takes over the **whole** target disk: it always creates its own EFI and
LUKS partitions as partitions 1 and 2, and re-partitions the disk when those are not the
ones it created. No wipe method (not even `WIPE_METHOD=skip`) preserves a Windows
installation on the same drive. Put Windows on a separate drive.

Whichever drive layout you use, disable Windows Fast Startup (Control Panel → Power
Options → "Turn on fast startup" → Off) so Windows never leaves shared firmware state
half-hibernated.

## BIOS Configuration

| Setting     | Value      | Reason                   |
| ----------- | ---------- | ------------------------ |
| Boot Mode   | UEFI       | Required for secure boot |
| Secure Boot | Setup Mode | Allows key enrollment    |
| Fast Boot   | Disabled   | Allows USB boot          |

## Post-Install

### Verify Secure Boot

```bash
sbctl status
# Should show: Secure Boot enabled, Setup Mode disabled
```

### Boot Windows

Use BIOS boot menu (F12/F8/Esc) to select drive containing Windows or add Windows to systemd-boot.

## Further Reading

- [Configuration](configuration.md) - config.yaml options
- [Secure Boot](secure-boot.md) - key management
- [Bootable Snapshots](bootable-snapshots.md) - recovery system
