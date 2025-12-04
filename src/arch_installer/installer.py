"""main installer orchestrator."""

import os
import sys
from dataclasses import dataclass
from enum import Enum, auto

from arch_installer.config.loader import load_main_yaml_config
from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner, SystemCommandRunner
from arch_installer.core.config_recap import confirm_config_recap, print_config_recap
from arch_installer.core.prompts import (
    prompt_disk_selection,
    prompt_secrets_key,
    run_interactive_setup,
)
from arch_installer.core.runtime_state import (
    RuntimeConfig,
    create_default_runtime_config,
)
from arch_installer.core.secrets import (
    SECRETS_KEY_ENV_VAR_LABEL,
    decrypt_secrets_from_config,
    get_secrets_key_from_env,
)
from arch_installer.errors import ConfigurationError
from arch_installer.steps.boot import BootloaderSetup, UkiGenerator
from arch_installer.steps.firewall import FirewallSetup
from arch_installer.steps.gpu import GpuDriverSetup
from arch_installer.steps.migration import InstallationMigrator
from arch_installer.steps.packages import PackageInstaller
from arch_installer.steps.snapper import SnapperSetup
from arch_installer.steps.storage import StorageProvisioner, WipeMethod
from arch_installer.steps.system import SystemConfigurator

# output formatting constants
_BANNER_WIDTH = 80
_SECTION_PREFIX = ">>>>>"


def _print_banner(title: str, subtitle: str = "") -> None:
    print("\n" + "=" * _BANNER_WIDTH)
    padding = (_BANNER_WIDTH - len(title)) // 2
    print(f"{' ' * padding}{title}")
    if subtitle:
        sub_padding = (_BANNER_WIDTH - len(subtitle)) // 2
        print(f"{' ' * sub_padding}{subtitle}")
    print("=" * _BANNER_WIDTH)


def _print_step_header(step_num: int, total: int, name: str) -> None:
    header = f"STEP {step_num}/{total}: {name}"
    print(f"\n{'=' * 5} {header} {'=' * 5}")


def _print_section(message: str) -> None:
    print(f"{_SECTION_PREFIX} {message}")


class InstallStep(Enum):
    STORAGE = auto()
    PACKAGES = auto()
    SYSTEM = auto()
    GPU = auto()
    MKINITCPIO = auto()
    BOOTLOADER = auto()
    SNAPPER = auto()
    FIREWALL = auto()
    MIGRATION = auto()


@dataclass
class StepDefinition:
    step: InstallStep
    name: str
    description: str
    required: bool = True


STEP_DEFINITIONS: dict[InstallStep, StepDefinition] = {
    InstallStep.STORAGE: StepDefinition(
        step=InstallStep.STORAGE,
        name="Disk Selection & Storage Setup",
        description="Select disk, partition (EFI + LUKS), format (BTRFS), mount subvolumes.",
        required=True,
    ),
    InstallStep.PACKAGES: StepDefinition(
        step=InstallStep.PACKAGES,
        name="Package Installation",
        description="Install base packages, kernels, firmware, and desktop environment.",
        required=True,
    ),
    InstallStep.SYSTEM: StepDefinition(
        step=InstallStep.SYSTEM,
        name="System Configuration",
        description="Configure hostname, username, timezone, locale, keymap.",
        required=True,
    ),
    InstallStep.GPU: StepDefinition(
        step=InstallStep.GPU,
        name="GPU Driver Setup",
        description="Install GPU drivers based on configuration.",
        required=False,
    ),
    InstallStep.MKINITCPIO: StepDefinition(
        step=InstallStep.MKINITCPIO,
        name="mkinitcpio & UKI Generation",
        description="Configure mkinitcpio, generate and sign UKIs for Secure Boot.",
        required=True,
    ),
    InstallStep.BOOTLOADER: StepDefinition(
        step=InstallStep.BOOTLOADER,
        name="Bootloader Setup",
        description="Install systemd-boot and sign bootloader binaries.",
        required=True,
    ),
    InstallStep.SNAPPER: StepDefinition(
        step=InstallStep.SNAPPER,
        name="Snapper Configuration",
        description="Configure automated BTRFS snapshots with snapper.",
        required=False,
    ),
    InstallStep.FIREWALL: StepDefinition(
        step=InstallStep.FIREWALL,
        name="Firewall Configuration",
        description="Enable UFW firewall with secure defaults.",
        required=False,
    ),
    InstallStep.MIGRATION: StepDefinition(
        step=InstallStep.MIGRATION,
        name="Migration from Existing Install",
        description="Preserve data from existing Arch installation.",
        required=False,
    ),
}


class ArchInstaller:
    def __init__(
        self,
        config: DeclaredConfig,
        runtime_config: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._runtime_config = runtime_config
        self._runner = runner

    def install(self) -> None:
        self._print_header()
        self._show_config_recap()

        steps = [
            (InstallStep.MIGRATION, self._run_migration_step),
            (InstallStep.STORAGE, self._run_storage_step),
            (InstallStep.PACKAGES, self._run_packages_step),
            (InstallStep.SYSTEM, self._run_system_step),
            (InstallStep.GPU, self._run_gpu_step),
            (InstallStep.MKINITCPIO, self._run_mkinitcpio_step),
            (InstallStep.BOOTLOADER, self._run_bootloader_step),
            (InstallStep.SNAPPER, self._run_snapper_step),
            (InstallStep.FIREWALL, self._run_firewall_step),
        ]

        total = len(steps)
        for index, (step, step_runner) in enumerate(steps, 1):
            step_def = STEP_DEFINITIONS[step]
            _print_step_header(index, total, step_def.name)

            if step in self._runtime_config.excluded_steps:
                _print_section("Step excluded by user.")
                continue

            step_runner()

        self._install_utility_scripts()
        self._write_final_config()
        self._print_completion()

    def _show_config_recap(self) -> None:
        """display configuration recap before installation.

        in non-interactive mode, shows recap without confirmation (auto-proceed).
        in interactive mode, the recap was already shown during prompts.
        """
        if self._runtime_config.non_interactive:
            print_config_recap(self._config, self._runtime_config)

    def _print_header(self) -> None:
        _print_banner("Declarative ArchLinux Installer (DALI)", "Starting Installation Process")

    def _print_completion(self) -> None:
        _print_banner("Declarative ArchLinux Installer (DALI) - INSTALLATION COMPLETE")
        print(
            """
Installation / convergence completed successfully!

Next steps:
  1. Set root password (optional): arch-chroot /mnt passwd
  2. Unmount and reboot:
       swapoff -a
       umount -R /mnt
       reboot
  3. After reboot, run verification:
       verify-install

Installed utilities (available after reboot):
  - verify-install         Run post-install checks
  - manage-snapshot-ukis  Regenerate bootable snapshot UKIs
  - dotfiles-sync          Backup/restore dotfiles to GitHub

Notes:
  - If Secure Boot was not in Setup Mode, enter BIOS/UEFI setup.
    Put it into Setup Mode explicitly, or delete existing PK key.
    After rebooting, re-run to sign UKIs and enroll keys.
"""
        )

    def _run_migration_step(self) -> None:
        if not self._runtime_config.enable_migration:
            print(">>>>> Skipped (migration not enabled).")
            return

        migrator = InstallationMigrator(
            self._config,
            self._runtime_config,
            self._runner,
            migration_enabled=True,
        )
        if self._runtime_config.target_disk:
            migrator.migrate(self._runtime_config.target_disk)

    def _run_storage_step(self) -> None:
        if self._ensure_target_mounted():
            result = self._runner.run("findmnt -n -o SOURCE /mnt", raise_on_nonzero_exit=False)
            source = result.stdout.strip() if result.success else "unknown"
            print(f">>>>> /mnt is already mounted from {source}")
            print(">>>>> Skipping disk selection as filesystem is mounted.")

            if not self._ensure_efi_mounted():
                print("Warning: /mnt/efi not mounted. Please ensure it is mounted.")
            return

        provisioner = StorageProvisioner(self._config, self._runtime_config, self._runner)

        # determine wipe method from runtime config or config.yaml
        wipe_method_str = self._runtime_config.wipe_method or self._config.storage.wipe_method
        wipe_method_map = {
            "quick": WipeMethod.QUICK,
            "secure": WipeMethod.SECURE,
            "discard": WipeMethod.DISCARD,
            "skip": WipeMethod.SKIP,
        }
        wipe_method = wipe_method_map.get(wipe_method_str, WipeMethod.QUICK)

        # migration mode requires wiping disk to create new LUKS with new password
        # data was already copied to staging before this step
        if self._runtime_config.enable_migration:
            wipe_method = WipeMethod.QUICK

        provisioner.provision_storage(wipe_method)

    def _run_packages_step(self) -> None:
        installer = PackageInstaller(
            self._config,
            self._runtime_config,
            self._runner,
            enable_migration=self._runtime_config.enable_migration,
        )
        installer.install_packages()

        if self._runtime_config.enable_migration:
            migrator = InstallationMigrator(
                self._config,
                self._runtime_config,
                self._runner,
                migration_enabled=True,
            )
            migrator.post_install_restore(str(self._runtime_config.target_root))

    def _run_system_step(self) -> None:
        configurator = SystemConfigurator(self._config, self._runtime_config, self._runner)
        configurator.configure_system()

    def _run_gpu_step(self) -> None:
        setup = GpuDriverSetup(
            self._runner,
            gpu_vendor=self._runtime_config.gpu_vendor,
            gpu_driver=self._runtime_config.gpu_driver,
        )
        setup.configure_gpu()

    def _run_mkinitcpio_step(self) -> None:
        generator = UkiGenerator(self._config, self._runtime_config, self._runner)
        generator.generate_ukis()

    def _run_bootloader_step(self) -> None:
        setup = BootloaderSetup(self._config, self._runtime_config, self._runner)
        setup.setup_bootloader()

    def _run_snapper_step(self) -> None:
        setup = SnapperSetup(self._config, self._runtime_config, self._runner)
        setup.configure_snapper()

    def _run_firewall_step(self) -> None:
        setup = FirewallSetup(
            self._runner,
            self._config.firewall,
            enabled=self._runtime_config.enable_firewall,
        )
        setup.configure_firewall()

    def _ensure_target_mounted(self) -> bool:
        result = self._runner.run("mountpoint -q /mnt", raise_on_nonzero_exit=False)
        return result.success

    def _ensure_efi_mounted(self) -> bool:
        result = self._runner.run("mountpoint -q /mnt/efi", raise_on_nonzero_exit=False)
        return result.success

    def _install_utility_scripts(self) -> None:
        print("\n>>>>> Installing utility scripts to /usr/local/bin...")

        scripts_dir = "/mnt/usr/local/bin"
        self._runner.run(f"mkdir -p {scripts_dir}")

        scripts = [
            ("scripts/verify_install.sh", "verify-install"),
            ("scripts/manage_snapshot_entries.sh", "refresh-snapshot-ukis"),
            ("scripts/dotfiles-sync.sh", "dotfiles-sync"),
        ]

        for src, dest in scripts:
            result = self._runner.run(f"test -f {src}", raise_on_nonzero_exit=False)
            if result.success:
                self._runner.run(f"cp {src} {scripts_dir}/{dest}")
                self._runner.run(f"chmod +x {scripts_dir}/{dest}")
                print(f"    Installed: {dest}")

        print(">>>>> Utility scripts installed.")

    def _write_final_config(self) -> None:
        """write final configuration to installed system."""
        import yaml

        print("\n>>>>> Writing final configuration...")

        username = self._config.system.user.name

        # build final config dict from current state
        final_config = self._build_final_config_dict()

        # write to /mnt/home/<user>/final_config.yaml
        user_home = f"/mnt/home/{username}"
        self._runner.run(f"mkdir -p {user_home}", raise_on_nonzero_exit=False)

        final_config_path = f"{user_home}/final_config.yaml"
        yaml_content = yaml.dump(final_config, default_flow_style=False, sort_keys=False)

        # escape single quotes in yaml content for shell
        escaped_content = yaml_content.replace("'", "'\\''")
        self._runner.run(f"cat > {final_config_path} << 'FINALCFG'\n{yaml_content}FINALCFG")

        # set ownership
        self._runner.run(
            f"arch-chroot /mnt chown {username}:{username} /home/{username}/final_config.yaml",
            raise_on_nonzero_exit=False,
        )

        print(f"    Final config written to: /home/{username}/final_config.yaml")

    def _build_final_config_dict(self) -> dict:
        """build config dict dynamically from declared config with runtime overrides.

        starts from the declared config and overlays any values that were changed
        during installation (via interactive prompts or env vars).
        """
        cfg = self._config
        state = self._runtime_config

        # helper to convert frozen dataclass to dict recursively
        def _to_dict(obj):
            if hasattr(obj, "__dataclass_fields__"):
                result = {}
                for field_name in obj.__dataclass_fields__:
                    value = getattr(obj, field_name)
                    result[field_name] = _to_dict(value)
                return result
            elif isinstance(obj, tuple):
                return [_to_dict(item) for item in obj]
            else:
                return obj

        # start with full config as base
        result = {
            "system": _to_dict(cfg.system),
            "storage": _to_dict(cfg.storage),
            "boot": _to_dict(cfg.boot),
            "packages": _to_dict(cfg.packages),
            "gpu": _to_dict(cfg.gpu),
            "snapper": _to_dict(cfg.snapper),
            "firewall": _to_dict(cfg.firewall),
            "docker": _to_dict(cfg.docker),
            "dotfiles": _to_dict(cfg.dotfiles),
            "migration": _to_dict(cfg.migration),
        }

        # overlay runtime overrides (only non-empty values)
        if state.target_disk:
            result["storage"]["target_disk"] = state.target_disk
        if state.swap_size_mb > 0:
            result["storage"]["swap"]["size_mb"] = state.swap_size_mb
        if state.cpu_vendor:
            result["system"]["cpu_vendor"] = state.cpu_vendor
        if state.gpu_vendor:
            result["gpu"]["vendor"] = state.gpu_vendor
        if state.gpu_driver:
            result["gpu"]["driver"] = state.gpu_driver
        if state.selected_kernels:
            result["boot"]["selected_kernels"] = state.selected_kernels
        if state.selected_desktops:
            result["packages"]["selected_desktops"] = state.selected_desktops

        # overlay option overrides (explicit flags)
        result["storage"]["swap"]["enabled"] = not state.skip_swap
        result["storage"]["swap"]["hibernation"]["enabled"] = state.enable_hibernation
        result["boot"]["enable_snapshot_boot"] = state.enable_snapshot_boot
        result["firewall"]["enabled"] = state.enable_firewall
        result["docker"]["enabled"] = state.enable_docker
        result["migration"]["enabled"] = state.enable_migration

        return result


def _is_system_config_minimal(config: DeclaredConfig) -> bool:
    """check if system configuration is missing or uses defaults.

    returns True if we should prompt for hostname, username, timezone, etc.
    """
    # check for missing or default values that suggest minimal/empty config
    system = config.system
    if not system.hostname or system.hostname in ("", "archlinux", "localhost"):
        return True
    if not system.timezone or system.timezone in ("", "UTC"):
        return True
    if not system.user.name or system.user.name in ("", "user"):
        return True
    return False


def _resolve_target_disk(
    config_disk: str,
    env_disk: str,
    non_interactive: bool,
) -> str:
    """resolve the target disk from config, env var, or user prompt.

    priority order:
    1. TARGET_DISK environment variable
    2. storage.target_disk from config.yaml
    3. interactive prompt (if not in non-interactive mode)

    args:
        config_disk: target_disk value from config.yaml
        env_disk: TARGET_DISK environment variable value
        non_interactive: if True, fail instead of prompting

    returns:
        resolved disk path

    raises:
        ConfigurationError: if no disk specified in non-interactive mode
    """
    if env_disk:
        return env_disk

    if config_disk:
        return config_disk

    if non_interactive:
        raise ConfigurationError(
            "No target disk specified. Set TARGET_DISK environment variable "
            "or storage.target_disk in config.yaml for non-interactive mode."
        )

    return prompt_disk_selection()


def create_installer_from_env() -> ArchInstaller:
    config_path = os.environ.get("CONFIG_PATH")
    config = load_main_yaml_config(config_path)

    verbose = os.environ.get("VERBOSE", "").lower() == "true"
    runner = SystemCommandRunner(verbose=verbose)
    state = create_default_runtime_config()

    non_interactive = os.environ.get("NON_INTERACTIVE", "").lower() == "true"

    if not non_interactive:
        return _create_installer_interactive(config, runner, state)
    else:
        return _create_installer_non_interactive(config, runner, state)


def _create_installer_interactive(
    config: DeclaredConfig,
    runner: CommandRunner,
    state: RuntimeConfig,
) -> ArchInstaller:
    secrets = config.secrets
    has_encrypted_secrets = bool(
        secrets and (secrets.luks_password_encrypted or secrets.user_password_encrypted)
    )

    require_system_config = _is_system_config_minimal(config)

    selections = run_interactive_setup(
        has_encrypted_secrets=has_encrypted_secrets,
        require_system_config=require_system_config,
        runner=runner,
        config=config,
    )

    luks_password = selections.luks_password
    user_password = selections.user_password

    if has_encrypted_secrets and secrets and (not luks_password or not user_password):
        secrets_key = get_secrets_key_from_env()
        if not secrets_key:
            secrets_key = prompt_secrets_key()

        decrypted_luks, decrypted_user = decrypt_secrets_from_config(
            secrets.luks_password_encrypted,
            secrets.user_password_encrypted,
            secrets_key,
        )
        luks_password = luks_password or decrypted_luks
        user_password = user_password or decrypted_user

    skip_swap = selections.swap_size_mb == 0

    # populate runtime config directly from selections
    state.non_interactive = False
    state.enable_snapshot_boot = selections.enable_snapshot_boot
    state.enable_firewall = selections.enable_firewall
    state.enable_hibernation = selections.enable_hibernation
    state.enable_docker = selections.enable_docker
    state.enable_migration = selections.enable_migration
    state.skip_swap = skip_swap
    state.package_profile = os.environ.get("PACKAGE_PROFILE", "base")
    state.target_disk = selections.target_disk
    state.luks_password = luks_password
    state.source_luks_password = selections.source_luks_password or os.environ.get(
        "SOURCE_LUKS_PASSWORD", ""
    )
    state.user_password = user_password
    state.swap_size_mb = selections.swap_size_mb
    state.wipe_method = selections.wipe_method
    state.cpu_vendor = selections.cpu_vendor
    state.gpu_vendor = selections.gpu_vendor
    state.gpu_driver = selections.gpu_driver
    state.selected_desktops = selections.selected_desktops or []
    state.hostname = selections.hostname
    state.username = selections.username
    state.timezone = selections.timezone

    kernels_str = os.environ.get("SELECTED_KERNELS", "")
    if kernels_str:
        state.selected_kernels = kernels_str.split(",")
    elif config.boot.kernels:
        state.selected_kernels = [kernel.package for kernel in config.boot.kernels]

    return ArchInstaller(config, state, runner)


def _get_configured_desktops(config: DeclaredConfig) -> list[str]:
    """get list of desktop environments defined in config.yaml.

    returns all desktops that have packages defined (non-empty lists).
    """
    desktops = config.packages.desktops
    configured = []

    if desktops.kde:
        configured.append("kde")
    if desktops.gnome:
        configured.append("gnome")
    if desktops.hyprland:
        configured.append("hyprland")

    return configured


def _create_installer_non_interactive(
    config: DeclaredConfig,
    runner: CommandRunner,
    state: RuntimeConfig,
) -> ArchInstaller:
    # determine passwords - env vars take priority, then encrypted config, then default
    luks_password = os.environ.get("LUKS_PASSWORD", "")
    user_password = os.environ.get("USER_PASSWORD", "")
    source_luks_password = os.environ.get("SOURCE_LUKS_PASSWORD", "")

    # if not provided via env vars, try decrypting from config
    if not luks_password or not user_password:
        secrets = config.secrets
        if secrets and (secrets.luks_password_encrypted or secrets.user_password_encrypted):
            decrypted_luks, decrypted_user = decrypt_secrets_from_config(
                secrets.luks_password_encrypted,
                secrets.user_password_encrypted,
            )
            if not luks_password:
                luks_password = decrypted_luks
            if not user_password:
                user_password = decrypted_user

    # fallback to default if still empty
    luks_password = luks_password or "password"
    user_password = user_password or "password"

    # resolve target disk: env var > config storage
    config_disk = config.storage.target_disk or ""
    target_disk = _resolve_target_disk(
        config_disk=config_disk,
        env_disk=os.environ.get("TARGET_DISK", ""),
        non_interactive=True,
    )

    # determine feature flags: env var > config boot/section > defaults
    def _get_bool_option(env_var: str, config_val: bool, default: bool) -> bool:
        env_str = os.environ.get(env_var, "").lower()
        if env_str in ("true", "1", "yes"):
            return True
        if env_str in ("false", "0", "no"):
            return False
        return config_val if config_val is not None else default

    enable_snapshot_boot = _get_bool_option(
        "ENABLE_SNAPSHOT_BOOT",
        config.boot.enable_snapshot_boot,
        False,
    )
    enable_firewall = _get_bool_option(
        "ENABLE_UFW",
        config.firewall.enabled,
        True,
    )
    enable_hibernation = _get_bool_option(
        "ENABLE_HIBERNATION",
        config.storage.swap.hibernation.enabled,
        False,
    )
    enable_docker = _get_bool_option(
        "ENABLE_DOCKER",
        config.docker.enabled,
        False,
    )
    skip_swap = _get_bool_option(
        "SKIP_SWAP",
        not config.storage.swap.enabled,
        False,
    )

    # resolve hardware settings: env var > config gpu > auto
    cpu_vendor = os.environ.get("CPU_VENDOR", "") or ""
    gpu_vendor = os.environ.get("GPU_VENDOR", "") or config.gpu.vendor or "none"
    gpu_driver = os.environ.get("GPU_DRIVER", "") or config.gpu.driver or ""

    # resolve swap size: env var > config storage
    swap_size_mb = int(os.environ.get("TEST_SWAP_SIZE_MB", "0") or "0")
    if not swap_size_mb:
        swap_size_mb = config.storage.swap.size_mb

    # resolve desktops: env var > config desktops (all defined desktops)
    desktops_str = os.environ.get("SELECTED_DESKTOPS", "")
    if desktops_str:
        selected_desktops = desktops_str.split(",")
    else:
        # in non-interactive mode, install all desktops defined in config
        selected_desktops = _get_configured_desktops(config)

    # populate runtime config directly
    state.non_interactive = True
    state.enable_snapshot_boot = enable_snapshot_boot
    state.enable_firewall = enable_firewall
    state.enable_hibernation = enable_hibernation
    state.enable_docker = enable_docker
    state.enable_migration = os.environ.get("ENABLE_MIGRATION", "").lower() == "true"
    state.skip_swap = skip_swap
    state.package_profile = os.environ.get("PACKAGE_PROFILE", "") or "base"
    state.target_disk = target_disk
    state.luks_password = luks_password
    state.source_luks_password = source_luks_password
    state.user_password = user_password
    state.swap_size_mb = swap_size_mb
    state.cpu_vendor = cpu_vendor
    state.gpu_vendor = gpu_vendor
    state.gpu_driver = gpu_driver
    state.selected_desktops = selected_desktops

    kernels_str = os.environ.get("SELECTED_KERNELS", "")
    if kernels_str:
        state.selected_kernels = kernels_str.split(",")
    elif config.boot.kernels:
        state.selected_kernels = [kernel.package for kernel in config.boot.kernels]

    return ArchInstaller(config, state, runner)


def main() -> int:
    try:
        installer = create_installer_from_env()
        installer.install()
        return 0
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user.")
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
