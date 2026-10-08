# USB Boot Drive

## Overview

The USB boot drive implements plausible deniability encryption (PDE). The internal disk holds ciphertext from its first byte to its last, without a partition table, with nothing on it that boots and nothing that identifies it as encrypted. Everything needed to start and unlock the system lives on a USB drive:

- the EFI system partition: systemd-boot and every signed UKI (kernels, variants, bootable snapshots)
- the LUKS2 header: key slots, Argon2id parameters and the volume key wrapped by the passphrase
- optionally, a signed recovery system: the Arch live system, bootable under Secure Boot

Without the drive, the machine has no bootable system, and its disk cannot be told apart from a disk wiped with random data.

## What the Internal Disk Shows

| Inspected with | Finds |
| --- | --- |
| `fdisk -l`, `lsblk`, `blkid -p` (partition table) | none: no MBR, no GPT, no partitions. To any partitioning tool the disk is uninitialized |
| `blkid -p`, `file -s`, `cryptsetup isLuks` | nothing: the ciphertext starts at the disk's first byte (data offset 0), and the header that would identify it is on the drive |
| a scan for LUKS magic (`LUKS\xba\xbe`, `SKUL\xba\xbe`) | nothing |
| the data itself | random data everywhere: `storage.wipe_method: secure` fills the disk before the volume is created, so ciphertext and unused space look the same |
| firmware NVRAM | no boot entry and no systemd-boot system token: the firmware starts the drive through its removable-media path (`EFI/BOOT/BOOTX64.EFI`) |

The installer refuses a USB boot drive with the `quick` or `discard` wipe methods: both leave zero-filled areas, and `quick` can leave the LUKS header of a previous installation in place. `skip` is accepted for a disk that was filled before. Migration fills the disk with random data whenever the header is detached.

## Drive Layout

| Partition | Type | Size | Contents |
| --- | --- | --- | --- |
| 1 | EFI system partition (vfat) | `storage.efi_size_mb` | systemd-boot, `loader/loader.conf`, the UKIs in `EFI/Linux/`, the recovery UKI in `EFI/recovery/` |
| 2 | Linux filesystem, no filesystem | 32 MiB | the raw LUKS2 header of the internal disk |
| 3 | Linux filesystem (ext4), optional | the ISO plus 10 % and 64 MiB | the ISO's `arch/` tree: kernel, initramfs, live root image (`airootfs.sfs`) and its signature |

The rest of the drive stays unpartitioned. The installer refuses a drive that is too small before it erases anything.

## Boot Flow

1. The firmware loads `EFI/BOOT/BOOTX64.EFI` (systemd-boot, signed) from the drive.
2. systemd-boot lists every UKI in `EFI/Linux/` (each kernel and variant, each bootable snapshot) and the recovery entry.
3. The chosen UKI (kernel, initramfs and command line, signed as one) starts. Its command line names both halves of the volume:

   ```
   rd.luks.name=<LUKS UUID>=cryptroot
   rd.luks.data=<LUKS UUID>=/dev/disk/by-id/<internal disk>
   rd.luks.options=<LUKS UUID>=header=/dev/disk/by-partuuid/<drive header partition>
   ```

   The LUKS UUID exists only in the header, and a disk without a partition table carries no identifier of its own, so the internal disk is named by the `/dev/disk/by-id` link udev derives from its hardware: its WWN or NVMe EUI when it has one, otherwise its model and serial number (for example `nvme-Samsung_SSD_990_PRO_2TB_S6Z1NX0W123456`). That name stays the same across reboots and kernel updates, and when the disk moves to another machine or port. A kernel name such as `/dev/nvme0n1` would not: it changes when another disk is added. The installer refuses a target disk that udev gives no such name.
4. systemd-cryptsetup in the initramfs reads the header from the drive, asks for the passphrase and opens the internal disk. The initramfs carries the USB storage modules (`xhci_pci`, `usb_storage`, `uas`, `sd_mod` and the older host controllers, each optional), so it reaches the drive on any machine.

## Installation

The USB boot drive takes part in these install steps:

| Step | With a USB boot drive |
| --- | --- |
| USB boot drive (before Storage) | checks the drive and the ISO, then partitions the drive, formats its EFI partition and copies the ISO's live system |
| Storage | erases the partition table, fills the disk with random data, then `cryptsetup luksFormat --header <drive partition 2> --offset 0 <disk>` on the whole disk; the drive's EFI partition is mounted as `/efi` |
| Kernel images | the detached header and the disk's by-id name on the command line, the USB storage modules in the initramfs |
| Bootloader | `bootctl install --variables=no --random-seed=no`, `systemd-boot-random-seed.service` masked |
| Recovery system | signs the live root image, builds, signs and lists the recovery UKI |
| USB boot safeguards (last) | `/efi` mounted on demand, the pacman guard, the snapshot catch-up |

A resumed installation (the target still mounted, or `WIPE_METHOD=skip`) keeps a drive whose header partition holds a LUKS header, because only that header unlocks the existing volume.

## Living With the Drive

### `/efi` Is Mounted on Demand

```
PARTUUID=<drive EFI partition>  /efi  vfat  umask=0077,noauto,nofail,x-systemd.automount,x-systemd.idle-timeout=60s,x-systemd.device-timeout=5s  0 2
```

The drive's EFI partition is mounted when something reads `/efi` and unmounted after 60 idle seconds, so the drive can be pulled at any time without leaving a dirty FAT, and plugged back in without a remount. While it is away, a read of `/efi` fails after 5 seconds instead of hanging.

### Updates Need the Drive

A kernel update writes the new modules to the root filesystem and the new UKI to `/efi`. Without the drive, the UKI on the drive would stay behind and boot a kernel whose modules are gone. The pacman hook `/etc/pacman.d/hooks/00-usb-boot-drive.hook` prevents that: it runs `check-usb-boot-drive` before every transaction that touches what mkinitcpio, sbctl or systemd-boot react to (kernels, modules, firmware, microcode, initcpio hooks, systemd, cryptsetup, EFI binaries). The check compares the partition UUID mounted at `/efi` with the drive's, recorded in `/etc/default/usb-boot-drive`. Without the drive, pacman stops before it changes anything, snap-pac's pre-snapshot included:

```
The USB boot drive is not plugged in. This transaction rewrites boot files on
its EFI partition (PARTUUID ...): plug it in and run it again.
```

Transactions that touch no boot file run as usual.

### Kernels, Variants and Snapshots in the Boot Menu

All UKIs live on the drive's EFI partition, so the mkinitcpio presets, sbctl's signing hook and `manage-snapshot-ukis` work as on an internal disk, and the drive's systemd-boot menu lists every kernel, every UKI variant and every bootable snapshot. Snapshot UKIs are built from `/etc/kernel/cmdline`, so they unlock through the drive's header as well.

Snapshots taken while the drive is away (snapper's timeline) cannot get a UKI. `manage-snapshot-ukis refresh` then leaves `/var/lib/manage-snapshot-ukis/refresh-pending` and exits. A udev rule matching the drive's EFI partition UUID starts `snapshot-ukis-catch-up.service` when the drive is plugged back in, which runs the refresh while that file exists.

## Recovery System

With `usb_boot.recovery_system: true`, the drive carries the Arch live system, started by a UKI signed with the system's own Secure Boot keys:

- `usb_boot.iso_path` names an Arch ISO file, or the live medium itself (`/dev/sr0`). Its `arch/` tree goes onto the recovery partition, which is sized from the ISO 9660 volume descriptor.
- The UKI holds the ISO's kernel and initramfs and the command line `archisobasedir=arch archisodevice=UUID=<recovery partition> cms_verify=y`. systemd-boot lists it as "Arch Linux recovery (2026.01.01)", after the system's own entries.
- Secure Boot verifies the UKI, and so its kernel, initramfs and command line. With `cms_verify=y` the initramfs verifies the live root image (`airootfs.sfs`) against a CMS signature before it mounts it, so a modified root image on the drive stops the boot.
- The signature is the installer's, not Arch's. archiso checks against the certificate in its initramfs, and the ISO's own certificate expires (the 2026.01 ISO's on 2026-05-30), after which the live system would refuse to boot. The installer therefore signs the root image with a one-time key kept on the live system's tmpfs, discards the key, and adds its certificate (valid for 100 years) to the UKI as a second initramfs that replaces `/codesign.crt`. That certificate lies inside the signed UKI, so it cannot be swapped either.
- The archiso initramfs carries every module and firmware file: the recovery UKI takes about 250 MB of the EFI partition.

To reach the installed system from the recovery system:

```bash
cryptsetup open --header /dev/sdX2 /dev/nvme0n1 cryptroot
mount -o subvol=@ /dev/mapper/cryptroot /mnt
```

## Spare Drive

The header on the drive is the only copy of the wrapped volume key: losing the drive loses the data. A spare drive is a clone that boots and unlocks the system on its own:

```bash
make clone_usb_boot USB_DEVICE=/dev/sdX SPARE_DEVICE=/dev/sdY
```

It copies the partition table, partition UUIDs included, and every partition, then compares the two headers. Both drives answer to the same partition UUIDs, so plug in one at a time. The spare holds the UKIs of the moment it was cloned: clone again after kernel updates (an older UKI still unlocks the disk, but its kernel may no longer find its modules).

A header-only copy also restores access: `cryptsetup luksHeaderBackup /dev/sdX2 --header-backup-file header.img`, used with `cryptsetup open --header header.img`. Keep it as private as the drive.

## What It Covers and What It Does Not

PDE here concerns what the machine reveals without the drive:

- The internal disk carries no partition table, no boot code, no LUKS metadata and no zero-filled areas: random data from its first byte to its last, like a disk wiped with random data.
- The firmware keeps no boot entry and no systemd-boot system token.

Still visible or out of scope:

- **Secure Boot keys.** sbctl enrolls the system's own platform key, key exchange key and db key in the firmware, which its setup screens and NVRAM show.
- **Use counters.** A disk's SMART data (data written, power-on hours) shows that it is in use, whatever it holds.
- **Change over time.** Two images of the disk taken at different times show where the ciphertext changed. Random fill hides what the data is, not where it was written.
- **The drive.** It holds the LUKS header (`blkid` reports it as LUKS2), the UKIs and command lines naming the internal disk by its model and serial number or WWN. Whoever holds the drive knows which disk is encrypted, and with the passphrase opens it.
- **A running system.** Once unlocked, the volume key is in memory.
- **Migration.** The migration step reads an existing installation whose header is on its own disk; an installation with a detached header has to be copied by hand.

## Configuration

```yaml
storage:
  wipe_method: secure    # skip on a disk filled before
  efi_size_mb: 2048      # the drive's EFI partition

usb_boot:
  enabled: true
  device: /dev/sdb         # the whole drive is erased
  recovery_system: true
  iso_path: /dev/sr0       # an Arch ISO file, or the live medium
```

| Environment variable | Setting |
| --- | --- |
| `ENABLE_USB_BOOT` | `usb_boot.enabled` |
| `USB_BOOT_DEVICE` | `usb_boot.device` |
| `ENABLE_RECOVERY_SYSTEM` | `usb_boot.recovery_system` |
| `ISO_PATH` | `usb_boot.iso_path` |
| `WIPE_METHOD` | `storage.wipe_method` |

The interactive setup asks about the drive first, before the Storage questions: "USB boot drive", then the drive (chosen from the detected disks), "Recovery system", and the ISO when the recovery system is on.

The installer refuses to start when the drive is the target disk, when the recovery system has no ISO, or when the wipe method leaves zero-filled areas (see above).

## Troubleshooting

### The machine does not boot from the drive

1. Choose the drive in the firmware's boot menu, or move USB devices first in its boot order.
2. With Secure Boot on, the firmware must hold the keys sbctl enrolled during installation.
3. Inspect the drive from another system:
   ```bash
   mount /dev/sdX1 /mnt/usb-efi
   ls /mnt/usb-efi/EFI/BOOT/ /mnt/usb-efi/EFI/Linux/ /mnt/usb-efi/loader/entries/
   ```

### The passphrase prompt never comes

The initramfs waits for both devices named on the command line. Check that the drive's header partition carries the partition UUID in `/etc/kernel/cmdline`, and that the disk's by-id name there still exists:

```bash
blkid -s PARTUUID /dev/sdX2
ls -l /dev/disk/by-id/ | grep nvme0n1
```

A disk replaced under warranty, or a firmware update that changes how the disk reports its serial number, changes the by-id name. Boot the recovery system, open the disk with `cryptsetup open --header`, and rebuild the UKIs with the new name in `/etc/kernel/cmdline*`.

### Another operating system offers to initialize the disk

Windows Disk Management, and installers in general, see a disk without a partition table and offer to initialize it. Accepting writes a partition table over the start and the end of the ciphertext, which damages the encrypted file system. Decline it, or keep the disk out of reach of other operating systems.

### pacman stops with "The USB boot drive is not plugged in"

Plug the drive in and run the transaction again. `ls /efi` should list `EFI` and `loader` within a few seconds.

### Snapshot entries are missing after the drive was away

```bash
systemctl status snapshot-ukis-catch-up.service
manage-snapshot-ukis refresh
```

### The recovery system stops before its shell

A failed `cms_verify` drops to the initramfs shell: the live root image on the recovery partition no longer matches the signature made at installation, so the drive was modified. Start an Arch ISO from other media instead.
