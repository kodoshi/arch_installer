# Windows Transition Guide

Guide for dual-booting Arch Linux alongside Windows. Assumes familiarity with partitioning, UEFI, and command-line basics.

**Not covered**: Partitioning fundamentals, basic terminal usage, creating bootable USBs, VM setup.

## Preparation

### Recommended: Separate Drives

Using separate physical drives for Windows and Arch is the safest approach:

- Windows updates cannot affect Linux boot
- Use BIOS boot menu to select OS
- Password-protect your BIOS settings

### Same Drive

If using a single drive:

1. Shrink Windows partition in Disk Management (leave ≥100GB for Arch)
2. Disable Windows Fast Startup: Control Panel → Power Options → "Turn on fast startup" → Off
3. Set `WIPE_METHOD=4` (skip) in installer to preserve Windows

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
