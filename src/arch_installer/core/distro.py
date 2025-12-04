"""
distribution abstraction layer for multi-distro support

this module provides abstract base classes and interfaces that allow
the installer to support multiple Linux distributions in the future
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

from arch_installer.core.command import CommandRunner


class DistroFamily(Enum):
    ARCH = auto()
    DEBIAN = auto()
    FEDORA = auto()
    SUSE = auto()


@dataclass(frozen=True)
class DistroInfo:
    name: str
    family: DistroFamily
    version: str
    package_manager: str
    init_system: str


class PackageManager(Protocol):
    def install(self, packages: list[str]) -> None: ...

    def update_cache(self) -> None: ...

    def is_installed(self, package: str) -> bool: ...


class BootstrapStrategy(ABC):
    """Abstract strategy for bootstrapping a new system."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    @abstractmethod
    def bootstrap(self, target: str, packages: list[str]) -> None:
        """Bootstrap a minimal system to the target path."""
        ...

    @abstractmethod
    def configure_fstab(self, target: str) -> None:
        """Generate fstab for the target system."""
        ...


class ArchBootstrapStrategy(BootstrapStrategy):
    """Arch Linux bootstrap using pacstrap."""

    def bootstrap(self, target: str, packages: list[str]) -> None:
        packages_str = " ".join(packages)
        self._runner.run(f"pacstrap -K {target} {packages_str}")

    def configure_fstab(self, target: str) -> None:
        self._runner.run(f"genfstab -U {target} >> {target}/etc/fstab")


class InitramfsGenerator(ABC):
    """Abstract strategy for initramfs generation."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    @abstractmethod
    def configure(self, hooks: list[str]) -> None:
        """Configure initramfs generation."""
        ...

    @abstractmethod
    def generate(self, kernel: str) -> None:
        """Generate initramfs for a kernel."""
        ...

    @abstractmethod
    def generate_all(self) -> None:
        """Generate initramfs for all kernels."""
        ...


class MkinitcpioGenerator(InitramfsGenerator):
    """mkinitcpio-based initramfs generation for Arch Linux."""

    def configure(self, hooks: list[str]) -> None:
        hooks_str = " ".join(hooks)
        conf_content = f"""MODULES=()
BINARIES=()
FILES=()
HOOKS=({hooks_str})
"""
        self._runner.run(f"cat > /mnt/etc/mkinitcpio.conf << 'EOF'\n{conf_content}EOF")

    def generate(self, kernel: str) -> None:
        self._runner.run_as_chroot(f"mkinitcpio -p {kernel}")

    def generate_all(self) -> None:
        self._runner.run_as_chroot("mkinitcpio -P")


class DistroFactory:
    """Factory for creating distro-specific components."""

    @staticmethod
    def get_bootstrap_strategy(
        distro: DistroFamily,
        runner: CommandRunner,
    ) -> BootstrapStrategy:
        strategies = {
            DistroFamily.ARCH: ArchBootstrapStrategy,
        }

        strategy_class = strategies.get(distro)
        if strategy_class is None:
            raise NotImplementedError(f"Bootstrap strategy not implemented for {distro}")

        return strategy_class(runner)

    @staticmethod
    def get_initramfs_generator(
        distro: DistroFamily,
        runner: CommandRunner,
    ) -> InitramfsGenerator:
        generators = {
            DistroFamily.ARCH: MkinitcpioGenerator,
        }

        generator_class = generators.get(distro)
        if generator_class is None:
            raise NotImplementedError(f"Initramfs generator not implemented for {distro}")

        return generator_class(runner)

