"""pytest fixtures for QEMU-based integration tests.

provides fixtures specifically for QEMU-based tests, with explicit
fixture dependencies (no autouse or session-scoped magic).
"""

import os
import socket
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
import yaml

from tests.qemu.package_cache import PackageCacheConfig, PackageCacheProxy
from tests.qemu.vm import (
    QemuConfig,
    QemuError,
    QemuVm,
    SecureBootMode,
    find_ovmf_paths,
    find_qemu_binary,
    find_sshpass_binary,
    wait_for_vm_boot_and_network,
)

USB_DISK_SIZE_GB = 4


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--qemu-display",
        action="store_true",
        default=False,
        help="show the VM display in an SDL window instead of running headless",
    )
    parser.addoption(
        "--qemu-memory",
        type=int,
        default=4096,
        help="QEMU VM memory in MB (default: 4096)",
    )
    parser.addoption(
        "--qemu-cpus",
        type=int,
        default=6,
        help="QEMU VM CPU count (default: 6)",
    )
    parser.addoption(
        "--qemu-disk-size",
        type=int,
        default=20,
        help="QEMU VM disk size in GB (default: 20)",
    )
    parser.addoption(
        "--qemu-work-dir",
        type=str,
        default=str(Path.home() / ".cache" / "arch-installer-qemu"),
        help=(
            "directory for VM disk images (default: ~/.cache/arch-installer-qemu). "
            "avoid tmpfs: a secure wipe fills the whole virtual disk"
        ),
    )
    parser.addoption(
        "--arch-iso",
        type=str,
        default=None,
        help="path to Arch Linux ISO for installation tests",
    )
    parser.addoption(
        "--keep-vm",
        action="store_true",
        default=False,
        help="keep VM running after test for debugging",
    )
    parser.addoption(
        "--package-cache-dir",
        type=str,
        default=None,
        help="directory for pacman package cache (creates temp if not set)",
    )
    parser.addoption(
        "--offline-mode",
        action="store_true",
        default=False,
        help="run tests in offline mode using only cached packages",
    )


def _find_free_port(start: int = 2222, end: int = 3000) -> int:
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
                probe_socket.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"no free port found in range {start}-{end}")


def _ssh_port_for_this_worker() -> int:
    # a probed port is only reserved once QEMU binds it, so parallel xdist workers
    # (gw0, gw1, ...) each search their own range to avoid picking the same one
    worker_index = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw") or 0)
    range_start = 2222 + worker_index * 100
    return _find_free_port(range_start, range_start + 100)


def _build_qemu_config(request, extra_disks_gb: tuple[int, ...] = ()) -> QemuConfig:
    return QemuConfig(
        memory_mb=request.config.getoption("--qemu-memory"),
        cpus=request.config.getoption("--qemu-cpus"),
        disk_size_gb=request.config.getoption("--qemu-disk-size"),
        extra_disks_gb=extra_disks_gb,
        secure_boot=SecureBootMode.SETUP_MODE,
        headless=not request.config.getoption("--qemu-display"),
        ssh_port=_ssh_port_for_this_worker(),
    )


def _vm_booted_with_network(
    request,
    config: QemuConfig,
    arch_iso_path: Path | None,
) -> Generator[QemuVm]:
    if arch_iso_path is None:
        pytest.skip("no Arch ISO provided (use --arch-iso)")

    work_root = Path(request.config.getoption("--qemu-work-dir"))
    work_root.mkdir(parents=True, exist_ok=True)
    working_directory = Path(tempfile.mkdtemp(prefix="arch-qemu-test-", dir=work_root))

    vm = QemuVm(config=config)
    vm.setup(working_directory)
    try:
        vm.start(iso_path=arch_iso_path)
        if not wait_for_vm_boot_and_network(vm, timeout=180):
            pytest.fail("VM failed to boot or establish network")
        yield vm
    finally:
        if request.config.getoption("--keep-vm"):
            print(f"\n[--keep-vm] VM kept running. Work dir: {working_directory}")
            print(f"[--keep-vm] SSH: ssh -p {config.ssh_port} root@127.0.0.1")
        else:
            vm.cleanup()


@pytest.fixture
def qemu_is_available() -> None:
    try:
        find_qemu_binary()
        find_ovmf_paths()
        find_sshpass_binary()
    except QemuError as error:
        pytest.skip(f"QEMU not available: {error}")


@pytest.fixture
def arch_iso_path(request) -> Path | None:
    iso_path = request.config.getoption("--arch-iso")
    if iso_path:
        path = Path(iso_path)
        if not path.exists():
            pytest.skip(f"Arch ISO not found: {iso_path}")
        return path
    return None


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent.parent


@pytest.fixture
def installer_config(project_root: Path) -> dict:
    with open(project_root / "config" / "config.yaml") as config_file:
        return yaml.safe_load(config_file)


@pytest.fixture
def package_cache_config(request, tmp_path_factory) -> PackageCacheConfig:
    cache_directory = request.config.getoption("--package-cache-dir")
    cache_path = (
        Path(cache_directory) if cache_directory else tmp_path_factory.mktemp("pacman-cache")
    )

    return PackageCacheConfig(
        cache_directory=cache_path,
        port=_find_free_port(8080, 9000),
        offline_mode=request.config.getoption("--offline-mode"),
    )


@pytest.fixture
def package_cache_proxy(
    package_cache_config: PackageCacheConfig,
) -> Generator[PackageCacheProxy]:
    proxy = PackageCacheProxy(package_cache_config)
    proxy.start()
    yield proxy
    proxy.stop()


@pytest.fixture
def qemu_vm_with_network(
    qemu_is_available: None,
    arch_iso_path: Path | None,
    request,
) -> Generator[QemuVm]:
    yield from _vm_booted_with_network(request, _build_qemu_config(request), arch_iso_path)


# second virtio disk (/dev/vdb) simulates a USB boot drive
@pytest.fixture
def qemu_vm_with_usb_disk_and_network(
    qemu_is_available: None,
    arch_iso_path: Path | None,
    request,
) -> Generator[QemuVm]:
    config = _build_qemu_config(request, extra_disks_gb=(USB_DISK_SIZE_GB,))
    yield from _vm_booted_with_network(request, config, arch_iso_path)


@pytest.fixture
def expected_subvolumes(installer_config: dict) -> list[str]:
    return [subvolume["name"] for subvolume in installer_config["storage"]["btrfs"]["subvolumes"]]


@pytest.fixture
def system_config(installer_config: dict) -> dict:
    return installer_config["system"]


@pytest.fixture
def storage_config(installer_config: dict) -> dict:
    return installer_config["storage"]
