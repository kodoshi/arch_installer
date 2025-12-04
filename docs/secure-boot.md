# Secure Boot Setup

## Automatic (Recommended)

The installer configures sbctl and enrolls keys if Secure Boot is in Setup Mode:

1. Enter UEFI setup (usually Esc/F2/F10/Del key during initial splash screen)
2. Find Secure Boot settings
3. Enable "Setup Mode" or depending on firmware, the PK (Platform Key) may need to be cleared **WARNING: do your research first and triple-check!**
4. Save and boot into Arch ISO
5. Run installer - keys enrolled automatically. It includes Microsoft vendor keys, allowing dual-boot, and certain OPROMs/firmware that was signed by MS **WARNING: remove MS keys only if you fully understand the implications!**
6. After installation, reboot and re-enter UEFI setup
7. Enable Secure Boot
8. Save and reboot into installed system


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
