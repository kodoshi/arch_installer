"""Configuration recap display for installation confirmation.

displays a comprehensive summary of all configuration choices before
proceeding with installation. used by both CLI and GUI modes.
"""

from dataclasses import dataclass

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.runtime_state import RuntimeConfig


@dataclass
class ConfigRecapItem:
    category: str
    label: str
    value: str
    is_enabled: bool = True


def _format_enabled(enabled: bool) -> str:
    return "Enabled" if enabled else "Disabled"


def _format_list(items: tuple[str, ...] | list[str] | None) -> str:
    if not items:
        return "None"
    return ", ".join(items)


def build_config_recap(
    config: DeclaredConfig,
    runtime: RuntimeConfig,
) -> list[ConfigRecapItem]:
    """build a list of configuration items for display."""
    items: list[ConfigRecapItem] = []

    # system section
    items.append(ConfigRecapItem("System", "Hostname", config.system.hostname))
    items.append(ConfigRecapItem("System", "Username", config.system.user.name))
    items.append(ConfigRecapItem("System", "Timezone", config.system.timezone))
    items.append(ConfigRecapItem("System", "Locale", config.system.locale.full_locale))
    items.append(ConfigRecapItem("System", "Keymap", config.system.locale.keymap))

    # storage section
    disk = runtime.target_disk or config.storage.target_disk or "<will be prompted>"
    items.append(ConfigRecapItem("Storage", "Target Disk", disk))
    items.append(ConfigRecapItem("Storage", "EFI Size", f"{config.storage.efi_size_mb} MiB"))
    items.append(ConfigRecapItem("Storage", "LUKS Encryption", config.storage.luks.type))
    items.append(ConfigRecapItem("Storage", "BTRFS Label", config.storage.btrfs.label))

    # swap
    if runtime.skip_swap:
        items.append(ConfigRecapItem("Storage", "Swap", "Disabled"))
    elif config.storage.swap.enabled:
        swap_size = runtime.swap_size_mb or config.storage.swap.size_mb
        items.append(ConfigRecapItem("Storage", "Swap Size", f"{swap_size} MB"))
        items.append(
            ConfigRecapItem(
                "Storage", "Hibernation", _format_enabled(config.storage.swap.hibernation.enabled)
            )
        )
    else:
        items.append(ConfigRecapItem("Storage", "Swap", "Disabled"))

    # boot section
    kernels = runtime.selected_kernels or tuple(k.name for k in config.boot.kernels)
    items.append(ConfigRecapItem("Boot", "Kernels", _format_list(kernels)))
    items.append(
        ConfigRecapItem(
            "Boot", "Secure Boot", _format_enabled(config.boot.secure_boot.enroll_keys)
        )
    )
    items.append(ConfigRecapItem("Boot", "Loader Timeout", f"{config.boot.loader.timeout}s"))

    # packages
    profile = runtime.package_profile or config.packages.profile
    items.append(ConfigRecapItem("Packages", "Profile", profile))

    desktops = runtime.selected_desktops or []
    items.append(ConfigRecapItem("Packages", "Desktops", _format_list(desktops) or "None"))

    # hardware
    cpu_vendor = runtime.cpu_vendor or "auto-detect"
    gpu_vendor = runtime.gpu_vendor or config.gpu.vendor or "none"
    gpu_driver = runtime.gpu_driver or config.gpu.driver or "default"
    items.append(ConfigRecapItem("Hardware", "CPU Vendor", cpu_vendor))
    items.append(ConfigRecapItem("Hardware", "GPU Vendor", gpu_vendor))
    items.append(ConfigRecapItem("Hardware", "GPU Driver", gpu_driver))

    # features
    items.append(
        ConfigRecapItem("Features", "Snapper Snapshots", _format_enabled(config.snapper.enabled))
    )
    items.append(
        ConfigRecapItem(
            "Features",
            "Bootable Snapshots",
            _format_enabled(runtime.enable_snapshot_boot),
        )
    )
    items.append(
        ConfigRecapItem("Features", "Firewall (UFW)", _format_enabled(runtime.enable_firewall))
    )
    if runtime.enable_firewall and config.firewall.enabled:
        items.append(
            ConfigRecapItem(
                "Features", "  SSH Access", _format_enabled(config.firewall.ssh.enabled)
            )
        )
        if config.firewall.ssh.enabled:
            items.append(ConfigRecapItem("Features", "  SSH Port", str(config.firewall.ssh.port)))

    items.append(ConfigRecapItem("Features", "Docker", _format_enabled(config.docker.enabled)))
    items.append(
        ConfigRecapItem("Features", "Dotfiles Sync", _format_enabled(config.dotfiles.enabled))
    )

    # migration
    if config.migration.enabled:
        items.append(ConfigRecapItem("Migration", "Source Disk", config.migration.source_disk))
        items.append(
            ConfigRecapItem(
                "Migration", "Preserve Home", _format_enabled(config.migration.preserve_home)
            )
        )

    return items


def print_config_recap(
    config: DeclaredConfig,
    runtime: RuntimeConfig,
) -> None:
    """print a formatted configuration recap to stdout."""
    items = build_config_recap(config, runtime)

    print("\n" + "=" * 70)
    print("                    CONFIGURATION RECAP")
    print("=" * 70)

    current_category = ""
    for item in items:
        if item.category != current_category:
            print(f"\n  [{item.category}]")
            current_category = item.category

        print(f"    {item.label:.<30} {item.value}")

    print("\n" + "-" * 70)


def confirm_config_recap() -> bool:
    """prompt user to confirm the configuration."""
    print("\nPlease review the configuration above carefully.")
    print("The target disk will be COMPLETELY ERASED.\n")

    while True:
        try:
            response = input("Proceed with installation? (yes/no): ").strip().lower()
            if response in ("yes", "y"):
                return True
            elif response in ("no", "n"):
                return False
            else:
                print("Please type 'yes' or 'no'.")
        except (EOFError, KeyboardInterrupt):
            print("\nInstallation cancelled.")
            return False


def is_config_complete_for_non_interactive(
    config: DeclaredConfig,
    runtime: RuntimeConfig,
) -> bool:
    """check if configuration is complete enough for non-interactive mode.

    returns True if all required fields are present and no prompts would be needed.
    """
    # must have target disk
    if not (runtime.target_disk or config.storage.target_disk):
        return False

    # must have cpu vendor
    if not runtime.cpu_vendor:
        return False

    # must have gpu vendor (can be 'none')
    if not (runtime.gpu_vendor or config.gpu.vendor):
        return False

    # must have passwords (either via env or config)
    if not runtime.luks_password:
        return False
    if not runtime.user_password:
        return False

    return True
