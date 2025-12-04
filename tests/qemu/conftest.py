"""pytest fixtures for QEMU-based integration tests.

provides fixtures specifically for QEMU-based tests, with explicit
fixture dependencies (no autouse or session-scoped magic).
"""

import socket
import tempfile
from pathlib import Path
from typing import Generator, Optional

import pytest
import yaml

from tests.qemu.assertions import InstallationAssertions
from tests.qemu.package_cache import PackageCacheConfig, PackageCacheProxy
from tests.qemu.vm import (
    OvmfNotFoundError,
    QemuConfig,
    QemuVm,
    SecureBootMode,
    get_secure_boot_status,
    install_sbctl_in_live_iso,
    wait_for_vm_boot_and_network,
)


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--qemu-headless",
        action="store_true",
        default=True,
        help="run QEMU in headless mode (default: true)",
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
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"no free port found in range {start}-{end}")


@pytest.fixture
def qemu_is_available() -> bool:
    try:
        from tests.qemu.vm import find_ovmf_paths, find_qemu_binary

        find_qemu_binary()
        find_ovmf_paths()
        return True
    except (OvmfNotFoundError, Exception) as e:
        pytest.skip(f"QEMU not available: {e}")
        return False


@pytest.fixture
def arch_iso_path(request) -> Optional[Path]:
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
def config_yaml_path(project_root: Path) -> Path:
    return project_root / "config" / "config.yaml"


@pytest.fixture
def installer_config(config_yaml_path: Path) -> dict:
    with open(config_yaml_path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def package_cache_config(request, tmp_path_factory) -> PackageCacheConfig:
    cache_dir = request.config.getoption("--package-cache-dir")
    if cache_dir:
        cache_path = Path(cache_dir)
    else:
        cache_path = tmp_path_factory.mktemp("pacman-cache")

    return PackageCacheConfig(
        cache_dir=cache_path,
        port=_find_free_port(8080, 9000),
        offline_mode=request.config.getoption("--offline-mode"),
    )


@pytest.fixture
def package_cache_proxy(
    package_cache_config: PackageCacheConfig,
) -> Generator[PackageCacheProxy, None, None]:
    proxy = PackageCacheProxy(package_cache_config)
    proxy.start()
    yield proxy
    proxy.stop()


@pytest.fixture
def qemu_config(request) -> QemuConfig:
    ssh_port = _find_free_port(2222, 3000)
    monitor_port = _find_free_port(4000, 5000)

    return QemuConfig(
        memory_mb=request.config.getoption("--qemu-memory"),
        cpus=request.config.getoption("--qemu-cpus"),
        disk_size_gb=request.config.getoption("--qemu-disk-size"),
        secure_boot=SecureBootMode.SETUP_MODE,
        headless=request.config.getoption("--qemu-headless"),
        ssh_port=ssh_port,
        monitor_port=monitor_port,
    )


@pytest.fixture
def qemu_vm(
    qemu_is_available: bool,
    qemu_config: QemuConfig,
    request,
) -> Generator[QemuVm, None, None]:
    vm = QemuVm(config=qemu_config)

    work_dir = Path(tempfile.mkdtemp(prefix="arch-qemu-test-"))
    vm.setup(work_dir)

    yield vm

    if not request.config.getoption("--keep-vm"):
        vm.cleanup()
    else:
        print(f"\n[--keep-vm] VM kept running. Work dir: {work_dir}")
        print(f"[--keep-vm] SSH: ssh -p {qemu_config.ssh_port} root@127.0.0.1")


@pytest.fixture
def qemu_vm_booted_from_iso(
    qemu_vm: QemuVm,
    arch_iso_path: Optional[Path],
) -> Generator[QemuVm, None, None]:
    if arch_iso_path is None:
        pytest.skip("no Arch ISO provided (use --arch-iso)")

    qemu_vm.start(iso_path=arch_iso_path)
    yield qemu_vm


@pytest.fixture
def qemu_vm_with_network(
    qemu_vm_booted_from_iso: QemuVm,
) -> Generator[QemuVm, None, None]:
    if not wait_for_vm_boot_and_network(qemu_vm_booted_from_iso, timeout=180):
        pytest.fail("VM failed to boot or establish network")
    yield qemu_vm_booted_from_iso


@pytest.fixture
def qemu_vm_with_sbctl(
    qemu_vm_with_network: QemuVm,
    package_cache_proxy: PackageCacheProxy,
) -> Generator[QemuVm, None, None]:
    cache_url = f"http://10.0.2.2:{package_cache_proxy.config.port}"
    if not install_sbctl_in_live_iso(qemu_vm_with_network, cache_proxy_url=cache_url):
        pytest.fail("failed to install sbctl in live ISO")
    yield qemu_vm_with_network


@pytest.fixture
def qemu_vm_with_cache(
    qemu_vm_with_network: QemuVm,
    package_cache_proxy: PackageCacheProxy,
) -> Generator[tuple[QemuVm, str], None, None]:
    """VM with network and package cache proxy URL for installer tests."""
    cache_url = f"http://10.0.2.2:{package_cache_proxy.config.port}"
    yield qemu_vm_with_network, cache_url


@pytest.fixture
def installation_assertions(qemu_vm: QemuVm) -> InstallationAssertions:
    return InstallationAssertions(qemu_vm)


@pytest.fixture
def expected_subvolumes(installer_config: dict) -> list[str]:
    return [sv["name"] for sv in installer_config["storage"]["btrfs"]["subvolumes"]]


@pytest.fixture
def expected_kernels(installer_config: dict) -> list[str]:
    return [k["name"] for k in installer_config["boot"]["kernels"]]


@pytest.fixture
def expected_mkinitcpio_hooks(installer_config: dict) -> list[str]:
    return installer_config["boot"]["hooks"]


@pytest.fixture
def system_config(installer_config: dict) -> dict:
    return installer_config["system"]


@pytest.fixture
def boot_config(installer_config: dict) -> dict:
    return installer_config["boot"]


@pytest.fixture
def storage_config(installer_config: dict) -> dict:
    return installer_config["storage"]
