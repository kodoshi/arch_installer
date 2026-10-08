# USB Boot Drive: Hand-off

`src/arch_installer/executors/usb_boot.py` and `tests/unit/test_usb_boot.py` were left untouched during the October 2026 restructure, because the USB boot drive is still being finished. Everything below waits on that work. Each item names the files it touches.

## Check first: what `make init_to_usb` does to the internal disk

`make init_to_usb` (`cli.usb_init`) runs the whole `UsbBootExecutor` flow outside an installation, then the USB backup:

1. `provision`, `install_bootloader`
2. `relocate_internal_efi`: copies `/mnt/efi` to the USB drive, then **deletes** `/mnt/efi/EFI` and `/mnt/efi/loader`
3. `detach_luks_header` (when `usb_boot.detached_luks_header`): backs up the LUKS header of `storage.root_partition`, then runs **`cryptsetup erase`** on it
4. `install_recovery_iso`, `sign_binaries`

During an installation, steps 2 and 3 act on the system being installed. Standalone, `storage.root_partition` is derived from `storage.target_disk`, so running `make init_to_usb` on an installed machine with `TARGET_DISK` set would erase that disk's LUKS header. Decide whether a standalone init should only prepare the drive (steps 1 and 4), and move steps 2 and 3 into the installation's USB boot step.

Related regression from the "no code defaults" change: `usb_init` assembles the full configuration, and the shipped `config/config.yaml` leaves `storage.target_disk` to `TARGET_DISK` or the TUI, so `make init_to_usb` now stops with `storage.target_disk (config.yaml or TARGET_DISK)`. If a standalone init no longer touches the internal disk, it should not need a target disk at all.

## Renames waiting for the USB files

| Change | Why it waits | Files |
|---|---|---|
| `UsbBootExecutor` becomes `UsbBootStepExecutor` | the class lives in `usb_boot.py` | `executors/usb_boot.py`, `install_steps/registry.py`, `cli.py`, `tests/unit/test_usb_boot.py`, `tests/unit/test_installer.py`, `docs/diagrams/architecture.puml` |
| the base class `Executor` becomes `StepExecutor` | `usb_boot.py` subclasses it, and an alias would break the no-shims rule | `executors/base.py`, every executor module, `install_steps/wiring.py`, `tests/unit/test_installer.py` |

Every other step executor already has its `...StepExecutor` name. Both renames are mechanical: rename identifiers only, then run `make lint` and `make test`.

## Model defaults

`UsbBootConfig` in `config/models.py` is the only configuration class that still has field defaults. They stay only because `tests/unit/test_usb_boot.py` builds it from a few fields (`UsbBootConfig(enabled=True, device="/dev/sdb")`). The config builder already requires every field, so the defaults never reach an installation. To remove them, build the test values from the unit config instead:

```python
replace(build_config().usb_boot, enabled=True, device="/dev/sdb")
```

## Registry entry

`InstallStep.USB_BOOT_DRIVE` in `install_steps/registry.py` currently declares:

| Setting | Environment variable | Question |
|---|---|---|
| `usb_boot.enabled` | `ENABLE_USB_BOOT` | switch "USB boot drive" |
| `usb_boot.device` | `USB_BOOT_DEVICE` | text "USB device", asked only when the drive is on |
| `usb_boot.iso_path` | `ISO_PATH` | none |

The rest of the `usb_boot` section is configured in `config.yaml` only, and the step runs when `usb_boot.enabled` is true. Check this against the finished executor: whether the ISO path should be asked, and whether the detected disks should be offered as choices for the device (as the Storage step does for the target disk).

## Tests not run since the restructure

Both need the VM fixture with a second disk (`qemu_vm_with_usb_disk_and_network`, `/dev/vdb`):

```bash
TMPDIR=~/.cache/arch-installer-qemu poetry run pytest \
  "tests/qemu/test_installation.py::TestQemuFullInstallation::test_usb_boot_drive_stores_efi_and_luks_headers_on_second_disk" \
  "tests/qemu/test_installation.py::TestQemuFullInstallation::test_usb_backup_writes_packages_manifest_and_config_to_backup_partition" \
  --arch-iso ~/Downloads/archlinux.iso -v -s
```

## Conventions to apply when the code is final

- Identifiers without abbreviations, as in the rest of the code (the October sweep skipped `usb_boot.py`).
- `docs/usb-boot.md`: check the configuration examples and the steps against the finished behaviour.
- Regenerate `docs/functional-map.md` and re-render `docs/diagrams/architecture.puml` (`make diagrams`).
