"""backup of dotfiles, password databases, browser profiles and system config to a
backup partition (sync.backup_partition), plus the package list and config.yaml of this
machine. the partition is the user's own: it is mounted, never formatted, and it is not
the USB boot drive, whose partitions hold only what booting needs.
"""

import logging
from dataclasses import dataclass

import yaml

from arch_installer.config.models import (
    BackupCategory,
    BackupItemConfig,
    exportable_config,
)
from arch_installer.errors import ConfigurationError
from arch_installer.executors.base import (
    StepExecutor,
    directory_exists,
    is_mountpoint,
    path_exists,
    write_file,
)

logger = logging.getLogger(__name__)

BACKUP_MOUNT = "/mnt/usb-backup"
CUSTOM_ITEMS_DIRECTORY = "custom"

BACKUP_ITEMS = {
    BackupCategory.DOTFILES: (
        BackupItemConfig("zshrc", "~/.zshrc", "ZSH config"),
        BackupItemConfig("zshenv", "~/.zshenv", "ZSH environment"),
        BackupItemConfig("bashrc", "~/.bashrc", "Bash config"),
        BackupItemConfig("gitconfig", "~/.gitconfig", "Git config"),
        BackupItemConfig("gitignore", "~/.gitignore_global", "Git global ignore"),
        BackupItemConfig("kitty", "~/.config/kitty", "Kitty terminal config"),
        BackupItemConfig("alacritty", "~/.config/alacritty", "Alacritty config"),
        BackupItemConfig("nvim", "~/.config/nvim", "Neovim config"),
        BackupItemConfig(
            "vscode_settings", "~/.config/Code/User/settings.json", "VS Code settings"
        ),
        BackupItemConfig(
            "vscode_keybindings", "~/.config/Code/User/keybindings.json", "VS Code keybindings"
        ),
        BackupItemConfig("starship", "~/.config/starship.toml", "Starship prompt config"),
        BackupItemConfig("tmux", "~/.tmux.conf", "Tmux config"),
        BackupItemConfig("hypr", "~/.config/hypr", "Hyprland config"),
        BackupItemConfig("waybar", "~/.config/waybar", "Waybar config"),
        BackupItemConfig("rofi", "~/.config/rofi", "Rofi config"),
        BackupItemConfig("dunst", "~/.config/dunst", "Dunst notification config"),
    ),
    BackupCategory.KEEPASS: (
        BackupItemConfig("keepassxc_db", "~/Documents/Passwords.kdbx", "KeePassXC database"),
        BackupItemConfig("keepassxc_config", "~/.config/keepassxc", "KeePassXC config"),
    ),
    BackupCategory.BROWSER: (
        BackupItemConfig("firefox_profile", "~/.mozilla/firefox", "Firefox profile"),
        BackupItemConfig("chromium_profile", "~/.config/chromium", "Chromium profile"),
    ),
    BackupCategory.SYSTEM: (
        BackupItemConfig("pacman_conf", "/etc/pacman.conf", "Pacman config"),
        BackupItemConfig("makepkg_conf", "/etc/makepkg.conf", "Makepkg config"),
        BackupItemConfig("mkinitcpio_conf", "/etc/mkinitcpio.conf", "Mkinitcpio config"),
    ),
}


@dataclass(frozen=True)
class BackupManifest:
    timestamp: str
    hostname: str
    categories: tuple[str, ...]
    package_count: int
    items_backed_up: tuple[str, ...]


def package_catalog(package_names: list[str]) -> str:
    # the same shape as config.yaml, so it can be pasted into a new config as is
    catalog = {"packages": {"cataloged": package_names}}
    header = "# explicitly installed packages of the backed up system\n"
    return header + yaml.safe_dump(catalog, default_flow_style=False, sort_keys=False)


class UsbBackupStepExecutor(StepExecutor):
    def execute(self) -> None:
        partition = self._config.sync.backup_partition
        if not partition:
            raise ConfigurationError(
                "No partition to back up to (BACKUP_PARTITION or sync.backup_partition)"
            )
        if not path_exists(self._runner, partition):
            raise ConfigurationError(f"Backup partition {partition} does not exist")

        categories = self._config.sync.backup_categories
        logger.info("Backing up %s to %s...", ", ".join(categories), partition)
        self._runner.run(f"mkdir -p {BACKUP_MOUNT}")
        if not is_mountpoint(self._runner, BACKUP_MOUNT):
            self._runner.run(f"mount {partition} {BACKUP_MOUNT}")
        try:
            items_backed_up = self._copy_items(categories)
            package_names = self._explicitly_installed_packages()
            self._write_config_exports(package_names)
            manifest = BackupManifest(
                timestamp=self._command_output("date -Iseconds"),
                hostname=self._command_output("hostname"),
                categories=tuple(str(category) for category in categories),
                package_count=len(package_names),
                items_backed_up=tuple(items_backed_up),
            )
            write_file(
                self._runner,
                f"{BACKUP_MOUNT}/manifest.yaml",
                yaml.safe_dump(
                    {
                        "timestamp": manifest.timestamp,
                        "hostname": manifest.hostname,
                        "categories": list(manifest.categories),
                        "package_count": manifest.package_count,
                        "items_backed_up": list(manifest.items_backed_up),
                    },
                    sort_keys=False,
                ),
            )
        finally:
            self._runner.run(f"umount {BACKUP_MOUNT}", raise_on_nonzero_exit=False)
        logger.info(
            "Backup complete: %s items, %s packages", len(items_backed_up), len(package_names)
        )

    def _copy_items(self, categories: tuple[BackupCategory, ...]) -> list[str]:
        groups = [(str(category), BACKUP_ITEMS[category]) for category in categories]
        if self._config.sync.backup_items:
            groups.append((CUSTOM_ITEMS_DIRECTORY, self._config.sync.backup_items))

        items_backed_up = []
        for group_directory, items in groups:
            self._runner.run(f"mkdir -p {BACKUP_MOUNT}/{group_directory}")
            for item in items:
                if self._copy_item(
                    self._expand_home(item.source_path),
                    f"{BACKUP_MOUNT}/{group_directory}/{item.name}",
                ):
                    items_backed_up.append(f"{group_directory}/{item.name}")
                    logger.debug("Backed up %s (%s)", item.name, item.description)
        return items_backed_up

    def _copy_item(self, source: str, destination: str) -> bool:
        if not path_exists(self._runner, source):
            return False
        if directory_exists(self._runner, source):
            self._runner.run(f"mkdir -p {destination}")
            self._runner.run(f"rsync -a --delete {source}/ {destination}/")
        else:
            self._runner.run(f"cp -a {source} {destination}")
        return True

    def _explicitly_installed_packages(self) -> list[str]:
        result = self._runner.run("pacman -Qqe", raise_on_nonzero_exit=False)
        if not result.success:
            logger.warning("Could not list installed packages: %s", result.stderr.strip())
            return []
        return [name for name in result.stdout.split() if name]

    def _write_config_exports(self, package_names: list[str]) -> None:
        config_directory = f"{BACKUP_MOUNT}/config"
        self._runner.run(f"mkdir -p {config_directory}")
        write_file(
            self._runner, f"{config_directory}/package_catalog.yaml", package_catalog(package_names)
        )
        write_file(
            self._runner,
            f"{config_directory}/config.yaml",
            yaml.safe_dump(
                exportable_config(self._config), default_flow_style=False, sort_keys=False
            ),
        )

    def _expand_home(self, path: str) -> str:
        if path.startswith("~"):
            return path.replace("~", f"/home/{self._config.system.user.name}", 1)
        return path

    def _command_output(self, command: str) -> str:
        result = self._runner.run(command, raise_on_nonzero_exit=False)
        return result.stdout.strip() if result.success else "unknown"
