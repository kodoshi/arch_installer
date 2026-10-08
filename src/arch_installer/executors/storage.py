"""disk partitioning, LUKS encryption, BTRFS subvolumes, EFI partition and swapfile."""

import logging
import time

from arch_installer.config.models import CRYPTROOT_MAPPER_NAME, WipeMethod
from arch_installer.executors.base import (
    TARGET_EFI,
    TARGET_ROOT,
    Executor,
    file_exists,
    is_mountpoint,
    path_exists,
)

logger = logging.getLogger(__name__)


class StorageStepExecutor(Executor):
    def execute(self) -> None:
        storage = self._config.storage
        if is_mountpoint(self._runner, TARGET_ROOT):
            source = self._runner.run(
                f"findmnt -n -o SOURCE {TARGET_ROOT}", raise_on_nonzero_exit=False
            ).stdout.strip()
            logger.info(
                "%s is already mounted from %s, keeping the existing storage", TARGET_ROOT, source
            )
            if not is_mountpoint(self._runner, TARGET_EFI):
                logger.warning("%s is not mounted. Please ensure it is mounted.", TARGET_EFI)
            return

        logger.info("Converging storage on %s...", storage.target_disk)
        self._cleanup_stale_mounts()

        # migration copied the old data to staging already and needs a new LUKS volume
        # with the new password, so its disk is always wiped
        wipe_method = WipeMethod.QUICK if self._config.migration.enabled else storage.wipe_method

        if wipe_method != WipeMethod.SKIP:
            self._wipe_disk(wipe_method)
            self._create_partitions()
        elif not self._partitions_exist():
            self._create_partitions()
        elif not self._luks_is_usable():
            logger.info("Partitions exist but LUKS is unusable, re-partitioning...")
            self._wipe_disk(WipeMethod.QUICK)
            self._create_partitions()
        else:
            logger.info("Partitions already exist.")

        self._setup_luks()
        self._setup_btrfs()
        self._mount_filesystems()
        if storage.swap.enabled and storage.swap.size_mb > 0:
            self._create_swapfile()
        logger.info("Storage provisioning complete.")

    def _cleanup_stale_mounts(self) -> None:
        self._runner.run(f"umount -R {TARGET_ROOT}", raise_on_nonzero_exit=False)
        self._runner.run("swapoff -a", raise_on_nonzero_exit=False)
        self._runner.run(f"cryptsetup close {CRYPTROOT_MAPPER_NAME}", raise_on_nonzero_exit=False)
        # leftover mapping from an interrupted secure wipe
        self._runner.run("cryptsetup close container", raise_on_nonzero_exit=False)
        self._runner.run("dmsetup remove_all", raise_on_nonzero_exit=False)

    def _partitions_exist(self) -> bool:
        storage = self._config.storage
        return all(
            self._runner.run(f"lsblk {partition}", raise_on_nonzero_exit=False).success
            for partition in (storage.efi_partition, storage.root_partition)
        )

    def _luks_is_usable(self) -> bool:
        root_partition = self._config.storage.root_partition
        if not self._runner.run(
            f"cryptsetup isLuks {root_partition}", raise_on_nonzero_exit=False
        ).success:
            logger.info("Partition is not a valid LUKS volume")
            return False

        unlocks = self._runner.run(
            f"cryptsetup open --test-passphrase {root_partition}",
            input_data=self._config.credentials.luks_password,
            raise_on_nonzero_exit=False,
        ).success
        if not unlocks:
            logger.info("LUKS password doesn't match, the volume needs re-creation")
        return unlocks

    def _wipe_disk(self, method: WipeMethod) -> None:
        disk = self._config.storage.target_disk
        logger.info("Wiping %s using method: %s...", disk, method)

        if method == WipeMethod.SECURE:
            logger.info("Filling disk with random data (this will take some time)...")
            # shred handles I/O on virtual disks better than a raw dd
            shredded = self._runner.run(f"shred -v -n 1 {disk}", raise_on_nonzero_exit=False)
            if not shredded.success:
                logger.info("shred failed, falling back to dd...")
                self._runner.run(
                    f"dd bs=4M if=/dev/urandom of={disk} conv=fsync status=progress",
                    raise_on_nonzero_exit=False,
                )
            self._runner.run("sync")
            self._runner.run(f"blockdev --flushbufs {disk}", raise_on_nonzero_exit=False)
            time.sleep(2)
        elif method == WipeMethod.DISCARD:
            logger.info("Discarding blocks (blkdiscard)...")
            self._runner.run(f"blkdiscard -f {disk}", raise_on_nonzero_exit=False)

        self._runner.run(f"wipefs -af {disk}", raise_on_nonzero_exit=False)
        self._runner.run(f"sgdisk -Z {disk}", raise_on_nonzero_exit=False)
        self._runner.run(f"wipefs -af {disk}", raise_on_nonzero_exit=False)
        # make the kernel forget everything it cached about the old layout
        self._runner.run(f"blockdev --rereadpt {disk}", raise_on_nonzero_exit=False)
        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)
        self._runner.run(f"partprobe {disk}", raise_on_nonzero_exit=False)
        time.sleep(2)

    def _create_partitions(self) -> None:
        disk = self._config.storage.target_disk
        efi_size_mb = self._config.storage.efi_size_mb
        logger.info("Creating partitions on %s (EFI: %sMiB)...", disk, efi_size_mb)

        if "loop" in disk:
            self._prepare_loop_device(disk)

        self._runner.run(f"sgdisk -Z {disk}")
        self._runner.run(f"sgdisk -n1:0:+{efi_size_mb}M -t1:ef00 {disk}")
        self._runner.run(f"sgdisk -n2:0:0 -t2:8304 {disk}")
        self._runner.run(f"partprobe {disk}", raise_on_nonzero_exit=False)
        self._runner.run(f"blockdev --rereadpt {disk}", raise_on_nonzero_exit=False)

        if "loop" in disk:
            self._create_loop_partition_nodes(disk)
        self._wait_for_partitions()

    def _prepare_loop_device(self, disk: str) -> None:
        size = self._runner.run(
            f"blockdev --getsize64 {disk}", raise_on_nonzero_exit=False
        ).stdout.strip()
        if size in ("", "0"):
            self._runner.run(f"losetup -c {disk}", raise_on_nonzero_exit=False)
            size = self._runner.run(
                f"blockdev --getsize64 {disk}", raise_on_nonzero_exit=False
            ).stdout.strip()
            if size in ("", "0"):
                raise RuntimeError(f"Loop device {disk} has 0 size. Cannot partition.")
        logger.info("Loop device size: %s bytes", size)

    def _create_loop_partition_nodes(self, disk: str) -> None:
        listing = self._runner.run(f"lsblk -r -n -o NAME,MAJ:MIN,TYPE {disk}").stdout
        for line in listing.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "part":
                name, major_minor = parts[0], parts[1]
                device_node = f"/dev/{name}"
                if not path_exists(self._runner, device_node):
                    major, minor = major_minor.split(":")
                    logger.info("Creating device node %s (%s)", device_node, major_minor)
                    self._runner.run(f"mknod {device_node} b {major} {minor}")

    def _wait_for_partitions(self) -> None:
        root_partition = self._config.storage.root_partition
        for _ in range(20):
            if path_exists(self._runner, root_partition):
                return
            time.sleep(1)
            self._runner.run(
                f"partprobe {self._config.storage.target_disk}", raise_on_nonzero_exit=False
            )
        raise RuntimeError(f"Partition {root_partition} failed to appear")

    def _setup_luks(self) -> None:
        root_partition = self._config.storage.root_partition
        status = self._runner.run(
            f"cryptsetup status {CRYPTROOT_MAPPER_NAME}", raise_on_nonzero_exit=False
        )
        if status.success and "active" in status.stdout.lower():
            if root_partition in status.stdout:
                logger.info("LUKS volume already open on %s", root_partition)
                return
            raise RuntimeError(f"{CRYPTROOT_MAPPER_NAME} is open but points to the wrong device")

        if not self._runner.run(
            f"cryptsetup isLuks {root_partition}", raise_on_nonzero_exit=False
        ).success:
            logger.info("Formatting LUKS volume...")
            self._format_luks()
        self._open_luks()

    def _format_luks(self) -> None:
        luks = self._config.storage.luks
        self._runner.run(
            "cryptsetup luksFormat --batch-mode "
            f"--type {luks.type} "
            f"--cipher {luks.cipher} "
            f"--key-size {luks.key_size} "
            f"--hash {luks.hash} "
            f"--pbkdf {luks.pbkdf} "
            f"--pbkdf-memory {luks.pbkdf_memory} "
            f"--pbkdf-parallel {luks.pbkdf_parallel} "
            f"--iter-time {luks.pbkdf_time_ms} "
            f"--key-file - {self._config.storage.root_partition}",
            input_data=self._config.credentials.luks_password,
        )

    def _open_luks(self) -> None:
        self._runner.run(
            f"cryptsetup open --key-file - {self._config.storage.root_partition} {CRYPTROOT_MAPPER_NAME}",
            input_data=self._config.credentials.luks_password,
        )
        # give device mapper a moment before udev settles the new node
        time.sleep(1)
        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)

    def _setup_btrfs(self) -> None:
        cryptroot = self._config.storage.cryptroot_device
        label = self._config.storage.btrfs.label

        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)
        if (
            'TYPE="btrfs"'
            in self._runner.run(f"blkid {cryptroot}", raise_on_nonzero_exit=False).stdout
        ):
            logger.info("BTRFS filesystem detected.")
        else:
            logger.info("Formatting BTRFS with label '%s'...", label)
            self._runner.run(f"wipefs -af {cryptroot}", raise_on_nonzero_exit=False)
            self._runner.run("sync")
            time.sleep(1)
            self._runner.run(f'mkfs.btrfs -f -L "{label}" {cryptroot}')

        if not is_mountpoint(self._runner, TARGET_ROOT):
            self._runner.run(f"mount {cryptroot} {TARGET_ROOT}")
        self._create_subvolumes()
        self._runner.run(f"umount {TARGET_ROOT}")

    def _create_subvolumes(self) -> None:
        listing = self._runner.run(
            f"btrfs subvolume list {TARGET_ROOT}", raise_on_nonzero_exit=False
        )
        # lines look like "ID 256 gen 7 top level 5 path @home"
        existing = {
            line.split(" path ")[-1].strip()
            for line in listing.stdout.strip().split("\n")
            if " path " in line
        }
        for subvolume in self._config.storage.btrfs.subvolumes:
            if subvolume.name in existing:
                logger.info("Subvolume %s exists.", subvolume.name)
            else:
                logger.info("Creating subvolume %s...", subvolume.name)
                self._runner.run(f"btrfs subvolume create {TARGET_ROOT}/{subvolume.name}")

    def _mount_filesystems(self) -> None:
        storage = self._config.storage
        mount_options = storage.btrfs.mount_options

        self._runner.run(
            f"mount -o subvol=@,{mount_options} {storage.cryptroot_device} {TARGET_ROOT}"
        )
        for subvolume in storage.btrfs.subvolumes:
            if subvolume.name == "@":
                continue
            mountpoint = f"{TARGET_ROOT}{subvolume.mountpoint}"
            self._runner.run(f"mkdir -p {mountpoint}", raise_on_nonzero_exit=False)
            self._runner.run(
                f"mount -o subvol={subvolume.name},{mount_options} {storage.cryptroot_device} {mountpoint}"
            )
            if subvolume.nocow:
                self._runner.run(f"chattr +C {mountpoint}", raise_on_nonzero_exit=False)

        self._mount_efi_partition()

    def _mount_efi_partition(self) -> None:
        efi_partition = self._config.storage.efi_partition
        if (
            'TYPE="vfat"'
            not in self._runner.run(f"blkid {efi_partition}", raise_on_nonzero_exit=False).stdout
        ):
            logger.info("Formatting EFI partition...")
            self._runner.run(f"mkfs.vfat -F32 -n EFI {efi_partition}")

        self._runner.run(f"mkdir -p {TARGET_EFI}")
        # FAT has no per-file permissions, so the mount mask decides who can read the
        # ESP (including the boot loader random seed). genfstab carries it into fstab
        self._runner.run(f"mount -o umask=0077 {efi_partition} {TARGET_EFI}")

    def _create_swapfile(self) -> None:
        swap = self._config.storage.swap
        swap_path = f"{TARGET_ROOT}{swap.path}"

        if file_exists(self._runner, swap_path):
            logger.info("Swapfile already exists.")
            self._runner.run(f"swapon {swap_path}", raise_on_nonzero_exit=False)
            return

        logger.info("Creating %sMB swapfile...", swap.size_mb)
        # a swapfile on btrfs must be NOCOW and uncompressed before any data is written
        self._runner.run(f"truncate -s 0 {swap_path}")
        self._runner.run(f"chattr +C {swap_path}", raise_on_nonzero_exit=False)
        self._runner.run(
            f"btrfs property set {swap_path} compression none", raise_on_nonzero_exit=False
        )
        if not self._runner.run(
            f"fallocate -l {swap.size_mb}M {swap_path}", raise_on_nonzero_exit=False
        ).success:
            self._runner.run(f"dd if=/dev/zero of={swap_path} bs=1M count={swap.size_mb}")
        self._runner.run(f"chmod 600 {swap_path}")
        self._runner.run(f"mkswap {swap_path}")
        self._runner.run(f"swapon {swap_path}")
