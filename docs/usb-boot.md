# USB Boot Drive

With a USB boot drive, the installer sets up plausibly deniable encryption (PDE). Everything the machine needs to start and unlock the system goes on a USB stick:

- the EFI system partition, with systemd-boot and the signed UKIs for each kernel, variant and bootable snapshot
- the LUKS2 header, which holds the key slots, the Argon2id parameters and the volume key wrapped by your passphrase
- optionally, a recovery system: the Arch live system, signed so it boots under Secure Boot

The internal disk keeps only the encrypted data, with no partition table and no header. Without the stick the machine has nothing to boot, and the disk looks like one that was wiped with random data.

## What the Internal Disk Shows

| Inspected with | Result |
| --- | --- |
| `fdisk -l`, `lsblk`, `blkid -p` | No MBR, no GPT, no partitions. Partitioning tools see an uninitialized disk. |
| `blkid -p`, `file -s`, `cryptsetup isLuks` | Nothing recognized. The ciphertext starts at byte 0 of the disk and the header is on the stick. |
| A search for the LUKS magic bytes (`LUKS\xba\xbe`, `SKUL\xba\xbe`) | No match. |
| The data itself | Random data throughout. The `secure` wipe fills the disk before the volume is created, so used and unused space look alike. |
| Firmware NVRAM | No boot entry and no systemd-boot system token. The firmware boots the stick through its removable-media path (`EFI/BOOT/BOOTX64.EFI`). |

The installer refuses a USB boot drive together with the `quick` or `discard` wipe methods. Both leave zero-filled areas behind, and `quick` can also leave the LUKS header of an earlier installation on the disk. `skip` is allowed for a disk you already filled with random data. Migration always does the random fill when the header goes on a stick.

## Layout of the Stick

| Partition | Type | Size | Contents |
| --- | --- | --- | --- |
| 1 | EFI system partition (vfat) | `storage.efi_size_mb` | systemd-boot, `loader/loader.conf`, the UKIs in `EFI/Linux/`, the recovery UKI in `EFI/recovery/` |
| 2 | Linux filesystem, raw | 32 MiB | the LUKS2 header of the internal disk |
| 3 | Linux filesystem (ext4), optional | ISO size plus 10 % and 64 MiB | the `arch/` tree of the ISO: kernel, initramfs, root image (`airootfs.sfs`) and its signature |

The rest of the stick is left unpartitioned. If the stick is too small, the installer stops before erasing anything.

## How It Boots

1. The firmware loads `EFI/BOOT/BOOTX64.EFI`, the signed systemd-boot, from the stick.
2. systemd-boot lists the UKIs in `EFI/Linux/` (one per kernel and variant, plus the bootable snapshots) and the recovery entry.
3. The UKI you pick (kernel, initramfs and command line, signed together) starts. Its command line points at both halves of the volume:

   ```
   rd.luks.name=<LUKS UUID>=cryptroot
   rd.luks.data=<LUKS UUID>=/dev/disk/by-id/<internal disk>
   rd.luks.options=<LUKS UUID>=header=/dev/disk/by-partuuid/<header partition on the stick>
   ```

   The LUKS UUID is stored only in the header, and a disk without a partition table has no UUID of its own. The disk is therefore named by the `/dev/disk/by-id` link that udev builds from the hardware: the WWN or NVMe EUI if the disk has one, otherwise model and serial number (for example `nvme-Samsung_SSD_990_PRO_2TB_BLABLABLA`). This name survives reboots, kernel updates and moving the disk to another port or machine. A kernel name like `/dev/nvme0n1` would not, since it can change when another disk is added. The installer stops if udev has no such name for the target disk.
4. systemd-cryptsetup in the initramfs reads the header from the stick, asks for the passphrase and opens the disk. The initramfs includes the USB storage modules (`xhci_pci`, `usb_storage`, `uas`, `sd_mod` and the older host controllers, each optional), so it finds the stick on other hardware too.

## Installation

These install steps change when a USB boot drive is configured:

| Step | What it does with a USB boot drive |
| --- | --- |
| USB boot drive (runs before Storage) | Checks the stick and the ISO, partitions the stick, formats its EFI partition and copies the live system from the ISO. |
| Storage | Erases the partition table, fills the disk with random data, then runs `cryptsetup luksFormat --header <stick partition 2> --offset 0` on the whole disk. Mounts the stick's EFI partition at `/efi`. |
| Kernel images | Puts the header location and the disk's by-id name on the command line, and the USB storage modules in the initramfs. |
| Bootloader | Runs `bootctl install --variables=no --random-seed=no` and masks `systemd-boot-random-seed.service`. |
| Recovery system | Signs the live root image, then builds, signs and adds the recovery UKI. |
| USB boot safeguards (runs last) | Sets up the on-demand mount of `/efi`, the pacman guard and the snapshot catch-up described below. |

When an installation is resumed (the target is still mounted, or `WIPE_METHOD=skip`), a stick that already holds a LUKS header is kept as it is, since that header is the only way to open the existing volume.

## Day-to-Day Use

### `/efi` Is Mounted on Demand

```
PARTUUID=<stick EFI partition>  /efi  vfat  umask=0077,noauto,nofail,x-systemd.automount,x-systemd.idle-timeout=60s,x-systemd.device-timeout=5s  0 2
```

The EFI partition is mounted when something accesses `/efi` and unmounted after 60 idle seconds. You can pull the stick without leaving the FAT dirty and plug it back in without remounting anything. While the stick is out, accessing `/efi` fails after 5 seconds instead of hanging.

### Updates Need the Stick

A kernel update writes the new modules to the root filesystem and the new UKI to `/efi`. If the stick were missing, the old UKI would stay on it and boot a kernel whose modules are gone. The pacman hook `/etc/pacman.d/hooks/00-usb-boot-drive.hook` prevents this. Before any transaction that touches files mkinitcpio, sbctl or systemd-boot care about (kernels, modules, firmware, microcode, initcpio hooks, systemd, cryptsetup, EFI binaries), it runs `check-usb-boot-drive`. That script compares the partition UUID mounted at `/efi` with the one in `/etc/default/usb-boot-drive`. If they don't match, pacman stops before changing anything, including snap-pac's pre-snapshot:

```
The USB boot drive is not plugged in. This transaction rewrites boot files on
its EFI partition (PARTUUID ...): plug it in and run it again.
```

Transactions that touch no boot files are not affected.

### Kernels, Variants and Snapshots

The UKIs all live on the stick's EFI partition, so the mkinitcpio presets, sbctl's signing hook and `manage-snapshot-ukis` work the same as with an internal ESP, and the boot menu shows all kernels, variants and bootable snapshots. Snapshot UKIs are built from `/etc/kernel/cmdline` and unlock through the stick's header like the others.

Snapshots taken while the stick is out (snapper's timeline keeps running) can't get a UKI yet. In that case `manage-snapshot-ukis refresh` creates `/var/lib/manage-snapshot-ukis/refresh-pending` and exits. When the stick is plugged back in, a udev rule matching its EFI partition UUID starts `snapshot-ukis-catch-up.service`, which runs the refresh if that file exists.

## Recovery System

With `usb_boot.recovery_system: true`, the stick also carries the Arch live system, started by a UKI signed with your own Secure Boot keys.

`usb_boot.iso_path` points at an Arch ISO file or at the live medium itself (`/dev/sr0`). The installer copies the ISO's `arch/` tree to the recovery partition, which it sizes from the ISO 9660 volume descriptor.

The recovery UKI contains the ISO's kernel and initramfs and this command line:

```
archisobasedir=arch archisodevice=UUID=<recovery partition> cms_verify=y
```

It shows up in the boot menu as "Arch Linux recovery (2026.01.01)", below the system's own entries.

Secure Boot checks the UKI's signature, which covers the kernel, the initramfs and the command line. `cms_verify=y` makes the initramfs check the live root image (`airootfs.sfs`) against a CMS signature before mounting it, so a modified root image on the stick won't boot.

That signature is made by the installer, not by Arch. archiso verifies against a certificate stored in its initramfs, and the certificate in the ISO expires (for the 2026.01 ISO, on 2026-05-30). After that date the live system would refuse to boot. So the installer signs the root image with a one-time key that only exists on the live system's tmpfs, deletes the key, and adds its certificate (valid for 100 years) to the UKI as a second initramfs, which replaces `/codesign.crt`. The certificate is inside the signed UKI, so it can't be replaced either.

The archiso initramfs contains every module and firmware file, so the recovery UKI takes about 250 MB of the EFI partition.

To open the installed system from the recovery system:

```bash
cryptsetup open --header /dev/sdX2 /dev/nvme0n1 cryptroot
mount -o subvol=@ /dev/mapper/cryptroot /mnt
```

## Spare Stick

The header on the stick is the only copy of the wrapped volume key. If you lose the stick, you lose the data. A spare is a clone that boots and unlocks the system on its own:

```bash
make clone_usb_boot USB_DEVICE=/dev/sdX SPARE_DEVICE=/dev/sdY
```

This copies the partition table (including the partition UUIDs) and every partition, then checks that the two headers match. Since both sticks have the same partition UUIDs, only plug in one at a time. The spare contains the UKIs from the time it was cloned, so clone it again after kernel updates. An older UKI still unlocks the disk, but its kernel may not find its modules anymore.

You can also keep a copy of just the header: `cryptsetup luksHeaderBackup /dev/sdX2 --header-backup-file header.img`, and later `cryptsetup open --header header.img`. Treat that file like the stick itself.

## Limits

Deniability here is about what the machine reveals without the stick. Without it, the internal disk has no partition table, no boot code, no LUKS metadata and no zero-filled areas, and the firmware has no boot entry or systemd-boot token.

Some things remain visible or are out of scope:

- The firmware shows the Secure Boot keys sbctl enrolled (platform key, key exchange key and db key) in its setup screens and in NVRAM.
- The disk's SMART counters (data written, power-on hours) show that it is in use, whatever is on it.
- Two images of the disk taken at different times show which areas changed. The random fill hides the content, but not where data was written.
- The stick contains the LUKS header (which `blkid` reports as LUKS2) and UKIs whose command lines name the internal disk by model and serial number or WWN. Anyone holding the stick can tell which disk is encrypted, and with the passphrase they can open it.
- While the system is running, the volume key is in memory.
- The migration step only reads installations that keep their header on their own disk. An installation with the header on a stick has to be copied by hand.

## Configuration

```yaml
storage:
  wipe_method: secure    # or skip, for a disk that was already filled
  efi_size_mb: 2048      # size of the EFI partition on the stick

usb_boot:
  enabled: true
  device: /dev/sdb       # the whole stick is erased
  recovery_system: true
  iso_path: /dev/sr0     # an Arch ISO file, or the live medium
```

| Environment variable | Setting |
| --- | --- |
| `ENABLE_USB_BOOT` | `usb_boot.enabled` |
| `USB_BOOT_DEVICE` | `usb_boot.device` |
| `ENABLE_RECOVERY_SYSTEM` | `usb_boot.recovery_system` |
| `ISO_PATH` | `usb_boot.iso_path` |
| `WIPE_METHOD` | `storage.wipe_method` |

In the interactive setup, the USB questions come before the Storage questions: whether to use a USB boot drive, which drive (from the detected disks), whether to add the recovery system, and if so which ISO.

The installer won't start if the stick is also the target disk, if the recovery system is enabled without an ISO, or if the wipe method is `quick` or `discard`.

## Troubleshooting

### The machine doesn't boot from the stick

1. Pick the stick in the firmware's boot menu, or move USB devices to the top of the boot order.
2. With Secure Boot enabled, the firmware needs the keys sbctl enrolled during installation.
3. Check the stick's contents from another system:
   ```bash
   mount /dev/sdX1 /mnt/usb-efi
   ls /mnt/usb-efi/EFI/BOOT/ /mnt/usb-efi/EFI/Linux/ /mnt/usb-efi/loader/entries/
   ```

### The passphrase prompt never appears

The initramfs waits for both devices named on the command line. Check that the header partition on the stick has the PARTUUID from `/etc/kernel/cmdline`, and that the disk's by-id name from that file still exists:

```bash
blkid -s PARTUUID /dev/sdX2
ls -l /dev/disk/by-id/ | grep nvme0n1
```

Replacing the disk, or a firmware update that changes how it reports its serial number, changes the by-id name. In that case, boot the recovery system, open the disk with `cryptsetup open --header`, put the new name in `/etc/kernel/cmdline*` and rebuild the UKIs.

### Another operating system offers to initialize the disk

Windows Disk Management and most OS installers see a disk without a partition table and offer to initialize it. Accepting writes a partition table over the start and end of the encrypted data and damages the filesystem inside. Decline, or keep other operating systems away from the disk.

### pacman says "The USB boot drive is not plugged in"

Plug in the stick and run the command again. `ls /efi` should list `EFI` and `loader` within a few seconds.

### Snapshot entries are missing after the stick was out

```bash
systemctl status snapshot-ukis-catch-up.service
manage-snapshot-ukis refresh
```

### The recovery system stops before reaching a shell

If `cms_verify` fails, archiso drops to the initramfs shell. The live root image on the recovery partition no longer matches the signature made at installation, which means the stick was modified. Boot an Arch ISO from other media instead.
