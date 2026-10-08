"""interactive installation setup.

each screen shows the value inherited from config.yaml and the environment as its
default: pressing Enter keeps it, or the user picks another. the flow starts from the
resolved InstallerConfig and returns a new one with the answers applied.
"""

import curses
from dataclasses import dataclass, field, replace

from arch_installer.config.models import (
    CpuVendor,
    Desktop,
    GpuDriver,
    GpuVendor,
    InstallerConfig,
    WipeMethod,
)
from arch_installer.core.command import CommandRunner, SystemCommandRunner
from arch_installer.tui.widgets import (
    MenuOption,
    checkbox_menu,
    confirm_screen,
    info_screen,
    init_colors,
    password_input_with_confirm,
    radio_menu,
    text_input,
    toggle_menu,
)

GPU_OPTIONS = [
    MenuOption(GpuVendor.AMD, "AMD (AMDGPU, open-source)"),
    MenuOption(GpuVendor.INTEL, "Intel (integrated graphics)"),
    MenuOption(GpuVendor.NVIDIA, "NVIDIA (proprietary/nouveau)"),
    MenuOption(GpuVendor.NONE, "None (VM or generic)"),
]
NVIDIA_DRIVER_OPTIONS = [
    MenuOption(GpuDriver.NOUVEAU, "Nouveau - open-source, limited features"),
    MenuOption(GpuDriver.NVIDIA_OPEN, "NVIDIA Open - official open kernel modules, RTX 20+"),
    MenuOption(GpuDriver.NVIDIA_DKMS, "NVIDIA DKMS - proprietary, best compatibility"),
]
CPU_OPTIONS = [
    MenuOption(CpuVendor.INTEL, "Intel"),
    MenuOption(CpuVendor.AMD, "AMD"),
]
DESKTOP_OPTIONS = [
    MenuOption(Desktop.GNOME, "GNOME (Wayland, modern)"),
    MenuOption(Desktop.KDE, "KDE Plasma (Wayland, customizable)"),
    MenuOption(Desktop.HYPRLAND, "Hyprland (Wayland tiling WM)"),
]
WIPE_OPTIONS = [
    MenuOption(WipeMethod.QUICK, "Quick wipe (partition table only)"),
    MenuOption(WipeMethod.SECURE, "Secure wipe (random fill - slow)"),
    MenuOption(WipeMethod.DISCARD, "SSD discard (blkdiscard - fast)"),
    MenuOption(WipeMethod.SKIP, "Skip wipe (recovering partial install)"),
]
SWAP_PRESETS_MB = (4096, 8192, 16384, 32768, 65536)
NO_SWAP = MenuOption("0", "No swap")
KEEP_PASSWORD = "keep"
PASSWORD_OPTIONS = [
    MenuOption(KEEP_PASSWORD, "Keep the inherited password"),
    MenuOption("replace", "Enter a new password"),
]


@dataclass
class DiskInfo:
    path: str
    model: str
    size: str


@dataclass
class Answers:
    config: InstallerConfig
    luks_password: str = ""
    user_password: str = ""
    source_luks_password: str = ""
    selected_desktops: list[str] = field(default_factory=list)


class HardwareDetector:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def list_disks(self) -> list[DiskInfo]:
        result = self._runner.run(["lsblk", "-dno", "NAME,TYPE"], raise_on_nonzero_exit=False)
        if not result.success:
            return []
        disks = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "disk":
                path = f"/dev/{parts[0]}"
                model, size = self._disk_details(path)
                disks.append(DiskInfo(path, model, size))
        return disks

    def _disk_details(self, path: str) -> tuple[str, str]:
        result = self._runner.run(
            ["lsblk", "-dno", "MODEL,SIZE", path], raise_on_nonzero_exit=False
        )
        if not result.success:
            return "unknown", "unknown"
        # the model may contain spaces, the size never does
        parts = result.stdout.strip().rsplit(maxsplit=1)
        if len(parts) < 2:
            return "unknown", parts[0] if parts else "unknown"
        return parts[0], parts[1]


def _collect(window: curses.window, config: InstallerConfig, runner: CommandRunner) -> Answers:
    init_colors()
    curses.curs_set(0)
    hardware = HardwareDetector(runner)
    answers = Answers(config=config)

    info_screen(
        window,
        "DALI - Declarative Arch Linux Installer",
        "Interactive setup.\n\n"
        "Each screen starts on the value inherited from config.yaml and the\n"
        "environment. Press Enter to keep it, or choose another.\n\n"
        "  Arrows  navigate      Enter  confirm\n"
        "  Space   toggle        Ctrl+C quit",
    )

    migration = (
        radio_menu(
            window,
            "Installation Type",
            [
                MenuOption("fresh", "Fresh installation"),
                MenuOption("migration", "Migration from existing Arch"),
            ],
            inherited_value="migration" if config.migration.enabled else "fresh",
        )
        == "migration"
    )
    answers.config = replace(answers.config, migration=replace(config.migration, enabled=migration))

    answers.config = _prompt_system(window, answers.config)

    credentials = config.credentials
    answers.luks_password = _prompt_password(
        window, "Password Setup", "LUKS encryption password", credentials.luks_password
    )
    answers.user_password = _prompt_password(
        window, "Password Setup", "User account password", credentials.user_password
    )

    answers.config = _prompt_storage(window, answers.config, hardware)
    answers.config = _prompt_usb_boot(window, answers.config)
    answers.config = _prompt_hardware(window, answers.config)
    answers.selected_desktops = _prompt_desktops(window, answers.config)
    answers.config = _prompt_swap(window, answers.config)
    answers.config = _prompt_features(window, answers.config)

    if migration:
        answers.source_luks_password = _prompt_password(
            window,
            "Migration - Source Disk",
            "Source disk LUKS password",
            credentials.source_luks_password,
            confirm_new=False,
        )

    if not confirm_screen(window, "Configuration Summary", _summary(answers)):
        raise KeyboardInterrupt("installation cancelled by user")
    return answers


def _prompt_system(window: curses.window, config: InstallerConfig) -> InstallerConfig:
    system = config.system
    hostname = text_input(
        window, "System Configuration", "Hostname:", inherited=system.hostname, required=True
    )
    username = text_input(
        window, "System Configuration", "Username:", inherited=system.user.name, required=True
    )
    timezone = text_input(
        window,
        "System Configuration",
        "Timezone (e.g. Europe/Helsinki):",
        inherited=system.timezone,
        required=True,
    )
    keymap = (
        text_input(window, "System Configuration", "Keymap:", inherited=system.locale.keymap)
        or system.locale.keymap
    )
    return replace(
        config,
        system=replace(
            system,
            hostname=hostname,
            timezone=timezone,
            user=replace(system.user, name=username),
            locale=replace(system.locale, keymap=keymap),
        ),
    )


# an inherited password is kept or replaced; it is never shown
def _prompt_password(
    window: curses.window, title: str, name: str, inherited: str, confirm_new: bool = True
) -> str:
    if inherited:
        choice = radio_menu(
            window,
            title,
            PASSWORD_OPTIONS,
            description=f"{name}: inherited from the encrypted secrets or the environment.",
        )
        if choice == KEEP_PASSWORD:
            return inherited
    if confirm_new:
        return password_input_with_confirm(window, title, f"{name}:")
    return text_input(window, title, f"{name}:", required=True, masked=True)


def _prompt_storage(
    window: curses.window, config: InstallerConfig, hardware: HardwareDetector
) -> InstallerConfig:
    storage = config.storage
    disks = hardware.list_disks()
    if disks:
        options = [
            MenuOption(disk.path, f"{disk.path}  {disk.model}  ({disk.size})") for disk in disks
        ]
        target_disk = radio_menu(
            window,
            "Disk Selection",
            options,
            inherited_value=storage.target_disk,
            description="Select target disk (ALL DATA WILL BE ERASED):",
        )
    else:
        target_disk = text_input(
            window,
            "Disk Selection",
            "Enter disk path (e.g. /dev/sda):",
            inherited=storage.target_disk,
            required=True,
        )

    wipe_method = WipeMethod(
        radio_menu(window, "Disk Wipe Method", WIPE_OPTIONS, inherited_value=storage.wipe_method)
    )
    return replace(
        config,
        storage=replace(config.storage, target_disk=target_disk, wipe_method=wipe_method),
    )


# the inherited size is always offered, even when it is not one of the presets
def swap_size_options(inherited_size_mb: int) -> list[MenuOption]:
    sizes_mb = sorted({*SWAP_PRESETS_MB, inherited_size_mb} - {0})
    return [MenuOption(str(size_mb), _swap_label(size_mb)) for size_mb in sizes_mb] + [NO_SWAP]


def _swap_label(size_mb: int) -> str:
    if size_mb % 1024 == 0:
        return f"{size_mb // 1024} GB"
    return f"{size_mb} MB"


def _prompt_swap(window: curses.window, config: InstallerConfig) -> InstallerConfig:
    swap = config.storage.swap
    inherited_size_mb = swap.size_mb if swap.enabled else 0
    swap_size_mb = int(
        radio_menu(
            window,
            "Swap Size",
            swap_size_options(inherited_size_mb),
            inherited_value=str(inherited_size_mb),
        )
    )
    return replace(
        config,
        storage=replace(
            config.storage,
            swap=replace(
                config.storage.swap,
                enabled=swap_size_mb > 0,
                size_mb=swap_size_mb or config.storage.swap.size_mb,
            ),
        ),
    )


def _prompt_usb_boot(window: curses.window, config: InstallerConfig) -> InstallerConfig:
    enabled = (
        radio_menu(
            window,
            "USB Boot Drive",
            [
                MenuOption("no", "No - boot from the internal disk"),
                MenuOption("yes", "Yes - EFI and LUKS header on USB"),
            ],
            inherited_value="yes" if config.usb_boot.enabled else "no",
            description="Store the EFI partition and the detached LUKS header on a USB drive.",
        )
        == "yes"
    )
    device = config.usb_boot.device
    if enabled:
        device = text_input(
            window,
            "USB Boot Device",
            "Enter USB device path (e.g. /dev/sdb):",
            inherited=device,
            required=True,
        )
    return replace(config, usb_boot=replace(config.usb_boot, enabled=enabled, device=device))


def _prompt_hardware(window: curses.window, config: InstallerConfig) -> InstallerConfig:
    cpu_vendor = CpuVendor(
        radio_menu(window, "CPU Vendor", CPU_OPTIONS, inherited_value=config.system.cpu_vendor)
    )
    gpu_vendor = GpuVendor(
        radio_menu(window, "GPU Vendor", GPU_OPTIONS, inherited_value=config.gpu.vendor)
    )
    driver = config.gpu.driver
    if gpu_vendor == GpuVendor.NVIDIA:
        driver = GpuDriver(
            radio_menu(
                window,
                "NVIDIA Driver",
                NVIDIA_DRIVER_OPTIONS,
                inherited_value=config.gpu.driver or GpuDriver.NVIDIA_DKMS,
            )
        )
    else:
        driver = GpuDriver.VENDOR_DEFAULT
    return replace(
        config,
        system=replace(config.system, cpu_vendor=cpu_vendor),
        gpu=replace(config.gpu, vendor=gpu_vendor, driver=driver),
    )


def _prompt_desktops(window: curses.window, config: InstallerConfig) -> list[str]:
    return checkbox_menu(
        window,
        "Desktop Environments",
        DESKTOP_OPTIONS,
        inherited_values=[str(desktop) for desktop in config.packages.desktops_to_install],
        description="Select desktop environments to install (Space to toggle):",
    )


def _prompt_features(window: curses.window, config: InstallerConfig) -> InstallerConfig:
    toggles = [
        ("hibernation", "Hibernation (needs swap)", config.storage.swap.hibernation),
        ("firewall", "Firewall (UFW)", config.firewall.enabled),
        ("snapshots", "Bootable snapshots", config.boot.enable_snapshot_boot),
        ("docker", "Docker", config.docker.enabled),
        ("notifications", "Desktop notifications", config.notifications.enabled),
    ]
    chosen = dict(
        toggle_menu(
            window, "Features", toggles, description="Toggle with Space, then Tab to continue:"
        )
    )
    swap_enabled = config.storage.swap.enabled
    return replace(
        config,
        storage=replace(
            config.storage,
            swap=replace(config.storage.swap, hibernation=chosen["hibernation"] and swap_enabled),
        ),
        firewall=replace(config.firewall, enabled=chosen["firewall"]),
        boot=replace(config.boot, enable_snapshot_boot=chosen["snapshots"]),
        docker=replace(config.docker, enabled=chosen["docker"]),
        notifications=replace(config.notifications, enabled=chosen["notifications"]),
    )


def _summary(answers: Answers) -> list[tuple[str, str]]:
    config = answers.config
    on_off = {True: "enabled", False: "disabled"}
    desktops = ", ".join(answers.selected_desktops) or "none"
    return [
        ("System.Hostname", config.system.hostname),
        ("System.Username", config.system.user.name),
        ("System.Timezone", config.system.timezone),
        ("System.Keymap", config.system.locale.keymap),
        ("Storage.Target Disk", config.storage.target_disk),
        ("Storage.Wipe Method", str(config.storage.wipe_method)),
        (
            "Storage.Swap",
            f"{config.storage.swap.size_mb} MB" if config.storage.swap.enabled else "disabled",
        ),
        ("Hardware.CPU", str(config.system.cpu_vendor) or "unknown"),
        ("Hardware.GPU", str(config.gpu.vendor)),
        ("Packages.Desktops", desktops),
        ("Features.Hibernation", on_off[config.storage.swap.hibernation]),
        ("Features.Firewall", on_off[config.firewall.enabled]),
        ("Features.Bootable Snapshots", on_off[config.boot.enable_snapshot_boot]),
        ("Features.Docker", on_off[config.docker.enabled]),
        ("Features.Migration", on_off[config.migration.enabled]),
        ("USB Boot.Enabled", on_off[config.usb_boot.enabled]),
    ]


def _apply_answers(answers: Answers) -> InstallerConfig:
    config = answers.config
    credentials = replace(
        config.credentials,
        luks_password=answers.luks_password or config.credentials.luks_password,
        user_password=answers.user_password or config.credentials.user_password,
        source_luks_password=answers.source_luks_password
        or config.credentials.source_luks_password,
    )
    selected = tuple(Desktop(name) for name in answers.selected_desktops)
    return replace(
        config,
        credentials=credentials,
        packages=replace(config.packages, selected_desktops=selected),
    )


# quitting raises KeyboardInterrupt, which the caller reports as a cancellation
def run_tui_setup(config: InstallerConfig, runner: CommandRunner | None = None) -> InstallerConfig:
    answers = curses.wrapper(_collect, config, runner or SystemCommandRunner())
    return _apply_answers(answers)
