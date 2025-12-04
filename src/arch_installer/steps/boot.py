"""Boot and UKI setup - mkinitcpio, UKI generation, secure boot, bootloader."""

from dataclasses import fields

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.runtime_state import RuntimeConfig
from arch_installer.templates.mkinitcpio import (
    mkinitcpio_kernel_preset_header,
    mkinitcpio_main_conf,
    mkinitcpio_uki_variant_entry,
)
from arch_installer.templates.systemd_boot import systemd_boot_loader_conf


class KernelCommandLineBuilder:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._cmdline_config = config.boot.cmdline

    def build(self, luks_uuid: str, extra_params: str = "") -> str:
        parts: list[str] = []

        parts.append(f"rd.luks.name={luks_uuid}=cryptroot")
        parts.append("root=/dev/mapper/cryptroot")
        parts.append("rw")
        parts.append(f"rootflags={self._cmdline_config.rootflags}")

        if self._cmdline_config.quiet:
            parts.append("quiet")

        if self._cmdline_config.hardening:
            parts.extend(self._build_hardening_params())

        if self._state.enable_hibernation and not self._state.skip_swap:
            hibernation_params = self._build_hibernation_params()
            parts.extend(hibernation_params)

        if extra_params:
            parts.append(extra_params)

        return " ".join(parts)

    def _build_hardening_params(self) -> list[str]:
        hardening = self._cmdline_config.hardening
        if not hardening:
            return []

        params: list[str] = []
        for field in fields(hardening):
            value = getattr(hardening, field.name)
            if value is not None and value != "" and value != 0:
                params.append(f"{field.name}={value}")

        return params

    def _build_hibernation_params(self) -> list[str]:
        params: list[str] = []

        # resume device is cryptroot since swapfile is on encrypted btrfs
        params.append("resume=/dev/mapper/cryptroot")

        # get swap file offset for btrfs swapfile
        swap_path = self._config.storage.swap.path
        target_swap_path = f"{self._state.target_root}{swap_path}"

        offset = self._get_swapfile_offset(target_swap_path)
        if offset:
            params.append(f"resume_offset={offset}")
            print(
                f"    Hibernation configured: resume=/dev/mapper/cryptroot resume_offset={offset}"
            )

        return params

    def _get_swapfile_offset(self, swap_path: str) -> str | None:
        result = self._runner.run(
            f"btrfs inspect-internal map-swapfile -r {swap_path}",
            raise_on_nonzero_exit=False,
        )
        if result.success and result.stdout.strip():
            return result.stdout.strip()

        # fallback to filefrag for non-btrfs or older kernels
        result = self._runner.run(
            f"filefrag -v {swap_path}",
            raise_on_nonzero_exit=False,
        )
        if result.success:
            # parse filefrag output to get physical offset
            # format: "   0:        0..   16383:     192512..    208895:  16384:"
            for line in result.stdout.split("\n"):
                if ".." in line and not line.strip().startswith("ext:"):
                    parts = line.split()
                    if len(parts) >= 4:
                        # extract first physical offset
                        offset_part = parts[3].replace(".", "").replace(":", "")
                        if offset_part.isdigit():
                            return offset_part

        print("    Warning: Could not determine swapfile offset for hibernation")
        return None


class UkiGenerator:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._boot_config = config.boot

    def generate_ukis(self) -> None:
        print(">>>>> Configuring mkinitcpio...")

        self._configure_mkinitcpio()

        luks_uuid = self._get_luks_uuid()
        kernels = self._get_installed_kernels()

        if not kernels:
            print(">>>>> Warning: No kernels found. Skipping UKI generation.")
            return

        variants = self._get_variants()
        cmdline_builder = KernelCommandLineBuilder(self._config, self._state, self._runner)
        base_cmdline = cmdline_builder.build(luks_uuid)

        for kernel in kernels:
            self._configure_kernel_preset(kernel, variants, base_cmdline)

        self._write_default_cmdline(base_cmdline)
        self._ensure_vconsole()
        self._prepare_secure_boot()

        print(">>>>> Generating UKIs...")
        self._runner.run_as_chroot("mkinitcpio -P")

        self._sign_ukis(kernels, variants)

    def _configure_mkinitcpio(self) -> None:
        hooks = " ".join(self._boot_config.hooks)
        conf_content = mkinitcpio_main_conf(hooks)

        conf_file = "/mnt/etc/mkinitcpio.conf"
        self._runner.run(f"cat > {conf_file} << 'EOF'\n{conf_content}EOF")

        self._runner.run("mkdir -p /mnt/efi/EFI/Linux")
        print(f"    Written {conf_file}")

    def _get_luks_uuid(self) -> str:
        result = self._runner.run("cryptsetup status cryptroot", raise_on_nonzero_exit=False)
        if result.success:
            for line in result.stdout.split("\n"):
                if "device:" in line:
                    backing_device = line.split()[-1]
                    uuid_result = self._runner.run(
                        f"blkid -s UUID -o value {backing_device}", raise_on_nonzero_exit=False
                    )
                    if uuid_result.success and uuid_result.stdout.strip():
                        return uuid_result.stdout.strip()

        result = self._runner.run(
            "blkid -t TYPE=crypto_LUKS -s UUID -o value", raise_on_nonzero_exit=False
        )
        if result.success and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]

        if self._state.root_partition:
            result = self._runner.run(
                f"blkid -s UUID -o value {self._state.root_partition}", raise_on_nonzero_exit=False
            )
            if result.success and result.stdout.strip():
                return result.stdout.strip()

        raise RuntimeError("Could not determine LUKS partition UUID")

    def _get_installed_kernels(self) -> list[str]:
        kernels: list[str] = []

        kernel_packages = self._state.selected_kernels or [
            k.package for k in self._boot_config.kernels
        ]

        for kernel in kernel_packages:
            result = self._runner.run(
                f"test -f /mnt/boot/vmlinuz-{kernel}", raise_on_nonzero_exit=False
            )
            if result.success:
                kernels.append(kernel)
                print(f"    Found installed kernel: {kernel}")
            else:
                print(f"    Skipping kernel {kernel} (not installed)")

        return kernels

    def _get_variants(self) -> list[tuple[str, str]]:
        variants = [("default", "")]

        for flag in self._state.selected_debug_variants:
            for variant in self._boot_config.variants:
                if variant.suffix == flag:
                    variants.append((flag, variant.params))
                    break

        if len(variants) > 1:
            print(f"    Debug variants: {', '.join(v[0] for v in variants[1:])}")
        else:
            print("    Debug variants: None (default UKI only)")

        return variants

    def _configure_kernel_preset(
        self,
        kernel: str,
        variants: list[tuple[str, str]],
        base_cmdline: str,
    ) -> None:
        print(f"    Configuring preset for {kernel}...")

        preset_file = f"/mnt/etc/mkinitcpio.d/{kernel}.preset"
        kver = f"/boot/vmlinuz-{kernel}"

        preset_names = ["fallback"]
        for suffix, _ in variants:
            preset_names.append(suffix.replace("-", "_"))

        presets_str = " ".join(f"'{p}'" for p in preset_names)

        preset_content = mkinitcpio_kernel_preset_header(kernel, kver, presets_str)

        for suffix, extra_params in variants:
            preset_name = suffix.replace("-", "_")
            uki_path = f"/efi/EFI/Linux/arch-{kernel}-{suffix}.efi"
            cmdline_file = f"/etc/kernel/cmdline-{kernel}-{suffix}"

            if extra_params:
                full_cmdline = f"{base_cmdline} {extra_params}"
            else:
                full_cmdline = base_cmdline

            cmdline_host = f"/mnt{cmdline_file}"
            self._runner.run("mkdir -p /mnt/etc/kernel")
            self._runner.run(f'echo "{full_cmdline}" > {cmdline_host}')

            preset_content += mkinitcpio_uki_variant_entry(preset_name, uki_path, cmdline_file)

        self._runner.run(f"cat > {preset_file} << 'EOF'\n{preset_content}EOF")

    def _write_default_cmdline(self, cmdline: str) -> None:
        print(">>>>> Writing /mnt/etc/kernel/cmdline (default)...")
        self._runner.run("mkdir -p /mnt/etc/kernel")
        self._runner.run(f'echo "{cmdline}" > /mnt/etc/kernel/cmdline')
        print(f"    Cmdline: {cmdline}")

    def _ensure_vconsole(self) -> None:
        result = self._runner.run("test -f /mnt/etc/vconsole.conf", raise_on_nonzero_exit=False)
        if result.success:
            return

        keymap = self._config.system.locale.keymap
        self._runner.run(f'echo "KEYMAP={keymap}" > /mnt/etc/vconsole.conf')

    def _prepare_secure_boot(self) -> None:
        print(">>>>> Preparing Secure Boot (sbctl)...")

        result = self._runner.run_as_chroot("sbctl status", raise_on_nonzero_exit=False)
        setup_mode = False
        # sbctl uses ✗ for setup mode when ENABLED (because it's a "bad" security state)
        # and ✓ for setup mode when DISABLED (the secure state after key enrollment)
        # so we check for "enabled" text (case-insensitive) on the setup mode line
        for line in result.stdout.split("\n"):
            if "setup mode" in line.lower():
                if "enabled" in line.lower():
                    setup_mode = True
                    print("    Secure Boot is in Setup Mode - keys can be enrolled.")
                else:
                    print("    Note: Secure Boot is NOT in Setup Mode.")
                    print(
                        "    UKIs will be generated and signed, but key enrollment will be skipped."
                    )
                break
        else:
            # fallback to checking efivars directly
            efivar_result = self._runner.run_as_chroot(
                "cat /sys/firmware/efi/efivars/SetupMode-* 2>/dev/null | xxd -p | tail -c 3",
                raise_on_nonzero_exit=False,
            )
            if efivar_result.success and efivar_result.stdout.strip() == "01":
                setup_mode = True
                print("    Secure Boot is in Setup Mode (via efivars).")
            else:
                print("    Note: Secure Boot is NOT in Setup Mode.")

        # sbctl now stores keys at /var/lib/sbctl/keys/ by default
        key_file = "/mnt/var/lib/sbctl/keys/PK/PK.key"
        result = self._runner.run(f"test -f {key_file}", raise_on_nonzero_exit=False)
        if not result.success:
            if self._state.non_interactive:
                print("    [CI] Creating secure boot keys...")
                self._runner.run_as_chroot("sbctl create-keys", raise_on_nonzero_exit=False)
            else:
                print("    Creating secure boot keys...")
                result = self._runner.run_as_chroot(
                    "sbctl create-keys", raise_on_nonzero_exit=False
                )
                if not result.success:
                    print("    Warning: Could not create Secure Boot keys.")
                    print("    This is normal if not in Setup Mode.")
        else:
            print("    Secure boot keys already exist.")

        # enroll keys if in setup mode and configured to do so
        secure_boot_config = self._config.boot.secure_boot
        if setup_mode and secure_boot_config.enroll_keys:
            self._enroll_secure_boot_keys(secure_boot_config.include_microsoft_keys)

    def _enroll_secure_boot_keys(self, include_microsoft: bool) -> None:
        print(">>>>> Enrolling Secure Boot keys...")

        enroll_cmd = "sbctl enroll-keys"
        if include_microsoft:
            enroll_cmd += " --microsoft"
            print("    Including Microsoft keys for compatibility...")
        else:
            print("    NOT including Microsoft keys...")

        result = self._runner.run_as_chroot(enroll_cmd, raise_on_nonzero_exit=False)
        if result.success:
            print("    Secure Boot keys enrolled successfully.")
        else:
            print("    Warning: Key enrollment failed. You may need to:")
            print("      1. Reboot into UEFI settings")
            print("      2. Clear existing Secure Boot keys / enter Setup Mode")
            print(f"      3. Run: sbctl enroll-keys{' --microsoft' if include_microsoft else ''}")
            print(f"    Error: {result.stderr}")

    def _sign_ukis(self, kernels: list[str], variants: list[tuple[str, str]]) -> None:
        # sbctl now stores keys at /var/lib/sbctl/keys/ by default
        key_file = "/mnt/var/lib/sbctl/keys/db/db.key"
        result = self._runner.run(f"test -f {key_file}", raise_on_nonzero_exit=False)
        if not result.success:
            print(">>>>> Skipping UKI signing (no Secure Boot keys available).")
            return

        print(">>>>> Signing UKIs...")

        for kernel in kernels:
            for suffix, _ in variants:
                uki_path = f"/efi/EFI/Linux/arch-{kernel}-{suffix}.efi"
                uki_full = f"/mnt{uki_path}"

                result = self._runner.run(f"test -f {uki_full}", raise_on_nonzero_exit=False)
                if result.success:
                    print(f"    Signing {uki_path}...")
                    self._runner.run_as_chroot(
                        f"sbctl sign -s {uki_path}", raise_on_nonzero_exit=False
                    )


class BootloaderSetup:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._loader_config = config.boot.loader

    def setup_bootloader(self) -> None:
        print(">>>>> Installing systemd-boot...")

        self._install_bootctl()
        self._configure_loader()
        self._sign_bootloader()

        print(">>>>> Bootloader installation complete.")

    def _install_bootctl(self) -> None:
        if self._state.non_interactive:
            print("    [CI] Installing bootloader without EFI variables...")
            self._runner.run_as_chroot("bootctl install --esp-path=/efi --no-variables")
        else:
            self._runner.run_as_chroot("bootctl install --esp-path=/efi")

    def _configure_loader(self) -> None:
        print(">>>>> Configuring loader.conf...")

        loader_content = systemd_boot_loader_conf(
            timeout=self._loader_config.timeout,
            console_mode=self._loader_config.console_mode,
            editor_enabled=self._loader_config.editor,
        )

        self._runner.run(f"cat > /mnt/efi/loader/loader.conf << 'EOF'\n{loader_content}EOF")

    def _sign_bootloader(self) -> None:
        print(">>>>> Signing bootloader...")

        bootloader_paths = [
            "/efi/EFI/BOOT/BOOTX64.EFI",
            "/efi/EFI/systemd/systemd-bootx64.efi",
        ]

        for path in bootloader_paths:
            full_path = f"/mnt{path}"
            result = self._runner.run(f"test -f {full_path}", raise_on_nonzero_exit=False)
            if result.success:
                print(f"    Signing {path}...")
                self._runner.run_as_chroot(f"sbctl sign -s {path}", raise_on_nonzero_exit=False)
