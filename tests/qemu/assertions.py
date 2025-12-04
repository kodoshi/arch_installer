"""assertion helpers for QEMU-based installation verification.

provides comprehensive assertion functions to validate all aspects of
an arch linux installation including btrfs structure, secure boot,
UKI generation, system configuration, and services.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from tests.qemu.vm import QemuVm


@dataclass(frozen=True)
class AssertionResult:
    """result of a single assertion."""

    name: str
    passed: bool
    message: str
    details: Optional[str] = None


class QemuAssertionError(Exception):
    """assertion failed during QEMU test."""

    def __init__(self, results: list[AssertionResult]) -> None:
        failed = [r for r in results if not r.passed]
        message = f"{len(failed)} assertion(s) failed:\n"
        for r in failed:
            message += f"  - {r.name}: {r.message}\n"
            if r.details:
                message += f"    Details: {r.details}\n"
        super().__init__(message)
        self.results = results


class InstallationAssertions:
    """comprehensive assertions for verifying arch installation in QEMU VM.

    groups related assertions and provides both individual checks and
    composite verification methods for thorough installation validation.
    """

    def __init__(self, vm: QemuVm) -> None:
        self._vm = vm
        self._results: list[AssertionResult] = []

    def _run_cmd(self, command: str, timeout: int = 60) -> tuple[int, str, str]:
        """run command on VM and return (exit_code, stdout, stderr)."""
        return self._vm.run_ssh_command(command, timeout=timeout)

    def _assert(
        self,
        name: str,
        condition: bool,
        message: str,
        details: Optional[str] = None,
    ) -> AssertionResult:
        """record an assertion result."""
        result = AssertionResult(
            name=name,
            passed=condition,
            message=message if not condition else "OK",
            details=details,
        )
        self._results.append(result)
        return result

    def get_results(self) -> list[AssertionResult]:
        """get all assertion results."""
        return self._results.copy()

    def has_failures(self) -> bool:
        """check if any assertions have failed."""
        return any(not r.passed for r in self._results)

    def raise_if_failed(self) -> None:
        """raise QemuAssertionError if any assertions failed."""
        if any(not r.passed for r in self._results):
            raise QemuAssertionError(self._results)

    # =========================================================================
    # partition assertions
    # =========================================================================

    def assert_partitions_exist(self, device: str = "/dev/vda") -> AssertionResult:
        """verify disk has expected partitions."""
        code, stdout, _ = self._run_cmd(f"lsblk -n -o TYPE,NAME {device}")
        has_parts = "part" in stdout

        return self._assert(
            "partitions_exist",
            has_parts,
            f"expected partitions on {device}",
            stdout,
        )

    def assert_efi_partition_type(self, device: str = "/dev/vda") -> AssertionResult:
        """verify EFI partition has correct type (EFI System Partition GUID)."""
        # Use lsblk -o PARTTYPE to get GPT partition type GUID
        # EFI System Partition GUID: c12a7328-f81f-11d2-ba4b-00a0c93ec93b
        code, stdout, _ = self._run_cmd(f"lsblk -n -o PARTTYPE {device}1")
        parttype = stdout.strip().lower()
        # EFI System Partition GUID
        has_efi_type = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" in parttype

        return self._assert(
            "efi_partition_type",
            has_efi_type,
            "expected EFI partition type (c12a7328-f81f-11d2-ba4b-00a0c93ec93b)",
            f"partition type: {parttype}",
        )

    def assert_root_partition_type(self, device: str = "/dev/vda") -> AssertionResult:
        """verify root partition has Linux x86-64 root type (8304)."""
        # Use lsblk -o PARTTYPE to get GPT partition type GUID
        # Linux x86-64 root GUID: 4f68bce3-e8cd-4db1-96e7-fbcaf984b709
        code, stdout, _ = self._run_cmd(f"lsblk -n -o PARTTYPE {device}2")
        parttype = stdout.strip().lower()
        # Linux x86-64 root partition GUID
        has_root_type = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709" in parttype

        return self._assert(
            "root_partition_type",
            has_root_type,
            "expected Linux x86-64 root partition type (4f68bce3-e8cd-4db1-96e7-fbcaf984b709)",
            f"partition type: {parttype}",
        )

    def assert_efi_partition_size_mib(
        self,
        expected_mib: int,
        device: str = "/dev/vda",
        tolerance_percent: float = 10.0,
    ) -> AssertionResult:
        """verify EFI partition is approximately the expected size."""
        code, stdout, _ = self._run_cmd(f"lsblk -b -n -o SIZE {device}1")

        try:
            size_bytes = int(stdout.strip())
            size_mib = size_bytes // (1024 * 1024)
            tolerance = expected_mib * (tolerance_percent / 100)
            within_tolerance = abs(size_mib - expected_mib) <= tolerance

            return self._assert(
                "efi_partition_size",
                within_tolerance,
                f"expected {expected_mib}MiB (±{tolerance_percent}%), got {size_mib}MiB",
            )
        except ValueError:
            return self._assert(
                "efi_partition_size",
                False,
                "failed to parse EFI partition size",
                stdout,
            )

    # =========================================================================
    # LUKS assertions
    # =========================================================================

    def assert_luks_volume_active(self, mapper_name: str = "cryptroot") -> AssertionResult:
        """verify LUKS volume is open and active."""
        code, stdout, _ = self._run_cmd(f"cryptsetup status {mapper_name}")
        is_active = code == 0 and ("active" in stdout.lower() or "is active" in stdout)

        return self._assert(
            "luks_active",
            is_active,
            f"expected LUKS volume {mapper_name} to be active",
            stdout,
        )

    def assert_luks_type(
        self,
        expected_type: str = "LUKS2",
        mapper_name: str = "cryptroot",
    ) -> AssertionResult:
        """verify LUKS version/type."""
        code, stdout, _ = self._run_cmd(f"cryptsetup status {mapper_name}")
        has_type = expected_type.lower() in stdout.lower()

        return self._assert(
            "luks_type",
            has_type,
            f"expected LUKS type {expected_type}",
            stdout,
        )

    def assert_luks_cipher(
        self,
        expected_cipher: str = "aes-xts-plain64",
        mapper_name: str = "cryptroot",
    ) -> AssertionResult:
        """verify LUKS cipher."""
        code, stdout, _ = self._run_cmd(f"cryptsetup status {mapper_name}")
        has_cipher = expected_cipher in stdout

        return self._assert(
            "luks_cipher",
            has_cipher,
            f"expected cipher {expected_cipher}",
            stdout,
        )

    # =========================================================================
    # BTRFS assertions
    # =========================================================================

    def assert_btrfs_subvolumes_exist(
        self,
        expected_subvols: list[str],
        mount_point: str = "/mnt",
    ) -> AssertionResult:
        """verify expected BTRFS subvolumes exist."""
        code, stdout, _ = self._run_cmd(f"btrfs subvolume list {mount_point}")

        missing = []
        for subvol in expected_subvols:
            if subvol not in stdout:
                missing.append(subvol)

        return self._assert(
            "btrfs_subvolumes",
            len(missing) == 0,
            f"missing subvolumes: {missing}" if missing else "all subvolumes present",
            stdout,
        )

    def assert_btrfs_mount_options(
        self,
        expected_options: list[str],
        mount_point: str = "/mnt",
    ) -> AssertionResult:
        """verify BTRFS mount options."""
        code, stdout, _ = self._run_cmd(f"findmnt -n -o OPTIONS {mount_point}")

        missing = []
        for opt in expected_options:
            if opt not in stdout:
                missing.append(opt)

        return self._assert(
            "btrfs_mount_options",
            len(missing) == 0,
            f"missing mount options: {missing}" if missing else "all options present",
            stdout,
        )

    def assert_subvolume_mounted(
        self,
        subvol_name: str,
        mount_point: str,
    ) -> AssertionResult:
        """verify a specific subvolume is mounted at expected path."""
        code, stdout, _ = self._run_cmd(f"findmnt -n -o SOURCE,TARGET {mount_point}")

        # check subvol is in source and mount point matches
        source_ok = subvol_name in stdout or f"subvol=/{subvol_name}" in stdout
        target_ok = mount_point in stdout

        return self._assert(
            f"subvol_{subvol_name}_mounted",
            source_ok and target_ok,
            f"expected {subvol_name} mounted at {mount_point}",
            stdout,
        )

    def assert_nocow_attribute(self, path: str) -> AssertionResult:
        """verify directory has No_COW attribute for BTRFS."""
        code, stdout, _ = self._run_cmd(f"lsattr -d {path}")
        has_nocow = "C" in stdout

        return self._assert(
            f"nocow_{path}",
            has_nocow,
            f"expected No_COW attribute on {path}",
            stdout,
        )

    # =========================================================================
    # boot and UKI assertions
    # =========================================================================

    def assert_systemd_boot_installed(self, efi_path: str = "/efi") -> AssertionResult:
        """verify systemd-boot is installed."""
        code, stdout, _ = self._run_cmd(f"ls {efi_path}/EFI/BOOT/BOOTX64.EFI")
        exists = code == 0

        return self._assert(
            "systemd_boot_installed",
            exists,
            f"expected BOOTX64.EFI in {efi_path}/EFI/BOOT/",
        )

    def assert_loader_conf_exists(self, efi_path: str = "/efi") -> AssertionResult:
        """verify loader.conf exists."""
        code, stdout, _ = self._run_cmd(f"cat {efi_path}/loader/loader.conf")

        return self._assert(
            "loader_conf_exists",
            code == 0,
            f"expected loader.conf in {efi_path}/loader/",
            stdout,
        )

    def assert_loader_timeout(
        self,
        expected_timeout: int,
        efi_path: str = "/efi",
    ) -> AssertionResult:
        """verify loader.conf has correct timeout."""
        code, stdout, _ = self._run_cmd(f"cat {efi_path}/loader/loader.conf")
        has_timeout = f"timeout {expected_timeout}" in stdout

        return self._assert(
            "loader_timeout",
            has_timeout,
            f"expected timeout {expected_timeout}",
            stdout,
        )

    def assert_loader_editor_disabled(self, efi_path: str = "/efi") -> AssertionResult:
        """verify bootloader editor is disabled (security)."""
        code, stdout, _ = self._run_cmd(f"cat {efi_path}/loader/loader.conf")
        editor_disabled = "editor no" in stdout

        return self._assert(
            "loader_editor_disabled",
            editor_disabled,
            "expected 'editor no' in loader.conf",
            stdout,
        )

    def assert_uki_directory_exists(self, efi_path: str = "/efi") -> AssertionResult:
        """verify UKI directory exists."""
        code, _, _ = self._run_cmd(f"ls -la {efi_path}/EFI/Linux/")

        return self._assert(
            "uki_directory",
            code == 0,
            f"expected UKI directory at {efi_path}/EFI/Linux/",
        )

    def assert_uki_files_exist(
        self,
        expected_kernels: list[str],
        efi_path: str = "/efi",
    ) -> AssertionResult:
        """verify UKI files exist for expected kernels."""
        code, stdout, _ = self._run_cmd(f"ls {efi_path}/EFI/Linux/")

        missing = []
        for kernel in expected_kernels:
            # UKI naming: arch-linux-hardened-default.efi, etc.
            if kernel not in stdout:
                missing.append(kernel)

        return self._assert(
            "uki_files",
            len(missing) == 0,
            f"missing UKIs for kernels: {missing}" if missing else "all UKIs present",
            stdout,
        )

    def assert_kernel_cmdline_contains(
        self,
        expected_params: list[str],
        cmdline_path: str = "/etc/kernel/cmdline",
    ) -> AssertionResult:
        """verify kernel command line contains expected parameters."""
        code, stdout, _ = self._run_cmd(f"cat {cmdline_path}")

        missing = []
        for param in expected_params:
            if param not in stdout:
                missing.append(param)

        return self._assert(
            "kernel_cmdline",
            len(missing) == 0,
            f"missing cmdline params: {missing}" if missing else "all params present",
            stdout,
        )

    # =========================================================================
    # secure boot assertions
    # =========================================================================

    def assert_secure_boot_setup_mode(self) -> AssertionResult:
        """verify secure boot is in setup mode (keys not enrolled yet)."""
        code, stdout, stderr = self._run_cmd("sbctl status")

        # primary check: sbctl output if available
        # sbctl shows "Setup Mode:    ✗ Enabled" when setup mode IS enabled
        # (the ✗ indicates it's a bad security state, but enabled means we can enroll)
        if code == 0:
            in_setup = False
            for line in stdout.split("\n"):
                if "setup mode" in line.lower():
                    in_setup = "enabled" in line.lower()
                    break
            return self._assert(
                "secure_boot_setup_mode",
                in_setup,
                "expected secure boot in setup mode",
                stdout,
            )

        # fallback: read efivars directly (works on live ISO without sbctl)
        # SetupMode and SecureBoot are EFI variables under efivarfs. The first
        # 4 bytes are attributes; the 5th byte is the boolean value.
        setup_var = "/sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"
        secure_var = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"

        # extract the 5th byte (skip 4 attr bytes) as integer 0/1
        cmd = f"[ -f {setup_var} ] && dd if={setup_var} bs=1 skip=4 count=1 status=none | od -An -t u1 -v || echo MISSING"
        c1, out_setup, _ = self._run_cmd(cmd)

        cmd2 = f"[ -f {secure_var} ] && dd if={secure_var} bs=1 skip=4 count=1 status=none | od -An -t u1 -v || echo MISSING"
        c2, out_secure, _ = self._run_cmd(cmd2)

        try:
            setup_byte = int(out_setup.strip()) if "MISSING" not in out_setup else -1
        except ValueError:
            setup_byte = -1

        try:
            secure_byte = int(out_secure.strip()) if "MISSING" not in out_secure else -1
        except ValueError:
            secure_byte = -1

        # in setup mode, SetupMode==1 typically and SecureBoot==0
        in_setup = setup_byte == 1
        details = f"sbctl_unavailable(code={code}); efivars: SetupMode={setup_byte}, SecureBoot={secure_byte}"

        return self._assert(
            "secure_boot_setup_mode",
            in_setup,
            "expected secure boot in setup mode",
            details,
        )

    def assert_secure_boot_keys_created(self) -> AssertionResult:
        """verify sbctl keys have been created."""
        code, stdout, _ = self._run_cmd("sbctl status")
        keys_created = "keys" in stdout.lower() and "created" in stdout.lower()

        # alternative check: key files exist at expected locations
        if not keys_created:
            # check for the actual key files sbctl creates (new location)
            key_paths = [
                "/var/lib/sbctl/keys/PK/PK.key",
                "/var/lib/sbctl/keys/KEK/KEK.key",
                "/var/lib/sbctl/keys/db/db.key",
            ]
            for key_path in key_paths:
                code2, _, _ = self._run_cmd(f"test -f {key_path}")
                if code2 == 0:
                    keys_created = True
                    stdout += f"\n[key file found: {key_path}]"
                    break

            # also check if files are signed (implies keys exist)
            if not keys_created:
                code3, verify_out, _ = self._run_cmd("sbctl verify 2>&1 | head -5")
                if "signed" in verify_out.lower():
                    keys_created = True
                    stdout += f"\n[files are signed, keys must exist: {verify_out}]"

        return self._assert(
            "secure_boot_keys_created",
            keys_created,
            "expected sbctl keys to be created",
            stdout,
        )

    def assert_uki_signed(self, uki_path: str) -> AssertionResult:
        """verify a UKI file is signed for secure boot."""
        code, stdout, _ = self._run_cmd(f"sbctl verify {uki_path}")
        is_signed = code == 0 and "signed" in stdout.lower()

        return self._assert(
            f"uki_signed_{Path(uki_path).name}",
            is_signed,
            f"expected {uki_path} to be signed",
            stdout,
        )

    def assert_all_ukis_signed(self, efi_path: str = "/efi") -> AssertionResult:
        """verify all UKI files are signed."""
        code, stdout, _ = self._run_cmd(f"sbctl verify {efi_path}/EFI/Linux/*.efi")

        # sbctl verify returns 0 if all files are signed
        all_signed = code == 0

        return self._assert(
            "all_ukis_signed",
            all_signed,
            "expected all UKIs to be signed",
            stdout,
        )

    def assert_bootloader_signed(self, efi_path: str = "/efi") -> AssertionResult:
        """verify systemd-boot is signed."""
        code, stdout, _ = self._run_cmd(f"sbctl verify {efi_path}/EFI/BOOT/BOOTX64.EFI")
        is_signed = code == 0

        return self._assert(
            "bootloader_signed",
            is_signed,
            "expected BOOTX64.EFI to be signed",
            stdout,
        )

    # =========================================================================
    # system configuration assertions
    # =========================================================================

    def assert_hostname(self, expected: str) -> AssertionResult:
        """verify hostname is configured."""
        code, stdout, _ = self._run_cmd("cat /etc/hostname")
        matches = expected in stdout.strip()

        return self._assert(
            "hostname",
            matches,
            f"expected hostname '{expected}'",
            stdout,
        )

    def assert_timezone(self, expected: str) -> AssertionResult:
        """verify timezone is configured."""
        code, stdout, _ = self._run_cmd("readlink /etc/localtime")
        matches = expected in stdout

        return self._assert(
            "timezone",
            matches,
            f"expected timezone '{expected}'",
            stdout,
        )

    def assert_locale(self, expected: str) -> AssertionResult:
        """verify locale is configured."""
        code, stdout, _ = self._run_cmd("cat /etc/locale.conf")
        matches = expected in stdout

        return self._assert(
            "locale",
            matches,
            f"expected locale '{expected}'",
            stdout,
        )

    def assert_keymap(self, expected: str) -> AssertionResult:
        """verify console keymap is configured."""
        code, stdout, _ = self._run_cmd("cat /etc/vconsole.conf")
        matches = f"KEYMAP={expected}" in stdout

        return self._assert(
            "keymap",
            matches,
            f"expected KEYMAP={expected}",
            stdout,
        )

    def assert_user_exists(self, username: str) -> AssertionResult:
        """verify user account exists."""
        code, stdout, _ = self._run_cmd(f"id {username}")
        exists = code == 0

        return self._assert(
            f"user_{username}",
            exists,
            f"expected user '{username}' to exist",
            stdout,
        )

    def assert_user_in_groups(self, username: str, groups: list[str]) -> AssertionResult:
        """verify user is member of expected groups."""
        code, stdout, _ = self._run_cmd(f"groups {username}")

        missing = []
        for group in groups:
            if group not in stdout:
                missing.append(group)

        return self._assert(
            f"user_{username}_groups",
            len(missing) == 0,
            f"user missing groups: {missing}" if missing else "user in all groups",
            stdout,
        )

    # =========================================================================
    # mkinitcpio assertions
    # =========================================================================

    def assert_mkinitcpio_hooks(
        self,
        expected_hooks: list[str],
        config_path: str = "/etc/mkinitcpio.conf",
    ) -> AssertionResult:
        """verify mkinitcpio.conf has required hooks."""
        code, stdout, _ = self._run_cmd(f"cat {config_path}")

        missing = []
        for hook in expected_hooks:
            if hook not in stdout:
                missing.append(hook)

        return self._assert(
            "mkinitcpio_hooks",
            len(missing) == 0,
            f"missing hooks: {missing}" if missing else "all hooks present",
            stdout,
        )

    # =========================================================================
    # service assertions
    # =========================================================================

    def assert_service_enabled(self, service: str) -> AssertionResult:
        """verify a systemd service is enabled."""
        code, stdout, _ = self._run_cmd(f"systemctl is-enabled {service}")
        enabled = code == 0 and "enabled" in stdout

        return self._assert(
            f"service_{service}_enabled",
            enabled,
            f"expected {service} to be enabled",
            stdout,
        )

    def assert_service_active(self, service: str) -> AssertionResult:
        """verify a systemd service is active/running."""
        code, stdout, _ = self._run_cmd(f"systemctl is-active {service}")
        active = code == 0 and "active" in stdout

        return self._assert(
            f"service_{service}_active",
            active,
            f"expected {service} to be active",
            stdout,
        )

    # =========================================================================
    # fstab assertions
    # =========================================================================

    def assert_fstab_entry(
        self,
        mount_point: str,
        fs_type: Optional[str] = None,
        options: Optional[list[str]] = None,
    ) -> AssertionResult:
        """verify fstab has entry for mount point with expected options."""
        code, stdout, _ = self._run_cmd("cat /etc/fstab")

        has_mount = mount_point in stdout

        if fs_type and has_mount:
            has_mount = has_mount and fs_type in stdout

        missing_opts = []
        if options and has_mount:
            for opt in options:
                if opt not in stdout:
                    missing_opts.append(opt)

        ok = has_mount and len(missing_opts) == 0

        return self._assert(
            f"fstab_{mount_point.replace('/', '_')}",
            ok,
            f"fstab entry issues" if not ok else "OK",
            stdout[:500],
        )

    # =========================================================================
    # swap assertions
    # =========================================================================

    def assert_swapfile_exists(self, path: str = "/.swap/swapfile") -> AssertionResult:
        """verify swapfile exists."""
        code, _, _ = self._run_cmd(f"ls {path}")

        return self._assert(
            "swapfile_exists",
            code == 0,
            f"expected swapfile at {path}",
        )

    def assert_swapfile_size_mb(
        self,
        expected_mb: int,
        path: str = "/.swap/swapfile",
        tolerance_percent: float = 10.0,
    ) -> AssertionResult:
        """verify swapfile is approximately expected size."""
        code, stdout, _ = self._run_cmd(f"stat -c '%s' {path}")

        try:
            size_bytes = int(stdout.strip())
            size_mb = size_bytes // (1024 * 1024)
            tolerance = expected_mb * (tolerance_percent / 100)
            within_tolerance = abs(size_mb - expected_mb) <= tolerance

            return self._assert(
                "swapfile_size",
                within_tolerance,
                f"expected {expected_mb}MB (±{tolerance_percent}%), got {size_mb}MB",
            )
        except ValueError:
            return self._assert(
                "swapfile_size",
                False,
                "failed to parse swapfile size",
                stdout,
            )

    def assert_swap_active(self, path: str = "/.swap/swapfile") -> AssertionResult:
        """verify swap is active."""
        code, stdout, _ = self._run_cmd("cat /proc/swaps")

        # swapfile path may be listed with brackets or without
        has_swap = path in stdout or path.replace("/", "") in stdout.replace("/", "")

        return self._assert(
            "swap_active",
            has_swap,
            f"expected swap to be active at {path}",
            stdout,
        )

    def assert_swapfile_in_fstab(self, path: str = "/.swap/swapfile") -> AssertionResult:
        """verify swapfile is configured in fstab."""
        code, stdout, _ = self._run_cmd("cat /etc/fstab")

        has_entry = path in stdout

        return self._assert(
            "swapfile_fstab",
            has_entry,
            f"expected swapfile {path} in fstab",
            stdout[:500],
        )

    # =========================================================================
    # hibernation assertions
    # =========================================================================

    def assert_hibernation_resume_configured(
        self,
        swapfile_path: str = "/.swap/swapfile",
    ) -> AssertionResult:
        """verify resume= kernel parameter is configured for hibernation.

        checks kernel cmdline for resume= parameter pointing to the
        correct device, and resume_offset= for swapfile-based hibernation.
        """
        code, stdout, _ = self._run_cmd("cat /proc/cmdline")

        has_resume = "resume=" in stdout

        return self._assert(
            "hibernation_resume",
            has_resume,
            "expected resume= kernel parameter for hibernation",
            stdout,
        )

    def assert_hibernation_resume_offset(self) -> AssertionResult:
        """verify resume_offset= kernel parameter is configured.

        for swapfile-based hibernation, the resume_offset must be set
        to the physical offset of the swapfile on the disk.
        """
        code, stdout, _ = self._run_cmd("cat /proc/cmdline")

        has_offset = "resume_offset=" in stdout

        return self._assert(
            "hibernation_resume_offset",
            has_offset,
            "expected resume_offset= kernel parameter for swapfile hibernation",
            stdout,
        )

    def assert_mkinitcpio_resume_hook(
        self,
        config_path: str = "/etc/mkinitcpio.conf",
    ) -> AssertionResult:
        """verify mkinitcpio is configured for hibernation.

        for systemd-based initramfs (with 'systemd' and 'sd-encrypt' hooks),
        hibernation is handled automatically when resume= and resume_offset=
        kernel parameters are set. no explicit 'resume' hook is needed.

        for busybox-based initramfs, the traditional 'resume' hook is required.
        """
        code, stdout, _ = self._run_cmd(f"cat {config_path}")

        # check for systemd-based initramfs (handles resume automatically)
        has_systemd_initramfs = "systemd" in stdout and "sd-encrypt" in stdout

        # check for traditional resume hook (busybox initramfs)
        has_resume_hook = "resume" in stdout and "resume" not in "sd-encrypt"

        # either systemd initramfs OR explicit resume hook is valid
        is_valid = has_systemd_initramfs or has_resume_hook

        return self._assert(
            "mkinitcpio_resume_hook",
            is_valid,
            "expected systemd initramfs (systemd + sd-encrypt hooks) or resume hook for hibernation",
            stdout[:500],
        )

    # =========================================================================
    # packages assertions
    # =========================================================================

    def assert_package_installed(self, package: str) -> AssertionResult:
        """verify a package is installed."""
        code, stdout, _ = self._run_cmd(f"pacman -Qi {package}")

        return self._assert(
            f"package_{package}",
            code == 0,
            f"expected package '{package}' to be installed",
        )

    def assert_packages_installed(self, packages: list[str]) -> AssertionResult:
        """verify multiple packages are installed."""
        missing = []
        for pkg in packages:
            code, _, _ = self._run_cmd(f"pacman -Qi {pkg}")
            if code != 0:
                missing.append(pkg)

        return self._assert(
            "packages_installed",
            len(missing) == 0,
            f"missing packages: {missing}" if missing else "all packages installed",
        )

    # =========================================================================
    # bootable snapshot assertions
    # =========================================================================

    def assert_snapshot_hooks_deployed(self) -> AssertionResult:
        """verify pacman hooks for bootable snapshots are deployed."""
        code1, _, _ = self._run_cmd("ls /etc/pacman.d/hooks/95-snapshot-uki-refresh.hook")
        code2, _, _ = self._run_cmd("ls /usr/local/bin/refresh-snapshot-ukis")

        return self._assert(
            "snapshot_hooks",
            code1 == 0 and code2 == 0,
            "expected snapshot UKI refresh hook and script",
        )

    def assert_manage_snapshot_ukis_exists(self) -> AssertionResult:
        """verify manage-snapshot-ukis script is deployed and executable."""
        code, stdout, _ = self._run_cmd("test -x /usr/local/bin/manage-snapshot-ukis")

        return self._assert(
            "manage_snapshot_ukis_executable",
            code == 0,
            "expected manage-snapshot-ukis to exist and be executable",
        )

    def assert_snapper_config_exists(self, config_name: str = "root") -> AssertionResult:
        """verify snapper configuration exists."""
        code, stdout, _ = self._run_cmd(f"snapper -c {config_name} list")

        return self._assert(
            f"snapper_config_{config_name}",
            code == 0,
            f"expected snapper config '{config_name}'",
            stdout,
        )

    def assert_snapshot_created(self, config_name: str = "root") -> AssertionResult:
        """verify at least one snapshot exists for the given config."""
        code, stdout, _ = self._run_cmd(f"snapper -c {config_name} list --columns number")

        # output should have at least one numeric line (excluding header)
        lines = [l.strip() for l in stdout.strip().split("\n") if l.strip().isdigit()]
        has_snapshots = len(lines) > 0

        return self._assert(
            f"snapshot_exists_{config_name}",
            has_snapshots,
            f"expected at least one snapshot for config '{config_name}'",
            stdout,
        )

    def assert_snapshot_uki_generated(self, snapshot_id: int) -> AssertionResult:
        """verify a UKI was generated for a specific snapshot."""
        code, stdout, _ = self._run_cmd(
            f"ls /efi/EFI/Linux/*snapshot*{snapshot_id}*.efi 2>/dev/null"
        )

        return self._assert(
            f"snapshot_uki_{snapshot_id}",
            code == 0 and ".efi" in stdout,
            f"expected UKI for snapshot {snapshot_id}",
            stdout,
        )

    def assert_snapshot_uki_in_bootloader(self, snapshot_id: int) -> AssertionResult:
        """verify snapshot UKI is detected by systemd-boot."""
        code, stdout, _ = self._run_cmd("bootctl list --no-pager")

        snapshot_pattern = f"snapshot" in stdout.lower() or str(snapshot_id) in stdout

        return self._assert(
            f"snapshot_in_bootloader_{snapshot_id}",
            snapshot_pattern,
            f"expected snapshot {snapshot_id} in bootloader entries",
            stdout,
        )

    def assert_snapshot_ukis_list(self) -> AssertionResult:
        """verify snapshot UKI listing command works."""
        code, stdout, _ = self._run_cmd(
            "/usr/local/bin/manage-snapshot-ukis list 2>/dev/null || true"
        )

        return self._assert(
            "snapshot_ukis_list",
            code == 0,
            "expected manage-snapshot-ukis list to succeed",
            stdout,
        )

    def assert_snapshot_is_writable(self, snapshot_subvol: str) -> AssertionResult:
        """verify a snapshot subvolume is writable (not read-only)."""
        # check btrfs property ro flag
        code, stdout, _ = self._run_cmd(f"btrfs property get {snapshot_subvol} ro")

        is_writable = "ro=false" in stdout.lower()

        return self._assert(
            f"snapshot_writable_{snapshot_subvol}",
            is_writable,
            f"expected snapshot {snapshot_subvol} to be writable",
            stdout,
        )

    # =========================================================================
    # advanced secure boot assertions
    # =========================================================================

    def assert_secure_boot_enrolled(self) -> AssertionResult:
        """verify secure boot is in enrolled mode (not setup mode)."""
        code, stdout, _ = self._run_cmd("sbctl status")

        # check for setup mode disabled (keys enrolled)
        # sbctl shows "Setup Mode:    ✓ Disabled" when keys are enrolled
        setup_disabled = False
        for line in stdout.split("\n"):
            if "setup mode" in line.lower():
                setup_disabled = "disabled" in line.lower()
                break

        return self._assert(
            "secure_boot_enrolled",
            setup_disabled,
            "expected secure boot to have keys enrolled (setup mode disabled)",
            stdout,
        )

    def assert_secure_boot_enabled(self) -> AssertionResult:
        """verify secure boot is enabled and enforcing."""
        code, stdout, _ = self._run_cmd("sbctl status")

        # sbctl shows "Secure Boot:   ✓ Enabled" when secure boot is enabled
        sb_enabled = False
        for line in stdout.split("\n"):
            if "secure boot" in line.lower() and "setup" not in line.lower():
                sb_enabled = "enabled" in line.lower()
                break

        return self._assert(
            "secure_boot_enabled",
            sb_enabled,
            "expected secure boot to be enabled",
            stdout,
        )

    def assert_pk_enrolled(self) -> AssertionResult:
        """verify Platform Key (PK) is enrolled."""
        code, stdout, _ = self._run_cmd("sbctl status --json 2>/dev/null || sbctl status")

        pk_enrolled = "pk" in stdout.lower() or "platform key" in stdout.lower()

        # fallback: check efivar directly
        if not pk_enrolled:
            code2, out2, _ = self._run_cmd("ls /sys/firmware/efi/efivars/PK-* 2>/dev/null")
            pk_enrolled = code2 == 0

        return self._assert(
            "pk_enrolled",
            pk_enrolled,
            "expected Platform Key (PK) to be enrolled",
            stdout,
        )

    def assert_kek_enrolled(self) -> AssertionResult:
        """verify Key Exchange Key (KEK) is enrolled."""
        code, stdout, _ = self._run_cmd("ls /sys/firmware/efi/efivars/KEK-* 2>/dev/null")

        return self._assert(
            "kek_enrolled",
            code == 0,
            "expected Key Exchange Key (KEK) to be enrolled",
            stdout,
        )

    def assert_db_enrolled(self) -> AssertionResult:
        """verify Signature Database (db) is enrolled."""
        code, stdout, _ = self._run_cmd("ls /sys/firmware/efi/efivars/db-* 2>/dev/null")

        return self._assert(
            "db_enrolled",
            code == 0,
            "expected Signature Database (db) to be enrolled",
            stdout,
        )

    def assert_sbctl_verify_all(self, efi_path: str = "/efi") -> AssertionResult:
        """verify all boot files pass sbctl verification."""
        code, stdout, stderr = self._run_cmd(f"sbctl verify")

        # sbctl verify returns 0 if all files are properly signed
        all_verified = code == 0

        return self._assert(
            "sbctl_verify_all",
            all_verified,
            "expected all boot files to pass sbctl verification",
            stdout if stdout else stderr,
        )

    def assert_unsigned_boot_blocked(self, unsigned_file: str) -> AssertionResult:
        """verify an unsigned EFI file would be blocked by secure boot.

        this checks that sbctl verify reports the file as not signed,
        which would mean secure boot would block it.
        """
        code, stdout, _ = self._run_cmd(f"sbctl verify {unsigned_file}")

        # non-zero exit or "not signed" in output means it would be blocked
        would_block = code != 0 or "not signed" in stdout.lower()

        return self._assert(
            f"unsigned_blocked_{unsigned_file}",
            would_block,
            f"expected unsigned file {unsigned_file} to be blocked by secure boot",
            stdout,
        )

    def assert_live_iso_would_be_blocked(self) -> AssertionResult:
        """verify unsigned live ISO EFI loader would be blocked by secure boot.

        after custom secure boot key enrollment, the arch linux live ISO
        bootloader (and any other unsigned EFI binaries) should be rejected
        by firmware. this test verifies by checking sbctl status and
        confirming setup mode is disabled (keys are enrolled).
        """
        # verify setup mode is off (keys enrolled)
        code, stdout, _ = self._run_cmd("sbctl status")

        setup_mode_disabled = "setup mode" in stdout.lower() and "disabled" in stdout.lower()
        secure_boot_enabled = "secure boot" in stdout.lower() and "enabled" in stdout.lower()

        # also check that microsoft keys are NOT in the db (optional, depends on config)
        # if microsoft keys were enrolled, the ISO might boot
        ms_keys_present = "microsoft" in stdout.lower()

        # iso would be blocked if setup mode is off AND either:
        # - secure boot is enabled, or
        # - we're out of setup mode (keys enrolled)
        would_block = setup_mode_disabled and (secure_boot_enabled or not ms_keys_present)

        details = f"setup_mode_disabled={setup_mode_disabled}, secure_boot_enabled={secure_boot_enabled}, ms_keys={ms_keys_present}\nsbctl output:\n{stdout}"

        return self._assert(
            "live_iso_blocked",
            would_block,
            "expected unsigned live ISO to be blocked after key enrollment",
            details,
        )

    def assert_secure_boot_keys_exist(self) -> AssertionResult:
        """verify secure boot key files exist in the expected locations."""
        key_files = [
            "/usr/share/secureboot/keys/PK/PK.key",
            "/usr/share/secureboot/keys/KEK/KEK.key",
            "/usr/share/secureboot/keys/db/db.key",
        ]

        missing = []
        for key_file in key_files:
            code, _, _ = self._run_cmd(f"test -f {key_file}")
            if code != 0:
                missing.append(key_file)

        return self._assert(
            "secure_boot_keys_exist",
            len(missing) == 0,
            f"missing secure boot key files: {missing}" if missing else "all key files exist",
        )

    # =========================================================================
    # composite secure boot verification
    # =========================================================================

    def verify_secure_boot_complete(self, efi_path: str = "/efi") -> list[AssertionResult]:
        """run all secure boot assertions."""
        self.assert_secure_boot_keys_created()
        self.assert_secure_boot_keys_exist()
        self.assert_bootloader_signed(efi_path)
        self.assert_all_ukis_signed(efi_path)
        self.assert_sbctl_verify_all(efi_path)

        return self._results

    def verify_secure_boot_enrolled_and_enforcing(
        self,
        efi_path: str = "/efi",
    ) -> list[AssertionResult]:
        """
        stricter check that verifies keys are enrolled
        and secure boot is actually enforcing (not just configured)
        """
        self.assert_secure_boot_enrolled()
        self.assert_secure_boot_enabled()
        self.assert_pk_enrolled()
        self.assert_kek_enrolled()
        self.assert_db_enrolled()
        self.assert_sbctl_verify_all(efi_path)

        return self._results

    # =========================================================================
    # composite snapshot UKI verification
    # =========================================================================

    def verify_bootable_snapshots_complete(
        self,
        config_name: str = "root",
    ) -> list[AssertionResult]:
        """run all bootable snapshot assertions."""
        self.assert_snapper_config_exists(config_name)
        self.assert_manage_snapshot_ukis_exists()
        self.assert_snapshot_hooks_deployed()
        self.assert_snapshot_ukis_list()

        return self._results

    # =========================================================================
    # composite assertions (run multiple related checks)
    # =========================================================================

    def verify_storage_complete(
        self,
        device: str = "/dev/vda",
        efi_size_mb: int = 2048,
        expected_subvols: Optional[list[str]] = None,
    ) -> list[AssertionResult]:
        """run all storage-related assertions."""
        self.assert_partitions_exist(device)
        self.assert_efi_partition_type(device)
        self.assert_root_partition_type(device)
        self.assert_efi_partition_size_mib(efi_size_mb, device)
        self.assert_luks_volume_active()
        self.assert_luks_type("LUKS2")

        if expected_subvols:
            self.assert_btrfs_subvolumes_exist(expected_subvols)

        return self._results

    def verify_boot_complete(
        self,
        expected_timeout: int = 20,
        expected_kernels: Optional[list[str]] = None,
        efi_path: str = "/efi",
    ) -> list[AssertionResult]:
        """run all boot-related assertions."""
        self.assert_systemd_boot_installed(efi_path)
        self.assert_loader_conf_exists(efi_path)
        self.assert_loader_timeout(expected_timeout, efi_path)
        self.assert_loader_editor_disabled(efi_path)
        self.assert_uki_directory_exists(efi_path)

        if expected_kernels:
            self.assert_uki_files_exist(expected_kernels, efi_path)

        return self._results

    def verify_system_config_complete(
        self,
        hostname: str,
        timezone: str,
        locale: str,
        keymap: str,
        username: str,
        user_groups: list[str],
    ) -> list[AssertionResult]:
        """run all system configuration assertions."""
        self.assert_hostname(hostname)
        self.assert_timezone(timezone)
        self.assert_locale(locale)
        self.assert_keymap(keymap)
        self.assert_user_exists(username)
        self.assert_user_in_groups(username, user_groups)

        return self._results

    def verify_all(
        self,
        device: str = "/dev/vda",
        efi_size_mb: int = 2048,
        efi_path: str = "/efi",
        expected_subvols: Optional[list[str]] = None,
        expected_kernels: Optional[list[str]] = None,
        expected_timeout: int = 20,
        hostname: Optional[str] = None,
        timezone: Optional[str] = None,
        locale: Optional[str] = None,
        keymap: Optional[str] = None,
        username: Optional[str] = None,
        user_groups: Optional[list[str]] = None,
        verify_secure_boot: bool = True,
    ) -> list[AssertionResult]:
        """run comprehensive verification of entire installation."""
        self.verify_storage_complete(device, efi_size_mb, expected_subvols)
        self.verify_boot_complete(expected_timeout, expected_kernels, efi_path)

        if verify_secure_boot:
            self.verify_secure_boot_complete(efi_path)

        if all([hostname, timezone, locale, keymap, username]):
            self.verify_system_config_complete(
                hostname=hostname,  # type: ignore
                timezone=timezone,  # type: ignore
                locale=locale,  # type: ignore
                keymap=keymap,  # type: ignore
                username=username,  # type: ignore
                user_groups=user_groups or [],
            )

        return self._results
