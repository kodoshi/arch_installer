"""Storage provisioning - disk partitioning, LUKS encryption, BTRFS filesystem."""

import os
import time
from dataclasses import dataclass
from enum import Enum, auto

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.runtime_state import RuntimeConfig


class WipeMethod(Enum):
    QUICK = auto()
    SECURE = auto()
    DISCARD = auto()
    SKIP = auto()


@dataclass
class PartitionInfo:
    device: str
    type_code: str
    size_mib: int
    exists: bool = False


class StorageProvisioner:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._storage_config = config.storage

    def provision_storage(self, wipe_method: WipeMethod = WipeMethod.SKIP) -> None:
        print(f">>>>> Converging storage on {self._state.target_disk}...")

        self._cleanup_stale_mounts()

        partitions_exist = self._partitions_exist()

        # force wipe and partition creation when explicitly requested
        if wipe_method != WipeMethod.SKIP:
            self._wipe_disk(wipe_method)
            self._create_partitions()
        elif not partitions_exist:
            self._create_partitions()
        elif not self._luks_is_usable():
            # partitions exist but LUKS is broken (interrupted setup)
            # force re-partitioning to ensure clean state
            print("    Partitions exist but LUKS is unusable - forcing re-partition...")
            self._wipe_disk(WipeMethod.QUICK)
            self._create_partitions()
        else:
            print("    Partitions already exist.")

        self._setup_luks()
        self._setup_btrfs()
        self._mount_filesystems()

        if not self._state.skip_swap:
            self._create_swapfile()

        print(">>>>> Storage provisioning complete.")

    def _cleanup_stale_mounts(self) -> None:
        self._runner.run(f"umount -R {self._state.target_root}", raise_on_nonzero_exit=False)
        self._runner.run("swapoff -a", raise_on_nonzero_exit=False)
        self._runner.run("cryptsetup close cryptroot", raise_on_nonzero_exit=False)
        # close any leftover device mapper mappings from previous wipe attempts
        self._runner.run("cryptsetup close container", raise_on_nonzero_exit=False)
        self._runner.run("dmsetup remove_all", raise_on_nonzero_exit=False)

    def _partitions_exist(self) -> bool:
        efi_result = self._runner.run(
            f"lsblk {self._state.efi_partition}",
            raise_on_nonzero_exit=False,
        )
        root_result = self._runner.run(
            f"lsblk {self._state.root_partition}",
            raise_on_nonzero_exit=False,
        )
        return efi_result.success and root_result.success

    def _luks_is_usable(self) -> bool:
        """check if LUKS is properly formatted and can be opened with provided password."""
        # first check if it's even a LUKS device
        is_luks = self._runner.run(
            f"cryptsetup isLuks {self._state.root_partition}",
            raise_on_nonzero_exit=False,
        )
        if not is_luks.success:
            print("    Partition is not a valid LUKS volume")
            return False

        # try to open it with the provided password
        result = self._runner.run(
            f"cryptsetup open --test-passphrase {self._state.root_partition}",
            input_data=self._state.luks_password,
            raise_on_nonzero_exit=False,
        )
        if not result.success:
            print("    LUKS password doesn't match - volume needs re-creation")
            return False

        return True

    def _wipe_disk(self, method: WipeMethod) -> None:
        disk = self._state.target_disk
        print(f">>>>> Wiping {disk} using method: {method.name}...")

        if method == WipeMethod.SECURE:
            print("    Filling disk with random data (this will take some time)...")
            # use shred for secure wiping - it's designed for this purpose
            # and handles I/O better than raw dd on virtual disks
            # -v: verbose, -n 1: one pass of random data
            result = self._runner.run(
                f"shred -v -n 1 {disk}",
                raise_on_nonzero_exit=False,
            )
            if not result.success:
                # fallback to dd with smaller blocks and fsync for virtual disks
                print("    shred failed, falling back to dd...")
                self._runner.run(
                    f"dd bs=4M if=/dev/urandom of={disk} conv=fsync status=progress",
                    raise_on_nonzero_exit=False,
                )
            # comprehensive device reset after secure wipe
            self._runner.run("sync")
            self._runner.run(f"blockdev --flushbufs {disk}", raise_on_nonzero_exit=False)
            time.sleep(2)

        elif method == WipeMethod.DISCARD:
            print("    Discarding blocks (blkdiscard)...")
            self._runner.run(f"blkdiscard -f {disk}", raise_on_nonzero_exit=False)

        if method != WipeMethod.SKIP:
            # comprehensive device cleanup after any wipe
            self._runner.run(f"wipefs -af {disk}", raise_on_nonzero_exit=False)
            self._runner.run(f"sgdisk -Z {disk}", raise_on_nonzero_exit=False)
            self._runner.run(f"wipefs -af {disk}", raise_on_nonzero_exit=False)
            # force kernel to forget any cached state about this disk
            self._runner.run(f"blockdev --rereadpt {disk}", raise_on_nonzero_exit=False)
            self._runner.run("udevadm settle", raise_on_nonzero_exit=False)
            self._runner.run(f"partprobe {disk}", raise_on_nonzero_exit=False)
            time.sleep(2)

    def _create_partitions(self) -> None:
        disk = self._state.target_disk
        efi_size = self._get_efi_size_mb()

        print(f"    Creating partitions on {disk}...")
        print(f"    EFI partition size: {efi_size}MiB")

        if "loop" in disk:
            self._prepare_loop_device(disk)

        self._runner.run(f"sgdisk -Z {disk}")
        self._runner.run(f"sgdisk -n1:0:+{efi_size}M -t1:ef00 {disk}")
        self._runner.run(f"sgdisk -n2:0:0 -t2:8304 {disk}")
        self._runner.run(f"partprobe {disk}", raise_on_nonzero_exit=False)
        self._runner.run(f"blockdev --rereadpt {disk}", raise_on_nonzero_exit=False)

        if "loop" in disk:
            self._create_loop_partition_nodes(disk)

        self._wait_for_partitions()

    def _get_efi_size_mb(self) -> int:
        test_size = os.environ.get("TEST_EFI_SIZE_MB")
        if test_size:
            return int(test_size)
        return self._storage_config.efi_size_mb

    def _prepare_loop_device(self, disk: str) -> None:
        print("    [CI] Syncing loop device...")
        result = self._runner.run(f"blockdev --getsize64 {disk}", raise_on_nonzero_exit=False)
        size = result.stdout.strip() if result.success else "0"
        print(f"    [CI] Device size: {size} bytes")

        if size == "0":
            self._runner.run(f"losetup -c {disk}", raise_on_nonzero_exit=False)
            result = self._runner.run(f"blockdev --getsize64 {disk}", raise_on_nonzero_exit=False)
            size = result.stdout.strip() if result.success else "0"
            if size == "0":
                raise RuntimeError("Loop device has 0 size. Cannot partition.")

    def _create_loop_partition_nodes(self, disk: str) -> None:
        print("    Ensuring loop partition nodes exist...")
        result = self._runner.run(f"lsblk -r -n -o NAME,MAJ:MIN,TYPE {disk}")

        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "part":
                name, majmin = parts[0], parts[1]
                dev_node = f"/dev/{name}"

                check_result = self._runner.run(f"test -e {dev_node}", raise_on_nonzero_exit=False)
                if not check_result.success:
                    maj, minor = majmin.split(":")
                    print(f"    Creating device node {dev_node} ({majmin})")
                    self._runner.run(f"mknod {dev_node} b {maj} {minor}")

    def _wait_for_partitions(self) -> None:
        print("    Waiting for partitions...")
        for _ in range(20):
            result = self._runner.run(
                f"test -e {self._state.root_partition}",
                raise_on_nonzero_exit=False,
            )
            if result.success:
                print(f"    Partition {self._state.root_partition} found.")
                return
            time.sleep(1)
            self._runner.run(f"partprobe {self._state.target_disk}", raise_on_nonzero_exit=False)

        raise RuntimeError(f"Partition {self._state.root_partition} failed to appear")

    def _setup_luks(self) -> None:
        status_result = self._runner.run(
            "cryptsetup status cryptroot",
            raise_on_nonzero_exit=False,
        )
        if status_result.success and "active" in status_result.stdout.lower():
            if self._state.root_partition in status_result.stdout:
                print(f"    LUKS volume 'cryptroot' already open on {self._state.root_partition}")
                return
            raise RuntimeError("cryptroot is open but points to wrong device")

        is_luks_result = self._runner.run(
            f"cryptsetup isLuks {self._state.root_partition}",
            raise_on_nonzero_exit=False,
        )

        if is_luks_result.success:
            print("    Opening existing LUKS volume...")
            self._open_luks()
        else:
            print("    Formatting LUKS volume...")
            self._format_luks()
            self._open_luks()

    def _format_luks(self) -> None:
        luks = self._storage_config.luks
        partition = self._state.root_partition
        password = self._state.luks_password

        cmd = (
            f"cryptsetup luksFormat --batch-mode "
            f"--type {luks.type} "
            f"--pbkdf {luks.pbkdf} "
            f"--pbkdf-memory {luks.pbkdf_memory} "
            f"--pbkdf-parallel {luks.pbkdf_parallel} "
            f"--iter-time {luks.pbkdf_time_ms} "
            f"--key-file - {partition}"
        )

        self._runner.run(cmd, input_data=password)

    def _open_luks(self) -> None:
        partition = self._state.root_partition
        password = self._state.luks_password

        self._runner.run(
            f"cryptsetup open --key-file - {partition} cryptroot",
            input_data=password,
        )
        # wait for device mapper to fully initialize
        time.sleep(1)
        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)

    def _setup_btrfs(self) -> None:
        cryptroot = self._state.cryptroot_device
        btrfs = self._storage_config.btrfs

        # ensure device is ready before checking/formatting
        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)

        result = self._runner.run(f"blkid {cryptroot}", raise_on_nonzero_exit=False)

        if 'TYPE="btrfs"' not in result.stdout:
            print(f"    Formatting BTRFS with label '{btrfs.label}'...")
            # ensure no stale references before formatting
            self._runner.run(f"wipefs -af {cryptroot}", raise_on_nonzero_exit=False)
            self._runner.run("sync")
            time.sleep(1)
            self._runner.run(f'mkfs.btrfs -f -L "{btrfs.label}" {cryptroot}')
        else:
            print("    BTRFS filesystem detected.")

        if not self._ensure_target_mounted():
            self._runner.run(f"mount {cryptroot} {self._state.target_root}")

        self._create_subvolumes()
        self._runner.run(f"umount {self._state.target_root}")

    def _ensure_target_mounted(self) -> bool:
        result = self._runner.run(
            f"mountpoint -q {self._state.target_root}",
            raise_on_nonzero_exit=False,
        )
        return result.success

    def _ensure_efi_mounted(self) -> bool:
        result = self._runner.run(
            f"mountpoint -q {self._state.efi_mount}",
            raise_on_nonzero_exit=False,
        )
        return result.success

    def _create_subvolumes(self) -> None:
        target = self._state.target_root

        # get list of existing subvolumes
        result = self._runner.run(f"btrfs subvolume list {target}", raise_on_nonzero_exit=False)
        existing_subvols = set()
        if result.success:
            for line in result.stdout.strip().split("\n"):
                # format: "ID xxx gen xxx top level xxx path SUBVOL_NAME"
                if " path " in line:
                    subvol_name = line.split(" path ")[-1].strip()
                    existing_subvols.add(subvol_name)
            print(f"    Existing subvolumes: {sorted(existing_subvols)}")

        for subvolume in self._storage_config.btrfs.subvolumes:
            subvolume_name = subvolume.name
            subvolume_path = f"{target}/{subvolume_name}"

            if subvolume_name in existing_subvols:
                print(f"    Subvolume {subvolume_name} exists.")
            else:
                print(f"    Creating subvolume {subvolume_name}...")
                self._runner.run(f"btrfs subvolume create {subvolume_path}")

    def _mount_filesystems(self) -> None:
        cryptroot = self._state.cryptroot_device
        mount_opts = self._storage_config.btrfs.mount_options
        target = self._state.target_root

        # mount root subvolume first
        print(f"    Mounting @ to {target}...")
        self._runner.run(f"mount -o subvol=@,{mount_opts} {cryptroot} {target}")

        # create directories and mount all subvolumes from config
        print("    Mounting subvolumes from configuration...")
        for subvol in self._storage_config.btrfs.subvolumes:
            if subvol.name == "@":
                continue  # already mounted

            mountpoint = f"{target}{subvol.mountpoint}"
            # ensure parent directory exists
            self._runner.run(f"mkdir -p {mountpoint}", raise_on_nonzero_exit=False)
            self._mount_subvolume(subvol.name, mountpoint, mount_opts)

        self._disable_cow_on_subvolumes()
        self._setup_efi_partition()

    def _mount_subvolume(self, subvol: str, mountpoint: str, mount_opts: str) -> None:
        cryptroot = self._state.cryptroot_device
        self._runner.run(f"mount -o subvol={subvol},{mount_opts} {cryptroot} {mountpoint}")

    def _disable_cow_on_subvolumes(self) -> None:
        print("    Disabling CoW for performance-sensitive directories...")
        target = self._state.target_root

        # disable CoW on subvolumes marked as nocow in config
        for subvol in self._storage_config.btrfs.subvolumes:
            if subvol.nocow:
                path = f"{target}{subvol.mountpoint}"
                self._runner.run(f"chattr +C {path}", raise_on_nonzero_exit=False)

    def _setup_efi_partition(self) -> None:
        efi_part = self._state.efi_partition
        target = self._state.target_root
        efi_mount = f"{target}/efi"

        result = self._runner.run(f"blkid {efi_part}", raise_on_nonzero_exit=False)
        if 'TYPE="vfat"' not in result.stdout:
            print("    Formatting EFI partition...")
            self._runner.run(f"mkfs.vfat -F32 -n EFI {efi_part}")

        print(f"    Mounting EFI to {efi_mount}...")
        self._runner.run(f"mkdir -p {efi_mount}")
        self._runner.run(f"mount {efi_part} {efi_mount}")

    def _create_swapfile(self) -> None:
        swap_size_mb = self._get_swap_size_mb()
        if swap_size_mb <= 0:
            print("    Swapfile disabled.")
            return

        swap_path = f"{self._state.target_root}{self._storage_config.swap.path}"

        result = self._runner.run(f"test -f {swap_path}", raise_on_nonzero_exit=False)
        if result.success:
            print("    Swapfile already exists.")
            self._runner.run(f"swapon {swap_path}", raise_on_nonzero_exit=False)
            return

        print(f"    Creating {swap_size_mb}MB swapfile...")

        self._runner.run(f"truncate -s 0 {swap_path}")
        self._runner.run(f"chattr +C {swap_path}", raise_on_nonzero_exit=False)
        self._runner.run(
            f"btrfs property set {swap_path} compression none", raise_on_nonzero_exit=False
        )

        alloc_result = self._runner.run(
            f"fallocate -l {swap_size_mb}M {swap_path}", raise_on_nonzero_exit=False
        )
        if not alloc_result.success:
            self._runner.run(f"dd if=/dev/zero of={swap_path} bs=1M count={swap_size_mb}")

        self._runner.run(f"chmod 600 {swap_path}")
        self._runner.run(f"mkswap {swap_path}")
        self._runner.run(f"swapon {swap_path}")

        print(f"    Swapfile created and activated at {swap_path}")

    def _get_swap_size_mb(self) -> int:
        if self._state.skip_swap:
            return 0

        test_size = os.environ.get("TEST_SWAP_SIZE_MB")
        if test_size:
            return int(test_size)

        if self._state.swap_size_mb > 0:
            return self._state.swap_size_mb

        if self._storage_config.swap.enabled:
            return self._storage_config.swap.size_mb

        return 0
