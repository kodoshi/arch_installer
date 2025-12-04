"""
CLI implementation of the interaction strategy.

provides terminal-based prompts for all user input during installation.
"""

import getpass
import sys
from typing import Optional

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.interaction import (
    CPU_OPTIONS,
    DESKTOP_OPTIONS,
    GPU_OPTIONS,
    NVIDIA_DRIVER_OPTIONS,
    WIPE_OPTIONS,
    InstallationSelections,
    InteractionStrategy,
    MenuOption,
)


def _build_swap_options(default_size_mb: int = 0) -> list[MenuOption]:
    """build swap options list dynamically, including config default if not already present."""
    preset_sizes = [4096, 8192, 16384, 32768, 65536]

    # add default if not in presets
    if default_size_mb and default_size_mb not in preset_sizes:
        preset_sizes.append(default_size_mb)

    # sort by size
    preset_sizes.sort()

    options = []
    for size in preset_sizes:
        gb = size / 1024
        if gb >= 1:
            label = f"{int(gb)} GB" if gb == int(gb) else f"{gb:.1f} GB"
        else:
            label = f"{size} MB"
        options.append(MenuOption(str(size), label))

    # add special options at the end
    options.extend(
        [
            MenuOption("ram", "Match RAM size"),
            MenuOption("custom", "Custom size"),
            MenuOption("0", "No swap"),
        ]
    )

    return options


# menu formatting constants
MENU_WIDTH = 60
SECTION_CHAR = "="
SUBSECTION_CHAR = "-"
SECTION_PREFIX = ">>>>>"
INDENT = "     "


def _get_configured_desktops(config: Optional[DeclaredConfig]) -> list[str]:
    """extract desktop names that have packages defined in config."""
    if not config or not config.packages.desktops:
        return []
    desktops = config.packages.desktops
    result = []
    if desktops.kde:
        result.append("kde")
    if desktops.gnome:
        result.append("gnome")
    if desktops.hyprland:
        result.append("hyprland")
    return result


def _print_section_header(title: str) -> None:
    print(f"\n{SECTION_CHAR * MENU_WIDTH}")
    padding = (MENU_WIDTH - len(title)) // 2
    print(f"{' ' * padding}{title}")
    print(SECTION_CHAR * MENU_WIDTH)


def _print_subsection(message: str) -> None:
    print(f"\n{SECTION_PREFIX} {message}")


def _print_info(message: str) -> None:
    print(f"{INDENT}{message}")


def _print_menu(
    title: str,
    options: list[MenuOption],
    default_value: str = "",
) -> None:
    print(f"\n{title}")
    print(SUBSECTION_CHAR * len(title))
    for i, opt in enumerate(options, start=1):
        marker = " [config.yaml default]" if opt.value == default_value else ""
        print(f"  {i}) {opt.description}{marker}")


def _get_selection(
    options: list[MenuOption],
    prompt: str = "Select option",
    default_value: str = "",
) -> str:
    default_idx = None
    for i, opt in enumerate(options):
        if opt.value == default_value:
            default_idx = i + 1
            break

    prompt_text = f"{prompt} (1-{len(options)})"
    if default_idx:
        prompt_text += f" [Enter={default_idx}]"
    prompt_text += ": "

    while True:
        try:
            selection = input(prompt_text).strip()
            if not selection and default_idx:
                return options[default_idx - 1].value

            if not selection:
                continue

            idx = int(selection) - 1
            if 0 <= idx < len(options):
                return options[idx].value
            else:
                print(f"Invalid selection. Please enter a number between 1 and {len(options)}")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except (EOFError, KeyboardInterrupt):
            print("\nSelection cancelled.")
            sys.exit(1)


class CLIInteractionStrategy(InteractionStrategy):
    """CLI implementation using terminal prompts."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        config: DeclaredConfig | None = None,
    ) -> None:
        super().__init__(runner)
        self._config = config

    def set_config(self, config: DeclaredConfig) -> None:
        self._config = config

    def collect_all_selections(
        self,
        require_disk: bool = True,
        require_passwords: bool = True,
        require_system_config: bool = False,
    ) -> InstallationSelections:
        """run full interactive CLI setup."""
        _print_section_header("Declarative ArchLinux Installer (DALI) - INTERACTIVE SETUP")
        print("\nThis wizard will guide you through the installation options.")
        print("Values marked [config.yaml default] will be used if you press Enter.")
        print("Press Ctrl+C at any time to cancel.\n")

        selections = InstallationSelections()
        cfg = self._config

        # ask about installation type first
        _print_section_header("INSTALLATION TYPE")
        _print_subsection("Choose installation type")
        _print_info("Fresh install: New installation on empty or wiped disk")
        _print_info("Migration: Preserve data from existing Arch installation")
        print()
        selections.enable_migration = self.prompt_boolean(
            "Is this a migration from an existing Arch installation?",
            default=cfg.migration.enabled if cfg else False,
        )

        if require_system_config:
            _print_section_header("SYSTEM CONFIGURATION")
            selections.hostname = self.prompt_text(
                "Hostname",
                default=cfg.system.hostname if cfg else "archlinux",
                required=True,
            )
            selections.username = self.prompt_text(
                "Username",
                default=cfg.system.user.name if cfg else "user",
                required=True,
            )
            selections.timezone = self.prompt_text(
                "Timezone",
                default=cfg.system.timezone if cfg else "UTC",
                required=True,
            )
            selections.keymap = (
                self.prompt_text(
                    "Keymap",
                    default=cfg.system.locale.keymap if cfg else "us",
                    required=False,
                )
                or "us"
            )
            selections.locale = (
                self.prompt_text(
                    "Locale",
                    default=cfg.system.locale.full_locale if cfg else "en_US.UTF-8",
                    required=False,
                )
                or "en_US.UTF-8"
            )

        if require_passwords:
            _print_section_header("PASSWORD CONFIGURATION")
            selections.luks_password = self.prompt_password("LUKS encryption password")
            selections.user_password = self.prompt_password("User account password")

        if require_disk:
            selections.target_disk = self.prompt_disk_selection()
            # ask about wipe method only for fresh installs (not migration)
            if not selections.enable_migration:
                selections.wipe_method = self.prompt_wipe_method()

        selections.cpu_vendor = self.prompt_cpu_vendor()
        selections.gpu_vendor = self.prompt_gpu_vendor()
        selections.gpu_driver = self._prompt_gpu_driver(selections.gpu_vendor)

        selections.selected_desktops = self.prompt_desktop_environment()
        selections.swap_size_mb = self.prompt_swap_size()
        # only ask about hibernation if swap is enabled
        if selections.swap_size_mb > 0:
            selections.enable_hibernation = self._prompt_hibernation()
        else:
            selections.enable_hibernation = False
        selections.enable_firewall = self._prompt_firewall()
        selections.enable_snapshot_boot = self._prompt_snapshot_boot()
        selections.enable_docker = self._prompt_docker()

        # if migration enabled, ask for source LUKS password
        if selections.enable_migration:
            _print_section_header("MIGRATION - SOURCE DISK PASSWORD")
            _print_subsection("Source disk credentials")
            _print_info("If source disk is LUKS encrypted, enter its password.")
            _print_info("Leave empty if source disk is not encrypted.")
            print()
            try:
                source_pass = getpass.getpass("Source disk LUKS password (or Enter to skip): ")
                selections.source_luks_password = source_pass
            except (EOFError, KeyboardInterrupt):
                print("\nPassword entry cancelled.")
                sys.exit(1)

        self._show_summary(selections)
        self._confirm_installation()

        return selections

    def prompt_disk_selection(self) -> str:
        """interactively prompt user to select a target disk."""
        _print_section_header("DISK SELECTION")

        disks = self.get_available_disks()
        disk_paths = [d.path for d in disks]

        # check for configured default disk
        default_disk = self._config.storage.target_disk if self._config else ""
        default_idx = None
        if default_disk:
            if default_disk in disk_paths:
                default_idx = disk_paths.index(default_disk)
                _print_info(f"config.yaml default: {default_disk}")
            else:
                _print_info(f"config.yaml default '{default_disk}' not found in system")

        if not disks:
            print("\nERROR: No disks found on the system.")
            _print_info("Please ensure your storage device is connected and detected.")
            sys.exit(1)

        _print_subsection("Available disks")
        print()
        for i, disk in enumerate(disks):
            marker = " [config.yaml default]" if i == default_idx else ""
            print(f"  [{i}] {disk.path}{marker}")
            _print_info(f"Model: {disk.model}")
            _print_info(f"Size:  {disk.size}")
            if disk.partitions:
                _print_info("Partitions:")
                for part in disk.partitions:
                    print(f"          {part}")
            print()

        # get user selection
        prompt = "Select disk index to WIPE"
        if default_idx is not None:
            prompt += f" [Enter={default_idx}]"
        prompt += ": "

        while True:
            try:
                selection = input(prompt).strip()
                if not selection and default_idx is not None:
                    selected_disk = disks[default_idx]
                    break
                if not selection:
                    continue

                idx = int(selection)
                if 0 <= idx < len(disks):
                    selected_disk = disks[idx]
                    break
                else:
                    print(
                        f"Invalid selection. Please enter a number between 0 and {len(disks) - 1}"
                    )
            except ValueError:
                print("Invalid input. Please enter a number.")
            except (EOFError, KeyboardInterrupt):
                print("\nSelection cancelled.")
                sys.exit(1)

        # confirmations
        _print_subsection(f"Selected: {selected_disk.path}")
        _print_info(f"{selected_disk.model} ({selected_disk.size})")
        print("\n*** WARNING: ALL DATA ON THIS DISK WILL BE DESTROYED ***\n")

        try:
            confirm1 = input(f"Type '{selected_disk.path}' to confirm: ").strip()
            if confirm1 != selected_disk.path:
                print("Confirmation failed. Aborting.")
                sys.exit(1)

            confirm2 = input("Type 'WIPE-DISK' to proceed: ").strip()
            if confirm2 != "WIPE-DISK":
                print("Confirmation failed. Aborting.")
                sys.exit(1)
        except (EOFError, KeyboardInterrupt):
            print("\nSelection cancelled.")
            sys.exit(1)

        _print_subsection(f"Disk {selected_disk.path} selected for installation")
        return selected_disk.path

    def prompt_password(self, password_type: str, confirm: bool = True) -> str:
        """prompt for a password with optional confirmation."""
        try:
            password = getpass.getpass(f"{password_type}: ")
            if not password:
                print("Password cannot be empty.")
                sys.exit(1)

            if confirm:
                password_confirm = getpass.getpass(f"{password_type} (confirm): ")
                if password != password_confirm:
                    print("Passwords do not match.")
                    sys.exit(1)

            return password
        except (EOFError, KeyboardInterrupt):
            print("\nPassword entry cancelled.")
            sys.exit(1)

    def prompt_gpu_vendor(self) -> str:
        """prompt for GPU vendor selection."""
        _print_section_header("GPU VENDOR SELECTION")
        default = self._config.gpu.vendor if self._config else ""
        if default:
            _print_info(f"config.yaml default: {default}")
        _print_menu("GPU Vendor:", GPU_OPTIONS, default)
        return _get_selection(GPU_OPTIONS, "Select GPU vendor", default)

    def _prompt_gpu_driver(self, gpu_vendor: str) -> str:
        """prompt for GPU driver based on vendor selection."""
        default = self._config.gpu.driver if self._config else ""
        if gpu_vendor == "nvidia":
            _print_menu("NVIDIA Driver:", NVIDIA_DRIVER_OPTIONS, default)
            return _get_selection(NVIDIA_DRIVER_OPTIONS, "Select NVIDIA driver", default)
        elif gpu_vendor == "amd":
            _print_subsection("AMD driver stack (open-source)")
            _print_info("mesa (OpenGL/Vulkan)")
            _print_info("vulkan-radeon (Vulkan)")
            _print_info("libva-mesa-driver (hardware video acceleration)")
            return "amdgpu"
        elif gpu_vendor == "intel":
            _print_subsection("Intel driver stack (open-source)")
            _print_info("mesa (OpenGL/Vulkan)")
            _print_info("vulkan-intel (Vulkan)")
            _print_info("intel-media-driver (hardware video acceleration)")
            return "i915"
        else:
            return ""

    def prompt_nvidia_driver(self) -> str:
        """prompt for NVIDIA driver selection."""
        default = self._config.gpu.driver if self._config else ""
        _print_menu("NVIDIA Driver:", NVIDIA_DRIVER_OPTIONS, default)
        return _get_selection(NVIDIA_DRIVER_OPTIONS, "Select NVIDIA driver", default)

    def prompt_cpu_vendor(self) -> str:
        """prompt for CPU vendor selection."""
        _print_section_header("CPU VENDOR SELECTION")
        default = self._config.system.cpu_vendor if self._config else ""
        if default:
            _print_info(f"config.yaml default: {default}")
        _print_menu("CPU Vendor:", CPU_OPTIONS, default)
        return _get_selection(CPU_OPTIONS, "Select CPU vendor", default)

    def prompt_desktop_environment(self) -> list[str]:
        """prompt for desktop environment selection."""
        _print_section_header("DESKTOP ENVIRONMENT SELECTION")

        configured = _get_configured_desktops(self._config)

        # if only one desktop configured, just confirm it
        if len(configured) == 1:
            desktop = configured[0]
            _print_subsection(f"Single desktop configured: {desktop}")
            print("\nPress Enter to confirm, or type 'n' to select different desktop.")
            try:
                response = input("Confirm [Enter/n]: ").strip().lower()
                if response in ("", "y", "yes"):
                    return [desktop]
            except (EOFError, KeyboardInterrupt):
                print("\nSelection cancelled.")
                sys.exit(1)

        # show configured desktops if multiple
        if len(configured) > 1:
            _print_info(f"config.yaml has: {', '.join(configured)}")
            print("\nYou can install one or more. Options:")
            print("  - Enter to install all configured desktops")
            print("  - Select specific desktop(s) from menu below")

        _print_menu("Desktop Environment:", DESKTOP_OPTIONS)
        selection = _get_selection(DESKTOP_OPTIONS, "Select desktop environment")

        if selection == "all":
            return ["gnome", "kde", "hyprland"]
        elif selection == "none":
            return []
        else:
            return [selection]

    def prompt_swap_size(self) -> int:
        """prompt for swap file size."""
        _print_section_header("SWAP SIZE SELECTION")
        default_size = self._config.storage.swap.size_mb if self._config else 0
        default_str = str(default_size) if default_size else ""
        if default_size:
            _print_info(f"config.yaml default: {default_size} MB ({default_size // 1024} GB)")

        # build options dynamically to include config default
        swap_options = _build_swap_options(default_size)
        _print_menu("Swap Size:", swap_options, default_str)
        selection = _get_selection(swap_options, "Select swap size", default_str)

        if selection == "ram":
            return self.get_system_ram_mb()
        elif selection == "custom":
            return self._prompt_custom_swap_size()
        else:
            return int(selection)

    def prompt_boolean(self, prompt_text: str, default: bool = False) -> bool:
        """prompt user for yes/no answer."""
        default_str = "Y/n" if default else "y/N"
        while True:
            try:
                response = input(f"{prompt_text} ({default_str}): ").strip().lower()
                if response == "":
                    return default
                if response in ("y", "yes"):
                    return True
                elif response in ("n", "no"):
                    return False
                else:
                    print("Please enter 'y' or 'n'.")
            except (EOFError, KeyboardInterrupt):
                print("\nSelection cancelled.")
                sys.exit(1)

    def prompt_text(
        self,
        prompt_text: str,
        default: str = "",
        required: bool = False,
    ) -> str:
        """prompt user for text input."""
        default_display = f" [{default}]" if default else ""
        while True:
            try:
                response = input(f"{prompt_text}{default_display}: ").strip()
                if response:
                    return response
                elif default:
                    return default
                elif required:
                    print("This field is required.")
                else:
                    return ""
            except (EOFError, KeyboardInterrupt):
                print("\nInput cancelled.")
                sys.exit(1)

    def prompt_secrets_key(self) -> str:
        """prompt for the symmetric key to decrypt encrypted secrets."""
        _print_section_header("ENCRYPTED SECRETS DECRYPTION")
        _print_subsection("Your config contains encrypted passwords")
        _print_info("Enter the symmetric key to decrypt them.")
        print()

        try:
            key = getpass.getpass("Secrets decryption key: ")
            if not key:
                print("Key cannot be empty.")
                sys.exit(1)
            return key
        except (EOFError, KeyboardInterrupt):
            print("\nKey entry cancelled.")
            sys.exit(1)

    def _prompt_hibernation(self) -> bool:
        """prompt for hibernation enablement."""
        _print_section_header("HIBERNATION CONFIGURATION")
        default = self._config.storage.swap.hibernation.enabled if self._config else False
        if default:
            _print_info("config.yaml default: enabled")
        _print_subsection("About hibernation")
        _print_info("Saves session to disk on shutdown, restores on next boot.")
        _print_info("Requires swap size >= RAM size for reliable operation.")
        print()
        return self.prompt_boolean("Enable hibernation?", default=default)

    def _prompt_firewall(self) -> bool:
        """prompt for firewall enablement."""
        _print_section_header("FIREWALL CONFIGURATION")
        default = self._config.firewall.enabled if self._config else True
        if default:
            _print_info("config.yaml default: enabled")
        _print_subsection("About UFW")
        _print_info("Uncomplicated Firewall provides simple firewall management.")
        _print_info("Recommended for most users.")
        print()
        return self.prompt_boolean("Enable UFW firewall?", default=default)

    def _prompt_snapshot_boot(self) -> bool:
        """prompt for bootable snapshot feature."""
        _print_section_header("BOOTABLE SNAPSHOTS CONFIGURATION")
        default = self._config.boot.enable_snapshot_boot if self._config else False
        if default:
            _print_info("config.yaml default: enabled")
        _print_subsection("About bootable snapshots")
        _print_info("Boot into previous system states from the boot menu.")
        _print_info("Useful for system recovery after failed updates.")
        print()
        return self.prompt_boolean("Enable bootable snapshots?", default=default)

    def _prompt_docker(self) -> bool:
        """prompt for Docker installation."""
        _print_section_header("DOCKER CONFIGURATION")
        default = self._config.docker.enabled if self._config else False
        if default:
            _print_info("config.yaml default: enabled")
        _print_subsection("About Docker")
        _print_info("Container platform for application isolation.")
        _print_info("Includes: docker package, user group access, btrfs storage.")
        print()
        return self.prompt_boolean("Enable Docker?", default=default)

    def prompt_wipe_method(self) -> str:
        """prompt for disk wipe method selection."""
        _print_section_header("DISK WIPE METHOD")
        default = (
            self._config.storage.wipe_method
            if self._config and hasattr(self._config.storage, "wipe_method")
            else "quick"
        )
        if default:
            _print_info(f"config.yaml default: {default}")
        _print_subsection("About disk wipe methods")
        _print_info("Quick: Zap partition table only (fast)")
        _print_info("Secure: Fill with random data via LUKS (slow, best for encryption)")
        _print_info("Discard: SSD blkdiscard (fast, but leaks usage patterns)")
        _print_info("Skip: Keep existing partitions (if you are recovering a partial install)")
        print()
        _print_menu("Wipe Method:", WIPE_OPTIONS, default)
        return _get_selection(WIPE_OPTIONS, "Select wipe method", default)

    def _prompt_custom_swap_size(self) -> int:
        """prompt for custom swap size in MB."""
        while True:
            try:
                size_str = input("Enter swap size in MB (e.g., 4096): ").strip()
                if not size_str:
                    continue

                size = int(size_str)
                if size < 0:
                    print("Size must be non-negative.")
                    continue
                return size
            except ValueError:
                print("Invalid input. Please enter a number.")
            except (EOFError, KeyboardInterrupt):
                print("\nSelection cancelled.")
                sys.exit(1)

    def _show_summary(self, selections: InstallationSelections) -> None:
        """show configuration summary with all important fields."""
        cfg = self._config
        _print_section_header("CONFIGURATION SUMMARY")
        print()

        # system section
        _print_subsection("System")
        hostname = selections.hostname or (cfg.system.hostname if cfg else "")
        username = selections.username or (cfg.system.user.name if cfg else "")
        timezone = selections.timezone or (cfg.system.timezone if cfg else "")
        _print_info(f"Hostname:          {hostname}")
        _print_info(f"Username:          {username}")
        _print_info(f"Timezone:          {timezone}")
        if selections.locale:
            locales = self._get_distinct_locales()
            _print_info(
                f"Locale(s):         {', '.join(locales) if locales else selections.locale}"
            )
        if selections.keymap:
            _print_info(f"Keymap:            {selections.keymap}")

        # storage section
        _print_subsection("Storage")
        _print_info(f"Target Disk:       {selections.target_disk}")
        _print_info(f"Wipe Method:       {selections.wipe_method}")
        if selections.swap_size_mb > 0:
            _print_info(f"Swap Size:         {selections.swap_size_mb} MB")
        else:
            _print_info("Swap:              Disabled")

        # hardware section
        _print_subsection("Hardware")
        _print_info(f"CPU Vendor:        {selections.cpu_vendor}")
        _print_info(f"GPU Vendor:        {selections.gpu_vendor}")
        if selections.gpu_driver:
            _print_info(f"GPU Driver:        {selections.gpu_driver}")

        # packages section
        _print_subsection("Packages")
        desktops = selections.selected_desktops or []
        _print_info(f"Desktop(s):        {', '.join(desktops) if desktops else 'None'}")

        # features section
        _print_subsection("Features")
        enabled = "Enabled"
        disabled = "Disabled"
        _print_info(f"Hibernation:       {enabled if selections.enable_hibernation else disabled}")
        _print_info(f"Firewall (UFW):    {enabled if selections.enable_firewall else disabled}")
        _print_info(
            f"Bootable Snapshots: {enabled if selections.enable_snapshot_boot else disabled}"
        )
        _print_info(f"Docker:            {enabled if selections.enable_docker else disabled}")
        _print_info(f"Migration:         {enabled if selections.enable_migration else disabled}")

        print(f"\n{SUBSECTION_CHAR * MENU_WIDTH}")

    def _get_distinct_locales(self) -> list[str]:
        """get all distinct locales from config."""
        if not self._config:
            return []
        locale = self._config.system.locale
        locales = {locale.full_locale}
        for val in [locale.monetary, locale.time_format, locale.numeric, locale.paper]:
            if val and val != locale.full_locale:
                locales.add(val)
        return sorted(locales)

    def _confirm_installation(self) -> None:
        """confirm before proceeding with installation."""
        while True:
            try:
                response = input("Proceed with installation? (y/n): ").strip().lower()
                if response in ("y", "yes"):
                    return
                elif response in ("n", "no"):
                    print("Installation cancelled.")
                    sys.exit(0)
                else:
                    print("Please enter 'y' or 'n'.")
            except (EOFError, KeyboardInterrupt):
                print("\nInstallation cancelled.")
                sys.exit(1)
