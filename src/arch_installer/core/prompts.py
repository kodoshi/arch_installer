"""
interactive prompts for user input during installation.

this module provides module-level functions that delegate to the
CLI interaction strategy for simpler usage patterns.
"""

from typing import Optional

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.cli_interaction import CLIInteractionStrategy
from arch_installer.core.command import CommandRunner
from arch_installer.core.interaction import (
    CPU_OPTIONS,
    DESKTOP_OPTIONS,
    GPU_OPTIONS,
    NVIDIA_DRIVER_OPTIONS,
    SWAP_OPTIONS,
    WIPE_OPTIONS,
    DiskInfo,
    HardwareDetector,
    InstallationSelections,
    MenuOption,
)

__all__ = [
    "DiskInfo",
    "MenuOption",
    "GPU_OPTIONS",
    "NVIDIA_DRIVER_OPTIONS",
    "CPU_OPTIONS",
    "DESKTOP_OPTIONS",
    "SWAP_OPTIONS",
    "WIPE_OPTIONS",
    "InstallationSelections",
    "list_available_disks",
    "prompt_disk_selection",
    "prompt_secrets_key",
    "run_interactive_setup",
]

_strategy: Optional[CLIInteractionStrategy] = None


def _get_strategy(
    runner: Optional[CommandRunner] = None,
    config: Optional[DeclaredConfig] = None,
) -> CLIInteractionStrategy:
    global _strategy
    if _strategy is None or runner is not None:
        _strategy = CLIInteractionStrategy(runner, config)
    elif config is not None:
        _strategy.set_config(config)
    return _strategy


def list_available_disks(runner: Optional[CommandRunner] = None) -> list[DiskInfo]:
    """list all available disks on the system."""
    detector = HardwareDetector(runner)
    return detector.list_available_disks()


def prompt_disk_selection(runner: Optional[CommandRunner] = None) -> str:
    """interactively prompt user to select a target disk."""
    return _get_strategy(runner).prompt_disk_selection()


def prompt_secrets_key() -> str:
    """prompt for the symmetric key to decrypt encrypted secrets."""
    return _get_strategy().prompt_secrets_key()


def run_interactive_setup(
    has_encrypted_secrets: bool = False,
    require_system_config: bool = False,
    runner: Optional[CommandRunner] = None,
    config: Optional[DeclaredConfig] = None,
) -> InstallationSelections:
    """run the full interactive installation setup.

    guides user through all configuration options needed for installation.

    args:
        has_encrypted_secrets: whether config has encrypted passwords
        require_system_config: whether to ask for hostname/username/etc
        runner: optional command runner for hardware detection
        config: optional declared config to show defaults in prompts

    returns:
        complete installation selections

    raises:
        SystemExit: if user cancels at any point
    """
    strategy = _get_strategy(runner, config)
    require_passwords = not has_encrypted_secrets

    return strategy.collect_all_selections(
        require_disk=True,
        require_passwords=require_passwords,
        require_system_config=require_system_config,
    )
