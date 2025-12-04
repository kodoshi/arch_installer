import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from arch_installer.config.models import BootConfig, SnapperConfig
from arch_installer.core.command import CommandRunner
from arch_installer.errors import ArchInstallerError


class BTRFSSnapshotUKIError(ArchInstallerError):
    pass


class BTRFSSnapshotBootMode(Enum):
    READ_WRITE = "rw"
    READ_ONLY = "ro"


@dataclass(frozen=True)
class EFISpaceUsage:
    total: str
    used: str
    available: str
    percent_used: str


@dataclass(frozen=True)
class BTRFSSnapshotInfo:
    number: int
    description: str
    date: str
    snapshot_type: str
    subvol_path: str
    is_readonly: bool


@dataclass(frozen=True)
class UnifiedKernelImagePaths:
    efi_dir: Path
    uki_dir: Path
    snapshots_dir: Path
    uki_prefix: str


class BootableBTRFSSnapshotManager:
    """
    manages bootable BTRFS snapshot UKI generation for systemd-boot.

    class encapsulates the functionality to create Unified Kernel Images
    for BTRFS snapshots, making them bootable from the systemd-boot menu. it handles:

    - detecting available BTRFS snapshots from snapper
    - building kernel command lines with snapshot-specific subvolume paths
    - generating UKIs using ukify or objcopy fallback
    - signing UKIs for secure boot via sbctl
    - managing snapshot writability for booting
    """

    def __init__(
        self,
        cmd: CommandRunner,
        boot_config: BootConfig,
        snapper_config: SnapperConfig,
        efi_mountpoint: str = "/efi",
        root_mountpoint: str = "/",
    ) -> None:
        self._cmd = cmd
        self._boot_config = boot_config
        self._snapper_config = snapper_config

        self._paths = UnifiedKernelImagePaths(
            efi_dir=Path(efi_mountpoint),
            uki_dir=Path(efi_mountpoint) / "EFI" / "Linux",
            snapshots_dir=Path(root_mountpoint) / ".snapshots",
            uki_prefix="snapshot",
        )
        self._default_snapshot_count = 7

    def refresh_snapshot_ukis(
        self,
        max_snapshots: Optional[int] = None,
        kernel_name: Optional[str] = None,
    ) -> int:
        """generate bootable UKIs for recent snapshots.

        returns the number of successfully created UKIs.
        """
        if max_snapshots is None:
            max_snapshots = self._default_snapshot_count

        # check available EFI space before proceeding
        space_info = self.get_efi_space_usage()
        if space_info:
            percent = int(space_info.percent_used.rstrip("%"))
            if percent > 90:
                print(
                    f"    Warning: EFI partition is {percent}% full ({space_info.available} available)"
                )

        self._ensure_uki_directory_exists()
        self._remove_existing_snapshot_ukis()

        if not self._paths.snapshots_dir.exists():
            return 0

        kernel = kernel_name or self._detect_default_kernel()
        snapshots = self._get_sorted_snapshots(max_snapshots)

        if not snapshots:
            return 0

        secure_boot_available = self._check_secure_boot_signing_available()
        success_count = 0

        for snapshot in snapshots:
            writable_snapshot = self._ensure_snapshot_writable(snapshot)
            cmdline = self._build_snapshot_cmdline(
                writable_snapshot.subvol_path,
                BTRFSSnapshotBootMode.READ_WRITE,
            )

            if self._generate_uki_for_snapshot(
                writable_snapshot,
                kernel,
                cmdline,
                secure_boot_available,
            ):
                success_count += 1

        return success_count

    def list_available_snapshots(self) -> list[BTRFSSnapshotInfo]:
        """return list of available BTRFS root snapshots."""
        if not self._paths.snapshots_dir.exists():
            return []

        snapshots = []
        for snapshot_dir in sorted(self._paths.snapshots_dir.iterdir()):
            if not snapshot_dir.is_dir():
                continue

            snapshot_path = snapshot_dir / "snapshot"
            if not snapshot_path.exists():
                continue

            try:
                number = int(snapshot_dir.name)
            except ValueError:
                continue

            info = self._parse_snapshot_info_xml(snapshot_dir, number)
            snapshots.append(info)

        return snapshots

    def list_snapshot_ukis(self) -> list[tuple[str, str, bool]]:
        """return list of snapshot UKIs as (name, size, is_signed) tuples."""
        ukis = []
        pattern = f"{self._paths.uki_prefix}-*.efi"

        for uki_path in self._paths.uki_dir.glob(pattern):
            name = uki_path.stem
            size = self._get_file_size_human(uki_path)
            is_signed = self._verify_uki_signature(uki_path)
            ukis.append((name, size, is_signed))

        return ukis

    def cleanup_snapshot_ukis(self) -> int:
        """remove all snapshot UKIs and return count of removed files."""
        pattern = f"{self._paths.uki_prefix}-*.efi"
        removed_count = 0

        for uki_path in self._paths.uki_dir.glob(pattern):
            uki_path.unlink()
            self._remove_from_sbctl_database(uki_path)
            removed_count += 1

        return removed_count

    def get_efi_space_usage(self) -> Optional[EFISpaceUsage]:
        result = self._cmd.run(f"df -h {self._paths.efi_dir}")
        lines = result.stdout.strip().split("\n")

        if len(lines) < 2:
            return None

        parts = lines[1].split()
        if len(parts) < 5:
            return None

        return EFISpaceUsage(
            total=parts[1],
            used=parts[2],
            available=parts[3],
            percent_used=parts[4],
        )

    def _ensure_uki_directory_exists(self) -> None:
        """create UKI directory if it doesn't exist."""
        self._paths.uki_dir.mkdir(parents=True, exist_ok=True)

    def _remove_existing_snapshot_ukis(self) -> None:
        """remove all existing snapshot UKIs."""
        pattern = f"{self._paths.uki_prefix}-*.efi"
        for uki_path in self._paths.uki_dir.glob(pattern):
            uki_path.unlink()

    def _detect_default_kernel(self) -> str:
        """detect the default kernel to use for UKI generation."""
        # priority: hardened > mainline > lts > any installed
        preferred_order = ["linux-hardened", "linux", "linux-lts"]

        for kernel in preferred_order:
            vmlinuz = Path(f"/boot/vmlinuz-{kernel}")
            if vmlinuz.exists():
                return kernel

        # fallback: find any installed kernel
        for vmlinuz in Path("/boot").glob("vmlinuz-*"):
            kernel_name = vmlinuz.name.replace("vmlinuz-", "")
            return kernel_name

        raise BTRFSSnapshotUKIError("no kernel found in /boot")

    def _get_sorted_snapshots(self, max_count: int) -> list[BTRFSSnapshotInfo]:
        """get snapshots sorted by number descending, limited to max_count."""
        all_snapshots = self.list_available_snapshots()
        sorted_snapshots = sorted(all_snapshots, key=lambda s: s.number, reverse=True)
        return sorted_snapshots[:max_count]

    def _parse_snapshot_info_xml(
        self,
        snapshot_dir: Path,
        number: int,
    ) -> BTRFSSnapshotInfo:
        """parse snapshot info from snapper's info.xml file."""
        info_file = snapshot_dir / "info.xml"
        description = f"Snapshot {number}"
        date = "Unknown"
        snapshot_type = "single"

        if info_file.exists():
            try:
                tree = ET.parse(info_file)
                root = tree.getroot()

                desc_elem = root.find(".//description")
                if desc_elem is not None and desc_elem.text:
                    description = desc_elem.text

                date_elem = root.find(".//date")
                if date_elem is not None and date_elem.text:
                    date = date_elem.text

                type_elem = root.find(".//type")
                if type_elem is not None and type_elem.text:
                    snapshot_type = type_elem.text

            except ET.ParseError:
                pass

        snapshot_path = snapshot_dir / "snapshot"
        is_readonly = self._check_snapshot_readonly(snapshot_path)

        return BTRFSSnapshotInfo(
            number=number,
            description=description,
            date=date,
            snapshot_type=snapshot_type,
            subvol_path=f"@snapshots/{number}/snapshot",
            is_readonly=is_readonly,
        )

    def _check_snapshot_readonly(self, snapshot_path: Path) -> bool:
        """check if a snapshot is read-only."""
        result = self._cmd.run(
            f"btrfs property get {snapshot_path} ro",
            raise_on_nonzero_exit=False,
        )
        return "ro=true" in result.stdout.lower()

    def _ensure_snapshot_writable(self, snapshot: BTRFSSnapshotInfo) -> BTRFSSnapshotInfo:
        """make snapshot writable if it's read-only (required for booting)."""
        if not snapshot.is_readonly:
            return snapshot

        snapshot_path = self._paths.snapshots_dir / str(snapshot.number) / "snapshot"
        self._cmd.run(f"btrfs property set {snapshot_path} ro false", raise_on_nonzero_exit=False)

        return BTRFSSnapshotInfo(
            number=snapshot.number,
            description=snapshot.description,
            date=snapshot.date,
            snapshot_type=snapshot.snapshot_type,
            subvol_path=snapshot.subvol_path,
            is_readonly=False,
        )

    def _build_snapshot_cmdline(
        self,
        snapshot_subvol: str,
        mode: BTRFSSnapshotBootMode,
    ) -> str:
        """build kernel command line for booting into a snapshot."""
        base_cmdline = self._get_base_cmdline()

        if "rootflags=" in base_cmdline:
            base_cmdline = re.sub(
                r"rootflags=([^\s]*)?subvol=[^,\s]*",
                f"rootflags=\\1subvol={snapshot_subvol}",
                base_cmdline,
            )
        else:
            base_cmdline = f"{base_cmdline} rootflags=subvol={snapshot_subvol}"

        # remove any existing rw/ro flags and add the mode
        base_cmdline = base_cmdline.replace(" rw ", " ").replace(" ro ", " ")
        base_cmdline = base_cmdline.rstrip()
        if base_cmdline.endswith(" rw") or base_cmdline.endswith(" ro"):
            base_cmdline = base_cmdline[:-3]

        base_cmdline = f"{base_cmdline} {mode.value}"
        return base_cmdline

    def _get_base_cmdline(self) -> str:
        """get the base kernel command line from various sources."""
        # try /etc/kernel/cmdline-*-default first
        kernel_dir = Path("/etc/kernel")
        if kernel_dir.exists():
            for cmdline_file in kernel_dir.glob("cmdline-*-default"):
                return cmdline_file.read_text().strip()

            for cmdline_file in kernel_dir.glob("cmdline*"):
                return cmdline_file.read_text().strip()

        # fallback to /etc/kernel/cmdline
        cmdline_path = Path("/etc/kernel/cmdline")
        if cmdline_path.exists():
            return cmdline_path.read_text().strip()

        proc_cmdline = Path("/proc/cmdline")
        if proc_cmdline.exists():
            cmdline = proc_cmdline.read_text().strip()
            cmdline = re.sub(r"initrd=[^\s]*", "", cmdline)
            return " ".join(cmdline.split())

        return self._build_minimal_cmdline()

    def _build_minimal_cmdline(self) -> str:
        """build minimal cmdline with LUKS detection when no existing cmdline found."""
        luks_uuid = self._detect_luks_uuid()

        if luks_uuid:
            return f"rd.luks.name={luks_uuid}=cryptroot root=/dev/mapper/cryptroot rw quiet"

        return "rw quiet"

    def _detect_luks_uuid(self) -> Optional[str]:
        """detect LUKS partition UUID."""
        # try to get from active cryptroot mapping
        result = self._cmd.run("cryptsetup status cryptroot", raise_on_nonzero_exit=False)
        if result.exit_code == 0:
            for line in result.stdout.split("\n"):
                if "device:" in line:
                    device = line.split()[-1]
                    uuid_result = self._cmd.run(
                        f"blkid -s UUID -o value {device}",
                        raise_on_nonzero_exit=False,
                    )
                    if uuid_result.exit_code == 0:
                        return uuid_result.stdout.strip()

        # fallback: find any LUKS device
        result = self._cmd.run(
            "blkid -t TYPE=crypto_LUKS -s UUID -o value",
            raise_on_nonzero_exit=False,
        )
        if result.exit_code == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]

        return None

    def _check_secure_boot_signing_available(self) -> bool:
        """check if secure boot signing is available via sbctl."""
        result = self._cmd.run("command -v sbctl", raise_on_nonzero_exit=False)
        if result.exit_code != 0:
            return False

        result = self._cmd.run("sbctl status", raise_on_nonzero_exit=False)
        return result.exit_code == 0

    def _generate_uki_for_snapshot(
        self,
        snapshot: BTRFSSnapshotInfo,
        kernel: str,
        cmdline: str,
        sign_uki: bool,
    ) -> bool:
        """generate UKI for a single snapshot."""
        vmlinuz = Path(f"/boot/vmlinuz-{kernel}")
        initrd = Path(f"/boot/initramfs-{kernel}.img")
        uki_output = (
            self._paths.uki_dir / f"{self._paths.uki_prefix}-{snapshot.number}-{kernel}.efi"
        )

        if not vmlinuz.exists():
            return False

        if not initrd.exists():
            return False

        # create custom os-release for this snapshot
        osrelease_content = self._create_snapshot_osrelease(snapshot, kernel)
        osrelease_file = Path(f"/tmp/osrelease-{snapshot.number}")
        osrelease_file.write_text(osrelease_content)

        cmdline_file = Path(f"/tmp/cmdline-{snapshot.number}")
        cmdline_file.write_text(cmdline)

        try:
            success = self._build_uki_with_ukify_or_objcopy(
                vmlinuz,
                initrd,
                cmdline_file,
                osrelease_file,
                uki_output,
            )

            if success and sign_uki:
                self._sign_uki_file(uki_output)

            return success
        finally:
            osrelease_file.unlink(missing_ok=True)
            cmdline_file.unlink(missing_ok=True)

    def _create_snapshot_osrelease(
        self,
        snapshot: BTRFSSnapshotInfo,
        kernel: str,
    ) -> str:
        """create custom os-release content for snapshot boot entry."""
        formatted_date = snapshot.date
        if snapshot.date != "Unknown":
            try:
                dt = datetime.fromisoformat(snapshot.date.replace(" ", "T"))
                formatted_date = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                formatted_date = snapshot.date[:16]

        # format kernel name for display
        kernel_short = kernel.replace("linux-", "")
        if kernel_short == "linux":
            kernel_short = "mainline"

        return f"""NAME="Arch Linux"
PRETTY_NAME="Snapshot #{snapshot.number} [{kernel_short}] ({formatted_date})"
ID=arch
BUILD_ID=rolling
VERSION_ID={snapshot.number}
ANSI_COLOR="38;2;23;147;209"
"""

    def _build_uki_with_ukify_or_objcopy(
        self,
        vmlinuz: Path,
        initrd: Path,
        cmdline_file: Path,
        osrelease_file: Path,
        output: Path,
    ) -> bool:
        """build UKI using ukify or objcopy fallback."""
        # try ukify first
        ukify_result = self._cmd.run("command -v ukify", raise_on_nonzero_exit=False)
        if ukify_result.exit_code == 0:
            return self._build_uki_with_ukify(
                vmlinuz,
                initrd,
                cmdline_file,
                osrelease_file,
                output,
            )

        # fallback to objcopy
        objcopy_result = self._cmd.run("command -v objcopy", raise_on_nonzero_exit=False)
        if objcopy_result.exit_code == 0:
            return self._build_uki_with_objcopy(
                vmlinuz,
                initrd,
                cmdline_file,
                osrelease_file,
                output,
            )

        return False

    def _build_uki_with_ukify(
        self,
        vmlinuz: Path,
        initrd: Path,
        cmdline_file: Path,
        osrelease_file: Path,
        output: Path,
    ) -> bool:
        """build UKI using ukify."""
        cmd_parts = [f"ukify build --linux={vmlinuz}"]

        # add microcode first, then main initrd
        intel_ucode = Path("/boot/intel-ucode.img")
        amd_ucode = Path("/boot/amd-ucode.img")

        if intel_ucode.exists():
            cmd_parts.append(f"--initrd={intel_ucode}")
        if amd_ucode.exists():
            cmd_parts.append(f"--initrd={amd_ucode}")

        cmd_parts.extend(
            [
                f"--initrd={initrd}",
                f"--cmdline=@{cmdline_file}",
                f"--os-release=@{osrelease_file}",
                f"--output={output}",
            ]
        )

        result = self._cmd.run(" ".join(cmd_parts), raise_on_nonzero_exit=False)
        return result.exit_code == 0

    def _build_uki_with_objcopy(
        self,
        vmlinuz: Path,
        initrd: Path,
        cmdline_file: Path,
        osrelease_file: Path,
        output: Path,
    ) -> bool:
        """build UKI using objcopy as fallback."""
        stub = Path("/usr/lib/systemd/boot/efi/linuxx64.efi.stub")
        if not stub.exists():
            return False

        # combine initrds (microcode first)
        combined_initrd = Path("/tmp/combined-initrd.img")
        ucode_imgs = list(Path("/boot").glob("*-ucode.img"))

        if ucode_imgs:
            cat_cmd = f"cat {' '.join(str(u) for u in ucode_imgs)} {initrd} > {combined_initrd}"
        else:
            cat_cmd = f"cat {initrd} > {combined_initrd}"

        self._cmd.run(cat_cmd)

        objcopy_cmd = f"""objcopy \
            --add-section .osrel={osrelease_file} --change-section-vma .osrel=0x20000 \
            --add-section .cmdline={cmdline_file} --change-section-vma .cmdline=0x30000 \
            --add-section .linux={vmlinuz} --change-section-vma .linux=0x2000000 \
            --add-section .initrd={combined_initrd} --change-section-vma .initrd=0x3000000 \
            {stub} {output}"""

        result = self._cmd.run(objcopy_cmd, raise_on_nonzero_exit=False)
        combined_initrd.unlink(missing_ok=True)
        return result.exit_code == 0

    def _sign_uki_file(self, uki_path: Path) -> bool:
        """sign UKI file for secure boot using sbctl."""
        result = self._cmd.run(f"sbctl sign {uki_path}", raise_on_nonzero_exit=False)
        return result.exit_code == 0

    def _verify_uki_signature(self, uki_path: Path) -> bool:
        """verify if UKI is signed for secure boot."""
        if not self._check_secure_boot_signing_available():
            return False

        result = self._cmd.run(f"sbctl verify {uki_path}", raise_on_nonzero_exit=False)
        return result.exit_code == 0

    def _remove_from_sbctl_database(self, uki_path: Path) -> None:
        """remove UKI from sbctl database if present."""
        if self._check_secure_boot_signing_available():
            self._cmd.run(f"sbctl remove-file {uki_path}", raise_on_nonzero_exit=False)

    def _get_file_size_human(self, path: Path) -> str:
        """get human-readable file size."""
        try:
            size_bytes = path.stat().st_size
            for unit in ["B", "KB", "MB", "GB"]:
                if size_bytes < 1024:
                    return f"{size_bytes:.1f}{unit}"
                size_bytes /= 1024
            return f"{size_bytes:.1f}TB"
        except OSError:
            return "unknown"


def create_bootable_snapshot_manager_from_config(
    cmd: CommandRunner,
    boot_config: BootConfig,
    snapper_config: SnapperConfig,
    chroot_path: str = "/mnt",
) -> BootableBTRFSSnapshotManager:
    """factory function to create BootableSnapshotManager with chroot paths."""
    return BootableBTRFSSnapshotManager(
        cmd=cmd,
        boot_config=boot_config,
        snapper_config=snapper_config,
        efi_mountpoint=f"{chroot_path}/efi",
        root_mountpoint=chroot_path,
    )
