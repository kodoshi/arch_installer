"""interactive installation setup.

the TUI receives the inherited values (environment variables over config.yaml, each with
its source) and has the last word: every screen starts on the inherited value and shows
where it came from, Enter keeps it, any other choice replaces it. a setting without an
inherited value starts with nothing selected. the TUI returns only the values it asked for.
"""

import curses
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from arch_installer.config.models import CpuVendor, Desktop, GpuDriver, GpuVendor, WipeMethod
from arch_installer.config.value_precedence import SettingValue, apply_tui_choices
from arch_installer.core.command import CommandRunner, SystemCommandRunner
from arch_installer.tui.widgets import (
    Inherited,
    MenuOption,
    Toggle,
    checkbox_menu,
    confirm_screen,
    info_screen,
    init_colors,
    password_input_with_confirm,
    radio_menu,
    text_input,
    toggle_menu,
)


# the settings the TUI asks about, by their path in the configuration model
class AskedSetting(StrEnum):
    MIGRATION = "migration.enabled"
    HOSTNAME = "system.hostname"
    USERNAME = "system.user.name"
    TIMEZONE = "system.timezone"
    KEYMAP = "system.locale.keymap"
    LUKS_PASSWORD = "credentials.luks_password"
    USER_PASSWORD = "credentials.user_password"
    SOURCE_LUKS_PASSWORD = "credentials.source_luks_password"
    TARGET_DISK = "storage.target_disk"
    WIPE_METHOD = "storage.wipe_method"
    USB_BOOT = "usb_boot.enabled"
    USB_BOOT_DEVICE = "usb_boot.device"
    CPU_VENDOR = "system.cpu_vendor"
    GPU_VENDOR = "gpu.vendor"
    GPU_DRIVER = "gpu.driver"
    DESKTOPS = "packages.selected_desktops"
    SWAP = "storage.swap.enabled"
    SWAP_SIZE_MB = "storage.swap.size_mb"
    HIBERNATION = "storage.swap.hibernation"
    FIREWALL = "firewall.enabled"
    SNAPSHOT_BOOT = "boot.enable_snapshot_boot"
    DOCKER = "docker.enabled"
    NOTIFICATIONS = "notifications.enabled"


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
INSTALLATION_TYPE_OPTIONS = [
    MenuOption("fresh", "Fresh installation"),
    MenuOption("migration", "Migration from existing Arch"),
]
USB_BOOT_OPTIONS = [
    MenuOption("no", "No - boot from the internal disk"),
    MenuOption("yes", "Yes - EFI and LUKS header on USB"),
]
SWAP_PRESETS_MB = (4096, 8192, 16384, 32768, 65536)
NO_SWAP = MenuOption("0", "No swap")
KEEP_PASSWORD = "keep"
PASSWORD_OPTIONS = [
    MenuOption(KEEP_PASSWORD, "Keep the inherited password"),
    MenuOption("replace", "Enter a new password"),
]
FEATURE_TOGGLES = (
    (AskedSetting.HIBERNATION, "Hibernation (needs swap)"),
    (AskedSetting.FIREWALL, "Firewall (UFW)"),
    (AskedSetting.SNAPSHOT_BOOT, "Bootable snapshots"),
    (AskedSetting.DOCKER, "Docker"),
    (AskedSetting.NOTIFICATIONS, "Desktop notifications"),
)


@dataclass
class DiskInfo:
    path: str
    model: str
    size: str


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


# YAML gives plain strings, environment variables give enums: both read the same as text
def _as_text(value: Any) -> str:
    return str(value)


class SetupSession:
    def __init__(
        self,
        window: curses.window,
        inherited: Mapping[str, SettingValue],
        hardware: HardwareDetector,
    ) -> None:
        self._window = window
        self._inherited = inherited
        self._hardware = hardware
        self.choices: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        info_screen(
            self._window,
            "DALI - Declarative Arch Linux Installer",
            "Interactive setup.\n\n"
            "Each screen starts on the value inherited from the environment or\n"
            "config.yaml and shows where it came from. Press Enter to keep it,\n"
            "or choose another.\n\n"
            "  Arrows  navigate      Enter  confirm\n"
            "  Space   toggle        Ctrl+C quit",
        )
        self._ask_installation_type()
        self._ask_system()
        self._ask_password(AskedSetting.LUKS_PASSWORD, "Password Setup", "LUKS encryption password")
        self._ask_password(AskedSetting.USER_PASSWORD, "Password Setup", "User account password")
        self._ask_storage()
        self._ask_usb_boot()
        self._ask_hardware()
        self._ask_desktops()
        self._ask_swap()
        self._ask_features()
        if self._current(AskedSetting.MIGRATION):
            self._ask_password(
                AskedSetting.SOURCE_LUKS_PASSWORD,
                "Migration - Source Disk",
                "Source disk LUKS password",
                confirm_new=False,
            )
        if not confirm_screen(self._window, "Configuration Summary", self._summary()):
            raise KeyboardInterrupt("installation cancelled by user")
        return self.choices

    def _current(self, setting: AskedSetting) -> Any:
        if setting in self.choices:
            return self.choices[setting]
        setting_value = self._inherited.get(setting)
        return setting_value.value if setting_value else None

    # an empty inherited text (no USB device, the vendor's default GPU driver) offers nothing
    def _inherited_text(self, setting: AskedSetting) -> Inherited[str] | None:
        setting_value = self._inherited.get(setting)
        if setting_value is None or setting_value.value in (None, ""):
            return None
        return Inherited(_as_text(setting_value.value), str(setting_value.source))

    def _inherited_switch(self, setting: AskedSetting, on: str, off: str) -> Inherited[str] | None:
        setting_value = self._inherited.get(setting)
        if setting_value is None:
            return None
        return Inherited(on if setting_value.value else off, str(setting_value.source))

    def _inherited_flag(self, setting: AskedSetting) -> Inherited[bool] | None:
        setting_value = self._inherited.get(setting)
        if setting_value is None:
            return None
        return Inherited(bool(setting_value.value), str(setting_value.source))

    def _ask_installation_type(self) -> None:
        chosen = radio_menu(
            self._window,
            "Installation Type",
            INSTALLATION_TYPE_OPTIONS,
            inherited=self._inherited_switch(AskedSetting.MIGRATION, "migration", "fresh"),
        )
        self.choices[AskedSetting.MIGRATION] = chosen == "migration"

    def _ask_text(self, setting: AskedSetting, title: str, prompt: str) -> None:
        self.choices[setting] = text_input(
            self._window, title, prompt, inherited=self._inherited_text(setting), required=True
        )

    def _ask_system(self) -> None:
        title = "System Configuration"
        self._ask_text(AskedSetting.HOSTNAME, title, "Hostname:")
        self._ask_text(AskedSetting.USERNAME, title, "Username:")
        self._ask_text(AskedSetting.TIMEZONE, title, "Timezone (e.g. Europe/Helsinki):")
        self._ask_text(AskedSetting.KEYMAP, title, "Keymap:")

    # an inherited password is kept or replaced; it is never shown
    def _ask_password(
        self, setting: AskedSetting, title: str, name: str, confirm_new: bool = True
    ) -> None:
        inherited = self._inherited.get(setting)
        if inherited is not None and inherited.value:
            choice = radio_menu(
                self._window,
                title,
                PASSWORD_OPTIONS,
                inherited=None,
                description=f"{name}: inherited from {inherited.source}.",
            )
            if choice == KEEP_PASSWORD:
                self.choices[setting] = inherited.value
                return
        if confirm_new:
            self.choices[setting] = password_input_with_confirm(self._window, title, f"{name}:")
        else:
            self.choices[setting] = text_input(
                self._window, title, f"{name}:", inherited=None, required=True, masked=True
            )

    def _ask_storage(self) -> None:
        disks = self._hardware.list_disks()
        if disks:
            options = [
                MenuOption(disk.path, f"{disk.path}  {disk.model}  ({disk.size})") for disk in disks
            ]
            self.choices[AskedSetting.TARGET_DISK] = radio_menu(
                self._window,
                "Disk Selection",
                options,
                inherited=self._inherited_text(AskedSetting.TARGET_DISK),
                description="Select target disk (ALL DATA WILL BE ERASED):",
            )
        else:
            self._ask_text(
                AskedSetting.TARGET_DISK, "Disk Selection", "Enter disk path (e.g. /dev/sda):"
            )
        self.choices[AskedSetting.WIPE_METHOD] = radio_menu(
            self._window,
            "Disk Wipe Method",
            WIPE_OPTIONS,
            inherited=self._inherited_text(AskedSetting.WIPE_METHOD),
        )

    def _ask_usb_boot(self) -> None:
        enabled = (
            radio_menu(
                self._window,
                "USB Boot Drive",
                USB_BOOT_OPTIONS,
                inherited=self._inherited_switch(AskedSetting.USB_BOOT, "yes", "no"),
                description="Store the EFI partition and the detached LUKS header on a USB drive.",
            )
            == "yes"
        )
        self.choices[AskedSetting.USB_BOOT] = enabled
        if enabled:
            self._ask_text(
                AskedSetting.USB_BOOT_DEVICE,
                "USB Boot Device",
                "Enter USB device path (e.g. /dev/sdb):",
            )

    def _ask_hardware(self) -> None:
        self.choices[AskedSetting.CPU_VENDOR] = radio_menu(
            self._window,
            "CPU Vendor",
            CPU_OPTIONS,
            inherited=self._inherited_text(AskedSetting.CPU_VENDOR),
        )
        gpu_vendor = radio_menu(
            self._window,
            "GPU Vendor",
            GPU_OPTIONS,
            inherited=self._inherited_text(AskedSetting.GPU_VENDOR),
        )
        self.choices[AskedSetting.GPU_VENDOR] = gpu_vendor
        if gpu_vendor != GpuVendor.NVIDIA:
            # only NVIDIA offers a choice of driver; the others use their vendor's driver
            self.choices[AskedSetting.GPU_DRIVER] = GpuDriver.VENDOR_DEFAULT
            return
        self.choices[AskedSetting.GPU_DRIVER] = radio_menu(
            self._window,
            "NVIDIA Driver",
            NVIDIA_DRIVER_OPTIONS,
            inherited=self._inherited_text(AskedSetting.GPU_DRIVER),
        )

    def _ask_desktops(self) -> None:
        setting_value = self._inherited.get(AskedSetting.DESKTOPS)
        inherited = None
        if setting_value is not None and setting_value.value is not None:
            desktops = [_as_text(desktop) for desktop in setting_value.value]
            inherited = Inherited(desktops, str(setting_value.source))
        chosen = checkbox_menu(
            self._window,
            "Desktop Environments",
            DESKTOP_OPTIONS,
            inherited=inherited,
            description="Select desktop environments to install (Space to toggle):",
        )
        # a selection is a set: the same desktops in another order keep the inherited value
        unchanged = inherited is not None and set(chosen) == set(inherited.value)
        self.choices[AskedSetting.DESKTOPS] = setting_value.value if unchanged else chosen

    def _inherited_swap(self) -> Inherited[str] | None:
        enabled = self._inherited.get(AskedSetting.SWAP)
        size_mb = self._inherited.get(AskedSetting.SWAP_SIZE_MB)
        if enabled is not None and enabled.value is False:
            return Inherited(NO_SWAP.value, str(enabled.source))
        if enabled is not None and size_mb is not None:
            return Inherited(_as_text(size_mb.value), str(size_mb.source))
        return None

    def _ask_swap(self) -> None:
        inherited = self._inherited_swap()
        inherited_size_mb = int(inherited.value) if inherited else None
        swap_size_mb = int(
            radio_menu(
                self._window,
                "Swap Size",
                swap_size_options(inherited_size_mb),
                inherited=inherited,
            )
        )
        self.choices[AskedSetting.SWAP] = swap_size_mb > 0
        if swap_size_mb > 0:
            self.choices[AskedSetting.SWAP_SIZE_MB] = swap_size_mb

    def _ask_features(self) -> None:
        toggles = [
            Toggle(setting, label, self._inherited_flag(setting))
            for setting, label in FEATURE_TOGGLES
        ]
        chosen = toggle_menu(
            self._window,
            "Features",
            toggles,
            description="Toggle with Space, then Tab to continue:",
        )
        self.choices.update(chosen)
        # hibernation resumes from the swapfile, so it cannot be on without swap
        if not self.choices[AskedSetting.SWAP]:
            self.choices[AskedSetting.HIBERNATION] = False

    def _summary(self) -> list[tuple[str, str, str]]:
        final = apply_tui_choices(self._inherited, self.choices)

        def row(label: str, setting: AskedSetting, shown: str) -> tuple[str, str, str]:
            setting_value = final.get(setting)
            source = str(setting_value.source) if setting_value else "not set"
            return label, shown, source

        def as_switch(setting: AskedSetting) -> str:
            return "enabled" if self._current(setting) else "disabled"

        swap_size = (
            f"{self._current(AskedSetting.SWAP_SIZE_MB)} MB"
            if self._current(AskedSetting.SWAP)
            else "disabled"
        )
        desktops = ", ".join(_as_text(desktop) for desktop in self._current(AskedSetting.DESKTOPS))
        text_rows = (
            ("System.Hostname", AskedSetting.HOSTNAME),
            ("System.Username", AskedSetting.USERNAME),
            ("System.Timezone", AskedSetting.TIMEZONE),
            ("System.Keymap", AskedSetting.KEYMAP),
            ("Storage.Target Disk", AskedSetting.TARGET_DISK),
            ("Storage.Wipe Method", AskedSetting.WIPE_METHOD),
        )
        return [
            *(
                row(label, setting, _as_text(self._current(setting)))
                for label, setting in text_rows
            ),
            row("Storage.Swap", AskedSetting.SWAP, swap_size),
            row(
                "Hardware.CPU",
                AskedSetting.CPU_VENDOR,
                _as_text(self._current(AskedSetting.CPU_VENDOR)),
            ),
            row(
                "Hardware.GPU",
                AskedSetting.GPU_VENDOR,
                _as_text(self._current(AskedSetting.GPU_VENDOR)),
            ),
            row("Packages.Desktops", AskedSetting.DESKTOPS, desktops or "none"),
            *(
                row(f"Features.{label}", setting, as_switch(setting))
                for setting, label in FEATURE_TOGGLES
            ),
            row("Features.Migration", AskedSetting.MIGRATION, as_switch(AskedSetting.MIGRATION)),
            row("USB Boot.Enabled", AskedSetting.USB_BOOT, as_switch(AskedSetting.USB_BOOT)),
        ]


# the inherited size is always offered, even when it is not one of the presets
def swap_size_options(inherited_size_mb: int | None) -> list[MenuOption]:
    sizes_mb = set(SWAP_PRESETS_MB)
    if inherited_size_mb:
        sizes_mb.add(inherited_size_mb)
    return [MenuOption(str(size_mb), _swap_label(size_mb)) for size_mb in sorted(sizes_mb)] + [
        NO_SWAP
    ]


def _swap_label(size_mb: int) -> str:
    if size_mb % 1024 == 0:
        return f"{size_mb // 1024} GB"
    return f"{size_mb} MB"


def _collect(
    window: curses.window, inherited: Mapping[str, SettingValue], runner: CommandRunner
) -> dict[str, Any]:
    init_colors()
    curses.curs_set(0)
    return SetupSession(window, inherited, HardwareDetector(runner)).run()


# quitting raises KeyboardInterrupt, which the caller reports as a cancellation
def run_tui_setup(
    inherited: Mapping[str, SettingValue], runner: CommandRunner | None = None
) -> dict[str, Any]:
    return curses.wrapper(_collect, inherited, runner or SystemCommandRunner())
