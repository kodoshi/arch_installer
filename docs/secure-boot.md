# Secure Boot Setup

## Automatic (Recommended)

The installer configures sbctl and enrolls keys if Secure Boot is in Setup Mode:

1. Enter UEFI setup (usually Esc/F2/F10/Del key during initial splash screen)
2. Find Secure Boot settings
3. Enable "Setup Mode" or depending on firmware, the PK (Platform Key) may need to be cleared **WARNING: do your research first and triple-check!**
4. Save and boot into Arch ISO
5. Run installer, keys are enrolled automatically. It includes Microsoft vendor keys, allowing dual-boot, and certain OPROMs/firmware that were signed by MS **WARNING: remove MS keys only if you fully understand the implications!**
6. After installation, reboot and re-enter UEFI setup
7. Enable Secure Boot
8. Save and reboot into installed system

## Random Seed Hardening

`systemd-boot` and the UKI stub keep a random seed in `/efi/loader/random-seed` and rewrite it on every boot (since systemd 262 the stub also writes `boot-secret-mixin` there). FAT32 has no per-file permissions, so with the default mount mask anyone on the system could read the seed.

The installer mounts the ESP with `umask=0077`, which `genfstab` carries into `/etc/fstab`. Everything on the ESP, the seed included, is then readable by root only. Deleting the seed is not an option: it is recreated at the next boot, and removing it would also weaken early-boot entropy.

## Manual Key Enrollment

```bash
sbctl status
sbctl create-keys
sbctl enroll-keys --microsoft  # Include MS keys for dual-boot and to not block signed firmware
sbctl sign -s /efi/EFI/Linux/*.efi
```

## Verification

```bash
sbctl verify /efi/EFI/Linux/*.efi  # Check signing status
sbctl sign-all                      # Re-sign all files
```

## Troubleshooting

**Secure Boot rejects UKIs:**

```bash
sbctl verify /efi/EFI/Linux/*.efi
sbctl sign-all
```

**UKI won't boot with Secure Boot enabled:**

- Check if sbctl is configured: `sbctl status`
- Re-enroll keys: `sbctl enroll-keys --microsoft`
