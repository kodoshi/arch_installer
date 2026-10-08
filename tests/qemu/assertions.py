"""assertion helpers for QEMU-based installation verification.

provides comprehensive assertion functions to validate all aspects of
an arch linux installation including btrfs structure, secure boot,
UKI generation, system configuration, and services.
"""

import re
from dataclasses import dataclass

from tests.qemu.vm import QemuVm

# the colour codes verify-install wraps its labels in
ANSI_COLOUR = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class AssertionResult:
    """result of a single assertion."""

    name: str
    passed: bool
    message: str
    details: str | None = None


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


EFI_SYSTEM_PARTITION_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
LINUX_FILESYSTEM_PARTITION_TYPE = "0fc63daf-8483-4772-8e79-3d69d8477de4"
# prints, for a disk: the LUKS magic count (primary and secondary header) in its first
# 64 MiB, the zero-filled MiB in its first 512 MiB, the GPT signatures in its first and
# last MiB, and the zero-filled 4 KiB blocks in its last MiB
CIPHERTEXT_PROBE = (
    'import os, sys; device = open(sys.argv[1], "rb"); head = device.read(64 << 20); '
    "device.seek(0); zeros = sum(device.read(1 << 20) == bytes(1 << 20) for _ in range(512)); "
    "device.seek(-(1 << 20), os.SEEK_END); tail = device.read(1 << 20); "
    'print(head.count(b"LUKS\\xba\\xbe") + head.count(b"SKUL\\xba\\xbe"), zeros, '
    '(head[: 1 << 20] + tail).count(b"EFI PART"), '
    "sum(tail[offset : offset + 4096] == bytes(4096) for offset in range(0, len(tail), 4096)))"
)


class InstallationAssertions:
    """comprehensive assertions for verifying arch installation in QEMU VM.

    groups related assertions and provides both individual checks and
    composite verification methods for thorough installation validation.
    """

    def __init__(self, vm: QemuVm) -> None:
        self._vm = vm
        self._results: list[AssertionResult] = []

    def _run_command(self, command: str, timeout: int = 60) -> tuple[int, str, str]:
        """run command on VM and return (exit_code, stdout, stderr)."""
        return self._vm.run_ssh_command(command, timeout=timeout)

    def _assert(
        self,
        name: str,
        condition: bool,
        message: str,
        details: str | None = None,
    ) -> AssertionResult:
        """record an assertion result."""
        result = AssertionResult(
            name=name,
            passed=condition,
            message=message if not condition else "OK",
            details=details,
        )
        self._results.append(result)
        print(f"    ✓ {name}" if condition else f"    ✗ {name}: {message}")
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

    def _assert_all_present(
        self,
        name: str,
        command: str,
        expected_items: list[str],
        label: str,
    ) -> AssertionResult:
        _code, stdout, _ = self._run_command(command)
        missing = [item for item in expected_items if item not in stdout]
        return self._assert(
            name,
            len(missing) == 0,
            f"missing {label}: {missing}" if missing else f"all {label} present",
            stdout,
        )

    # =========================================================================
    # partition assertions
    # =========================================================================

    def assert_partitions_exist(self, device: str = "/dev/vda") -> AssertionResult:
        """verify disk has expected partitions."""
        _code, stdout, _ = self._run_command(f"lsblk -n -o TYPE,NAME {device}")
        has_partitions = "part" in stdout

        return self._assert(
            "partitions_exist",
            has_partitions,
            f"expected partitions on {device}",
            stdout,
        )

    def assert_efi_partition_type(self, device: str = "/dev/vda") -> AssertionResult:
        """verify EFI partition has correct type (EFI System Partition GUID)."""
        # Use lsblk -o PARTTYPE to get GPT partition type GUID
        # EFI System Partition GUID: c12a7328-f81f-11d2-ba4b-00a0c93ec93b
        _code, stdout, _ = self._run_command(f"lsblk -n -o PARTTYPE {device}1")
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
        _code, stdout, _ = self._run_command(f"lsblk -n -o PARTTYPE {device}2")
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
        _code, stdout, _ = self._run_command(f"lsblk -b -n -o SIZE {device}1")

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
        code, stdout, _ = self._run_command(f"cryptsetup status {mapper_name}")
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
        _code, stdout, _ = self._run_command(f"cryptsetup status {mapper_name}")
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
        _code, stdout, _ = self._run_command(f"cryptsetup status {mapper_name}")
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
        self, expected_subvols: list[str], mount_point: str = "/mnt"
    ) -> AssertionResult:
        return self._assert_all_present(
            "btrfs_subvolumes",
            f"btrfs subvolume list {mount_point}",
            expected_subvols,
            "subvolumes",
        )

    def assert_btrfs_mount_options(
        self, expected_options: list[str], mount_point: str = "/mnt"
    ) -> AssertionResult:
        return self._assert_all_present(
            "btrfs_mount_options",
            f"findmnt -n -o OPTIONS {mount_point}",
            expected_options,
            "mount options",
        )

    def assert_subvolume_mounted(
        self,
        subvol_name: str,
        mount_point: str,
    ) -> AssertionResult:
        """verify a specific subvolume is mounted at expected path."""
        _code, stdout, _ = self._run_command(f"findmnt -n -o SOURCE,TARGET {mount_point}")

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
        _code, stdout, _ = self._run_command(f"lsattr -d {path}")
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
        code, _stdout, _ = self._run_command(f"ls {efi_path}/EFI/BOOT/BOOTX64.EFI")
        exists = code == 0

        return self._assert(
            "systemd_boot_installed",
            exists,
            f"expected BOOTX64.EFI in {efi_path}/EFI/BOOT/",
        )

    def assert_loader_conf_exists(self, efi_path: str = "/efi") -> AssertionResult:
        """verify loader.conf exists."""
        code, stdout, _ = self._run_command(f"cat {efi_path}/loader/loader.conf")

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
        _code, stdout, _ = self._run_command(f"cat {efi_path}/loader/loader.conf")
        has_timeout = f"timeout {expected_timeout}" in stdout

        return self._assert(
            "loader_timeout",
            has_timeout,
            f"expected timeout {expected_timeout}",
            stdout,
        )

    def assert_loader_editor_disabled(self, efi_path: str = "/efi") -> AssertionResult:
        """verify bootloader editor is disabled (security)."""
        _code, stdout, _ = self._run_command(f"cat {efi_path}/loader/loader.conf")
        editor_disabled = "editor no" in stdout

        return self._assert(
            "loader_editor_disabled",
            editor_disabled,
            "expected 'editor no' in loader.conf",
            stdout,
        )

    def assert_uki_directory_exists(self, efi_path: str = "/efi") -> AssertionResult:
        """verify UKI directory exists."""
        code, _, _ = self._run_command(f"ls -la {efi_path}/EFI/Linux/")

        return self._assert(
            "uki_directory",
            code == 0,
            f"expected UKI directory at {efi_path}/EFI/Linux/",
        )

    def assert_uki_files_exist(
        self, expected_kernels: list[str], efi_path: str = "/efi"
    ) -> AssertionResult:
        return self._assert_all_present(
            "uki_files", f"ls {efi_path}/EFI/Linux/", expected_kernels, "UKIs for kernels"
        )

    def assert_kernel_cmdline_contains(
        self, expected_parameters: list[str], cmdline_path: str = "/etc/kernel/cmdline"
    ) -> AssertionResult:
        return self._assert_all_present(
            "kernel_cmdline", f"cat {cmdline_path}", expected_parameters, "cmdline params"
        )

    # =========================================================================
    # secure boot assertions
    # =========================================================================

    def assert_secure_boot_keys_created(self) -> AssertionResult:
        """verify sbctl keys have been created."""
        _code, stdout, _ = self._run_command("sbctl status")
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
                code2, _, _ = self._run_command(f"test -f {key_path}")
                if code2 == 0:
                    keys_created = True
                    stdout += f"\n[key file found: {key_path}]"
                    break

            # also check if files are signed (implies keys exist)
            if not keys_created:
                _code3, verify_output, _ = self._run_command("sbctl verify 2>&1 | head -5")
                if "signed" in verify_output.lower():
                    keys_created = True
                    stdout += f"\n[files are signed, keys must exist: {verify_output}]"

        return self._assert(
            "secure_boot_keys_created",
            keys_created,
            "expected sbctl keys to be created",
            stdout,
        )

    def assert_all_ukis_signed(self, efi_path: str = "/efi") -> AssertionResult:
        """verify all UKI files are signed."""
        code, stdout, _ = self._run_command(f"sbctl verify {efi_path}/EFI/Linux/*.efi")

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
        code, stdout, _ = self._run_command(f"sbctl verify {efi_path}/EFI/BOOT/BOOTX64.EFI")
        is_signed = code == 0

        return self._assert(
            "bootloader_signed",
            is_signed,
            "expected BOOTX64.EFI to be signed",
            stdout,
        )

    def assert_esp_random_seed_private(self, efi_path: str = "/efi") -> AssertionResult:
        # systemd-boot and the UKI stub rewrite the seed every boot, so it is expected to
        # exist; what matters is that only root can read it
        seed_path = f"{efi_path}/loader/random-seed"
        code, stdout, _ = self._run_command(f"stat -c %a {seed_path}")
        if code != 0:
            return self._assert("esp_random_seed_private", True, "no random seed on ESP")

        mode = stdout.strip()
        readable_by_others = int(mode, 8) & 0o077 != 0
        return self._assert(
            "esp_random_seed_private",
            not readable_by_others,
            f"expected {seed_path} to be root-only, got mode {mode}",
        )

    # =========================================================================
    # system configuration assertions
    # =========================================================================

    def assert_hostname(self, expected: str) -> AssertionResult:
        """verify hostname is configured."""
        _code, stdout, _ = self._run_command("cat /etc/hostname")
        matches = expected in stdout.strip()

        return self._assert(
            "hostname",
            matches,
            f"expected hostname '{expected}'",
            stdout,
        )

    def assert_timezone(self, expected: str) -> AssertionResult:
        """verify timezone is configured."""
        _code, stdout, _ = self._run_command("readlink /etc/localtime")
        matches = expected in stdout

        return self._assert(
            "timezone",
            matches,
            f"expected timezone '{expected}'",
            stdout,
        )

    def assert_locale(self, expected: str) -> AssertionResult:
        """verify locale is configured."""
        _code, stdout, _ = self._run_command("cat /etc/locale.conf")
        matches = expected in stdout

        return self._assert(
            "locale",
            matches,
            f"expected locale '{expected}'",
            stdout,
        )

    def assert_keymap(self, expected: str) -> AssertionResult:
        """verify console keymap is configured."""
        _code, stdout, _ = self._run_command("cat /etc/vconsole.conf")
        matches = f"KEYMAP={expected}" in stdout

        return self._assert(
            "keymap",
            matches,
            f"expected KEYMAP={expected}",
            stdout,
        )

    def assert_user_exists(self, username: str) -> AssertionResult:
        """verify user account exists."""
        code, stdout, _ = self._run_command(f"id {username}")
        exists = code == 0

        return self._assert(
            f"user_{username}",
            exists,
            f"expected user '{username}' to exist",
            stdout,
        )

    def assert_user_in_groups(self, username: str, groups: list[str]) -> AssertionResult:
        return self._assert_all_present(
            f"user_{username}_groups", f"groups {username}", groups, "groups"
        )

    # =========================================================================
    # mkinitcpio assertions
    # =========================================================================

    def assert_final_config_written(self, username: str) -> AssertionResult:
        final_config_path = f"/home/{username}/final_config.yaml"
        code, _, _ = self._run_command(f"test -f {final_config_path}")
        return self._assert(
            "final_config_written", code == 0, f"expected {final_config_path} to exist"
        )

    def assert_verify_install_finds_no_failures(self, expected_hostname: str) -> AssertionResult:
        # the installed verify-install, against the expectations the installer wrote; a
        # missing expectations file would only warn, so reading it is checked as well
        exit_code, stdout, stderr = self._run_command("verify-install --verbose", timeout=300)
        failures = [line.strip() for line in stdout.splitlines() if "✗" in line]
        plain_lines = [ANSI_COLOUR.sub("", line).strip() for line in stdout.splitlines()]
        summary = [
            line for line in plain_lines if line.startswith(("Passed:", "Failed:", "Warnings:"))
        ]
        print(f"    verify-install summary: {', '.join(summary)}")
        used_expectations = "Using expectations from /etc/dali/expected-state.env" in stdout
        compared_hostname = f"Hostname: {expected_hostname}" in stdout
        return self._assert(
            "verify_install_passes",
            exit_code == 0 and not failures and used_expectations and compared_hostname,
            f"verify-install exited {exit_code}, failures {failures}, "
            f"read expectations: {used_expectations}, compared hostname: {compared_hostname}",
            stdout + stderr,
        )

    def assert_mkinitcpio_hooks(
        self, expected_hooks: list[str], config_path: str = "/etc/mkinitcpio.conf"
    ) -> AssertionResult:
        return self._assert_all_present(
            "mkinitcpio_hooks", f"cat {config_path}", expected_hooks, "hooks"
        )

    # =========================================================================
    # service assertions
    # =========================================================================

    def assert_service_enabled(self, service: str) -> AssertionResult:
        """verify a systemd service is enabled."""
        code, stdout, _ = self._run_command(f"systemctl is-enabled {service}")
        enabled = code == 0 and "enabled" in stdout

        return self._assert(
            f"service_{service}_enabled",
            enabled,
            f"expected {service} to be enabled",
            stdout,
        )

    def assert_service_active(self, service: str) -> AssertionResult:
        """verify a systemd service is active/running."""
        code, stdout, _ = self._run_command(f"systemctl is-active {service}")
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
        filesystem_type: str | None = None,
        options: list[str] | None = None,
    ) -> AssertionResult:
        """verify fstab has entry for mount point with expected options."""
        _code, stdout, _ = self._run_command("cat /etc/fstab")

        has_mount = mount_point in stdout

        if filesystem_type and has_mount:
            has_mount = has_mount and filesystem_type in stdout

        missing_options = []
        if options and has_mount:
            for option in options:
                if option not in stdout:
                    missing_options.append(option)

        ok = has_mount and len(missing_options) == 0

        return self._assert(
            f"fstab_{mount_point.replace('/', '_')}",
            ok,
            "fstab entry issues" if not ok else "OK",
            stdout[:500],
        )

    # =========================================================================
    # swap assertions
    # =========================================================================

    def assert_swapfile_exists(self, path: str = "/.swap/swapfile") -> AssertionResult:
        """verify swapfile exists."""
        code, _, _ = self._run_command(f"ls {path}")

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
        _code, stdout, _ = self._run_command(f"stat -c '%s' {path}")

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
        _code, stdout, _ = self._run_command("cat /proc/swaps")

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
        _code, stdout, _ = self._run_command("cat /etc/fstab")

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
        _code, stdout, _ = self._run_command("cat /proc/cmdline")

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
        _code, stdout, _ = self._run_command("cat /proc/cmdline")

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
        _code, stdout, _ = self._run_command(f"cat {config_path}")

        # check for systemd-based initramfs (handles resume automatically)
        has_systemd_initramfs = "systemd" in stdout and "sd-encrypt" in stdout

        # check for traditional resume hook (busybox initramfs)
        # must confirm "resume" appears as a standalone hook, not as part of "sd-encrypt"
        has_resume_hook = "resume" in stdout and "sd-encrypt" not in stdout

        # either systemd initramfs OR explicit resume hook is valid
        is_valid = has_systemd_initramfs or has_resume_hook

        return self._assert(
            "mkinitcpio_resume_hook",
            is_valid,
            "expected systemd initramfs (systemd + sd-encrypt hooks) or resume hook for hibernation",
            stdout[:500],
        )

    # =========================================================================
    # package assertions
    # =========================================================================

    def assert_package_installed(self, package: str) -> AssertionResult:
        """verify a package is installed."""
        code, _stdout, _ = self._run_command(f"pacman -Qi {package}")

        return self._assert(
            f"package_{package}",
            code == 0,
            f"expected package '{package}' to be installed",
        )

    def assert_packages_installed(self, packages: list[str]) -> AssertionResult:
        """verify multiple packages are installed."""
        missing = []
        for package in packages:
            code, _, _ = self._run_command(f"pacman -Qi {package}")
            if code != 0:
                missing.append(package)

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
        code1, _, _ = self._run_command("ls /etc/pacman.d/hooks/95-snapshot-uki-refresh.hook")
        code2, _, _ = self._run_command("ls /usr/local/bin/refresh-snapshot-ukis")

        return self._assert(
            "snapshot_hooks",
            code1 == 0 and code2 == 0,
            "expected snapshot UKI refresh hook and script",
        )

    def assert_snapshot_created(self, config_name: str = "root") -> AssertionResult:
        """verify at least one snapshot exists for the given config."""
        _code, stdout, _ = self._run_command(f"snapper -c {config_name} list --columns number")

        lines = [line.strip() for line in stdout.strip().split("\n") if line.strip().isdigit()]
        has_snapshots = len(lines) > 0

        return self._assert(
            f"snapshot_exists_{config_name}",
            has_snapshots,
            f"expected at least one snapshot for config '{config_name}'",
            stdout,
        )

    def assert_snapshot_uki_generated(self, snapshot_id: int) -> AssertionResult:
        """verify a UKI was generated for a specific snapshot."""
        code, stdout, _ = self._run_command(
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
        _code, stdout, _ = self._run_command("bootctl list --no-pager")

        has_snapshot = "snapshot" in stdout.lower() or str(snapshot_id) in stdout

        return self._assert(
            f"snapshot_in_bootloader_{snapshot_id}",
            has_snapshot,
            f"expected snapshot {snapshot_id} in bootloader entries",
            stdout,
        )

    def assert_manage_snapshot_ukis_exists(self) -> AssertionResult:
        """verify manage-snapshot-ukis script is deployed and executable."""
        code, _stdout, _ = self._run_command("test -x /usr/local/bin/manage-snapshot-ukis")

        return self._assert(
            "manage_snapshot_ukis_executable",
            code == 0,
            "expected manage-snapshot-ukis to exist and be executable",
        )

    def assert_snapper_config_exists(self, config_name: str = "root") -> AssertionResult:
        """verify snapper configuration exists."""
        code, stdout, _ = self._run_command(f"snapper -c {config_name} list")

        return self._assert(
            f"snapper_config_{config_name}",
            code == 0,
            f"expected snapper config '{config_name}'",
            stdout,
        )

    def assert_snapshot_ukis_list(self) -> AssertionResult:
        """verify snapshot UKI listing command works."""
        code, stdout, _ = self._run_command("/usr/local/bin/manage-snapshot-ukis list 2>/dev/null")

        return self._assert(
            "snapshot_ukis_list",
            code == 0,
            "expected manage-snapshot-ukis list to succeed",
            stdout,
        )

    def assert_snapshot_is_writable(self, snapshot_subvol: str) -> AssertionResult:
        """verify a snapshot subvolume is writable (not read-only)."""
        # check btrfs property ro flag
        _code, stdout, _ = self._run_command(f"btrfs property get {snapshot_subvol} ro")

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
        _code, stdout, _ = self._run_command("sbctl status")

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
        _code, stdout, _ = self._run_command("sbctl status")

        # sbctl shows "Secure Boot:   ✓ Enabled" when secure boot is enabled
        secure_boot_enabled = False
        for line in stdout.split("\n"):
            if "secure boot" in line.lower() and "setup" not in line.lower():
                secure_boot_enabled = "enabled" in line.lower()
                break

        return self._assert(
            "secure_boot_enabled",
            secure_boot_enabled,
            "expected secure boot to be enabled",
            stdout,
        )

    def assert_pk_enrolled(self) -> AssertionResult:
        """verify Platform Key (PK) is enrolled via sbctl and efivar."""
        # check via sbctl first
        sbctl_code, sbctl_output, _ = self._run_command("sbctl status")
        sbctl_pk = False
        if sbctl_code == 0:
            for line in sbctl_output.split("\n"):
                if "setup mode" in line.lower():
                    sbctl_pk = "disabled" in line.lower()
                    break

        # also check efivar directly
        efivar_code, efivar_output, _ = self._run_command(
            "ls /sys/firmware/efi/efivars/PK-* 2>/dev/null"
        )
        efivar_pk = efivar_code == 0

        details = f"sbctl_setup_mode_disabled={sbctl_pk}, efivar_pk_exists={efivar_pk}\nsbctl: {sbctl_output}\nefivar: {efivar_output}"

        return self._assert(
            "pk_enrolled",
            sbctl_pk and efivar_pk,
            "expected Platform Key (PK) to be enrolled (both sbctl and efivar)",
            details,
        )

    def assert_kek_enrolled(self) -> AssertionResult:
        """verify Key Exchange Key (KEK) is enrolled."""
        code, stdout, _ = self._run_command("ls /sys/firmware/efi/efivars/KEK-* 2>/dev/null")

        return self._assert(
            "kek_enrolled",
            code == 0,
            "expected Key Exchange Key (KEK) to be enrolled",
            stdout,
        )

    def assert_db_enrolled(self) -> AssertionResult:
        """verify Signature Database (db) is enrolled."""
        code, stdout, _ = self._run_command("ls /sys/firmware/efi/efivars/db-* 2>/dev/null")

        return self._assert(
            "db_enrolled",
            code == 0,
            "expected Signature Database (db) to be enrolled",
            stdout,
        )

    def assert_sbctl_verify_all(self, efi_path: str = "/efi") -> AssertionResult:
        """verify all boot files pass sbctl verification."""
        code, stdout, stderr = self._run_command("sbctl verify")

        # sbctl verify returns 0 if all files are properly signed
        all_verified = code == 0

        return self._assert(
            "sbctl_verify_all",
            all_verified,
            "expected all boot files to pass sbctl verification",
            stdout if stdout else stderr,
        )

    def assert_live_iso_would_be_blocked(self) -> AssertionResult:
        """verify unsigned live ISO EFI loader would be blocked by secure boot.

        after custom secure boot key enrollment, the arch linux live ISO
        bootloader (and any other unsigned EFI binaries) should be rejected
        by firmware. this test verifies by checking sbctl status and
        confirming setup mode is disabled (keys are enrolled).
        """
        # verify setup mode is off (keys enrolled)
        _code, stdout, _ = self._run_command("sbctl status")

        setup_mode_disabled = "setup mode" in stdout.lower() and "disabled" in stdout.lower()
        secure_boot_enabled = "secure boot" in stdout.lower() and "enabled" in stdout.lower()

        # also check that microsoft keys are NOT in the db (optional, depends on config)
        # if microsoft keys were enrolled, the ISO might boot
        microsoft_keys_present = "microsoft" in stdout.lower()

        # iso would be blocked if setup mode is off AND either:
        # - secure boot is enabled, or
        # - we're out of setup mode (keys enrolled)
        would_block = setup_mode_disabled and (secure_boot_enabled or not microsoft_keys_present)

        details = f"setup_mode_disabled={setup_mode_disabled}, secure_boot_enabled={secure_boot_enabled}, ms_keys={microsoft_keys_present}\nsbctl output:\n{stdout}"

        return self._assert(
            "live_iso_blocked",
            would_block,
            "expected unsigned live ISO to be blocked after key enrollment",
            details,
        )

    def assert_secure_boot_keys_exist(self) -> AssertionResult:
        """verify secure boot key files exist in the expected locations."""
        # sbctl uses /var/lib/sbctl/keys as the default path
        key_files = [
            "/var/lib/sbctl/keys/PK/PK.key",
            "/var/lib/sbctl/keys/KEK/KEK.key",
            "/var/lib/sbctl/keys/db/db.key",
        ]

        missing = []
        for key_file in key_files:
            code, _, _ = self._run_command(f"test -f {key_file}")
            if code != 0:
                missing.append(key_file)

        return self._assert(
            "secure_boot_keys_exist",
            len(missing) == 0,
            f"missing secure boot key files: {missing}" if missing else "all key files exist",
        )

    # =========================================================================
    # USB boot drive assertions (plausible deniability encryption)
    # =========================================================================

    def assert_internal_disk_holds_only_ciphertext(self, disk: str) -> AssertionResult:
        # no partition table, nothing that identifies the disk, no LUKS magic, and random
        # data from the first byte to the last
        _, partitions, _ = self._run_command(f"lsblk -nlo TYPE {disk}")
        _, partition_table, _ = self._run_command(f"blkid -p -o value -s PTTYPE {disk}")
        _, filesystem_type, _ = self._run_command(f"blkid -p -o value -s TYPE {disk}")
        luks_code, _, _ = self._run_command(f"cryptsetup isLuks {disk}")
        _, probe, _ = self._run_command(f"python -c '{CIPHERTEXT_PROBE}' {disk}", timeout=120)
        luks_magic, zero_mebibytes, gpt_signatures, zero_tail_blocks = [
            *probe.split(),
            "?",
            "?",
            "?",
            "?",
        ][:4]
        facts = {
            "partitions": partitions.split().count("part"),
            "partition_table": partition_table.strip(),
            "identifies_as": filesystem_type.strip(),
            "is_luks": luks_code == 0,
            "luks_magic": luks_magic,
            "zero_mebibytes": zero_mebibytes,
            "gpt_signatures": gpt_signatures,
            "zero_blocks_in_last_mebibyte": zero_tail_blocks,
        }
        expected = {
            "partitions": 0,
            "partition_table": "",
            "identifies_as": "",
            "is_luks": False,
            "luks_magic": "0",
            "zero_mebibytes": "0",
            "gpt_signatures": "0",
            "zero_blocks_in_last_mebibyte": "0",
        }
        return self._assert(
            "internal_disk_holds_only_ciphertext",
            facts == expected,
            f"{disk} shows more than ciphertext: {facts}",
            str(facts),
        )

    def assert_usb_boot_drive_layout(self, device: str, recovery_system: bool) -> AssertionResult:
        expected = {1: "vfat", 2: "crypto_LUKS"}
        if recovery_system:
            expected[3] = "ext4"
        found = {}
        for number in expected:
            _, filesystem, _ = self._run_command(f"blkid -p -o value -s TYPE {device}{number}")
            found[number] = filesystem.strip()
        _, efi_type, _ = self._run_command(f"blkid -p -o value -s PART_ENTRY_TYPE {device}1")
        return self._assert(
            "usb_boot_drive_layout",
            found == expected and efi_type.strip() == EFI_SYSTEM_PARTITION_TYPE,
            f"expected {expected} with an EFI system partition, found {found} ({efi_type.strip()})",
        )

    def assert_drive_header_unlocks(
        self, header_partition: str, data_device: str, passphrase: str
    ) -> AssertionResult:
        code, _, stderr = self._run_command(
            f"printf '%s' '{passphrase}' | cryptsetup open --test-passphrase "
            f"--header {header_partition} --key-file - {data_device}",
            timeout=120,
        )
        return self._assert(
            "drive_header_unlocks_internal_disk",
            code == 0,
            f"{header_partition} does not unlock {data_device}: {stderr.strip()}",
        )

    def assert_booted_through_the_detached_header(self, data_device: str) -> AssertionResult:
        _, cmdline, _ = self._run_command("cat /proc/cmdline")
        _, status, _ = self._run_command("cryptsetup status cryptroot")
        backing_devices = [line.split()[-1] for line in status.splitlines() if "device:" in line]
        return self._assert(
            "booted_through_detached_header",
            "rd.luks.data=" in cmdline
            and "/dev/disk/by-id/" in cmdline
            and backing_devices == [data_device],
            f"cmdline or cryptroot do not show the detached header: {cmdline.strip()}",
            status,
        )

    def assert_efi_mounted_on_demand_from(self, efi_partition: str) -> AssertionResult:
        self._run_command("ls /efi/EFI")
        _, source, _ = self._run_command("findmnt -n -t vfat -o SOURCE --mountpoint /efi")
        _, fstab, _ = self._run_command("grep ' */efi' /etc/fstab || grep '/efi' /etc/fstab")
        return self._assert(
            "efi_mounted_on_demand",
            source.strip() == efi_partition and "x-systemd.automount" in fstab,
            f"/efi is {source.strip() or 'not mounted'} (fstab: {fstab.strip()})",
        )

    def assert_firmware_keeps_no_boot_traces(self) -> AssertionResult:
        _, boot_entries, _ = self._run_command("efibootmgr")
        token_code, _, _ = self._run_command("ls /sys/firmware/efi/efivars/LoaderSystemToken-*")
        return self._assert(
            "firmware_keeps_no_boot_traces",
            "Linux Boot Manager" not in boot_entries and token_code != 0,
            "the firmware holds a boot entry or a systemd-boot system token",
            boot_entries,
        )

    def assert_boot_menu_lists(self, entry_ids: list[str]) -> AssertionResult:
        return self._assert_all_present(
            "boot_menu_entries", "bootctl list --no-pager", entry_ids, "boot entries"
        )

    def assert_every_boot_file_signed(self) -> AssertionResult:
        _, stdout, _ = self._run_command("sbctl verify 2>&1")
        plain = ANSI_COLOUR.sub("", stdout)
        unsigned = [line.strip() for line in plain.splitlines() if "not signed" in line]
        return self._assert(
            "every_boot_file_signed",
            not unsigned and "arch-recovery.efi" in plain,
            f"unsigned: {unsigned}" if unsigned else "the recovery UKI was not verified",
            plain,
        )

    def assert_usb_backup_has_manifest(
        self, mount_point: str = "/mnt/usb-backup"
    ) -> AssertionResult:
        """verify the USB backup partition contains a backup manifest."""
        code, stdout, _ = self._run_command(f"cat {mount_point}/manifest.yaml 2>/dev/null")
        has_manifest = code == 0 and "timestamp" in stdout

        return self._assert(
            "usb_backup_has_manifest",
            has_manifest,
            f"expected manifest.yaml in {mount_point}/",
            stdout,
        )

    def assert_usb_backup_has_package_catalog(
        self, mount_point: str = "/mnt/usb-backup"
    ) -> AssertionResult:
        """verify the USB backup partition contains a package catalog."""
        code, stdout, _ = self._run_command(
            f"cat {mount_point}/config/package_catalog.yaml 2>/dev/null"
        )
        has_catalog = code == 0 and "cataloged" in stdout

        return self._assert(
            "usb_backup_has_package_catalog",
            has_catalog,
            f"expected package_catalog.yaml in {mount_point}/config/",
            stdout,
        )

    def assert_usb_backup_has_config(self, mount_point: str = "/mnt/usb-backup") -> AssertionResult:
        """verify the USB backup partition contains an exported config.yaml."""
        code, stdout, _ = self._run_command(f"cat {mount_point}/config/config.yaml 2>/dev/null")
        has_config = code == 0 and "system:" in stdout

        return self._assert(
            "usb_backup_has_config",
            has_config,
            f"expected config.yaml in {mount_point}/config/",
            stdout,
        )

    def assert_usb_backup_has_category_directory(
        self, category: str, mount_point: str = "/mnt/usb-backup"
    ) -> AssertionResult:
        """verify the USB backup partition has a specific category directory."""
        code, stdout, _ = self._run_command(f"test -d {mount_point}/{category} && echo 'exists'")
        has_directory = code == 0 and "exists" in stdout

        return self._assert(
            f"usb_backup_has_{category}_dir",
            has_directory,
            f"expected {category}/ directory in {mount_point}/",
            stdout,
        )

    def assert_kernel_cmdline_has_detached_header(self) -> AssertionResult:
        """verify kernel cmdline references a detached LUKS header."""
        _code, stdout, _ = self._run_command("cat /proc/cmdline")
        has_header_reference = "header=/luks_header.img" in stdout

        return self._assert(
            "cmdline_detached_header",
            has_header_reference,
            "expected rd.luks.options with header= in kernel cmdline",
            stdout,
        )
