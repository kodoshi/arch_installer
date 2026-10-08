# Post-Installation Verification

Run after booting into the installed system:

```bash
sudo verify-install
```

`make verify` runs the same script with `--fix --verbose`.

## Options

| Option                  | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `--fix`                 | Attempt to automatically fix issues                  |
| `--verbose`             | Show detailed output for all checks                  |
| `--expected-state FILE` | Read the expectations from FILE instead of the default |

## What It Compares Against

At the end of an installation the installer writes `/etc/dali/expected-state.env`: the values the installed system should have (hostname, timezone, locale, keymap, user, swap, kernel packages, display managers, snapper, firewall, GPU), rendered from the final configuration as shell assignments, without any secrets. `verify-install` sources that file, so it needs no YAML parser on the target.

Because the script runs as root and sources the file, it refuses a file not owned by root or writable by anyone else. Without the file it still runs every check that needs no expectation, and reports a warning that the configuration-dependent checks were skipped.

The maximal QEMU test runs `verify-install` after rebooting into the installed system and requires zero failures.

## What It Checks

| Category          | Checks                                                               |
| ----------------- | -------------------------------------------------------------------- |
| **Secure Boot**   | UEFI mode, Secure Boot enabled, sbctl keys enrolled, binaries signed |
| **UKI**           | EFI mounted, UKI directory exists, all kernel variants have UKIs     |
| **Bootloader**    | systemd-boot installed, loader.conf, editor disabled                 |
| **Encryption**    | LUKS2, root encrypted, cipher, PBKDF (argon2id), key size            |
| **BTRFS**         | Root is BTRFS, subvolumes mounted, compression, NoCoW attrs          |
| **Swap**          | Swapfile exists, active, permissions (600)                           |
| **System Config** | Hostname, timezone, locale, keymap; user in wheel group              |
| **Network**       | NetworkManager running, enabled, connectivity                        |
| **Services**      | systemd-resolved, timesyncd, display manager, snapper timers         |
| **Kernel Params** | Hardening (lockdown, IOMMU, PTI, Spectre mitigations)                |
| **GPU**           | Configured driver loaded, GPU detected                               |
| **Packages**      | Essential packages, kernels, CPU microcode                           |

## Example Output

```
═══════════════════════════════════════════════════════════════════════════════
  SECURE BOOT VERIFICATION
═══════════════════════════════════════════════════════════════════════════════
  ✓ System booted in UEFI mode
  ✓ Secure Boot is ENABLED
  ✓ All registered binaries are properly signed

═══════════════════════════════════════════════════════════════════════════════
  VERIFICATION SUMMARY
═══════════════════════════════════════════════════════════════════════════════

  Passed:   42
  Failed:   0
  Warnings: 2

  ✓ All critical checks passed!
```

## Auto-Fix

```bash
sudo verify-install --fix
```

Can fix:

- Activate inactive swap
- Start/enable NetworkManager
- Sign unsigned binaries with sbctl
