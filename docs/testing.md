# Testing

## Development Setup

### Prerequisites

- Python 3.13+
- [Poetry](https://python-poetry.org/) for dependency management
- Docker (for integration tests)
- QEMU + OVMF (for end-to-end tests)

### Installing Poetry

```bash
# Linux/macOS
curl -sSL https://install.python-poetry.org | python3 -

# or via pipx
pipx install poetry
```

### Setting Up the Project

```bash
cd arch_installer
poetry install        # install dependencies
poetry shell          # activate virtual environment
```

You can prefix commands with `poetry run`:

```bash
poetry run pytest tests/unit/
```

## Running Tests

```bash
# unit tests (fast)
poetry run pytest tests/unit/

# QEMU tests (requires ISO)
poetry run pytest tests/qemu/ --arch-iso ./archlinux-x86_64.iso

# all tests with coverage
poetry run pytest --cov

# verbose output
poetry run pytest -v

# stop on first failure
poetry run pytest -x
```

## Philosophy

The testing approach follows these principles:

1. **Full Environment Testing**: Tests run in actual QEMU virtual machines with real UEFI firmware, not mocked environments. This ensures the installer works in production conditions.

2. **Explicit Dependencies**: All test fixtures use explicit parameter dependencies - no `autouse` or hidden session setup. If a test needs something, it's in the function signature.

3. **Layered Testing**: From fast unit tests to slow full-installation tests, each layer validates different aspects without redundancy.

4. **Deterministic Reproducibility**: Every test run should produce identical results given the same inputs. External dependencies (network, package versions) are controlled through a local package cache proxy (WIP).

## Test Categories

### Unit Tests (`tests/unit/`)

Fast tests that validate individual components in isolation:

- Configuration parsing and validation
- Command building logic
- Path manipulation
- Data structure transformations

Run with: `poetry run pytest tests/unit/`

### QEMU Tests (`tests/qemu/`)

Full end-to-end tests in QEMU VMs with real UEFI firmware:

- Complete installation workflows (non-interactive and TUI interactive)
- Secure Boot enrollment and verification
- Negative Secure Boot test (unsigned binaries blocked)
- BTRFS snapshot functionality
- USB boot drive (plausible deniability encryption): an internal disk holding only ciphertext (no partition table, random from first to last byte), booting every kernel, a snapshot, the recovery system and the spare drive from the drive's menu, the pacman guard with the drive unplugged
- System bootability validation
- TUI installer driven through tmux keystrokes (only the secrets key comes from the environment; passwords are inherited from encrypted secrets and kept on their screens)
- TUI screen-by-screen selections (`test_tui.py`): typed overrides and cursor moves relative to the inherited values

All QEMU installation tests have a 30-minute timeout to prevent hanging.

Run with: `poetry run pytest tests/qemu/ --arch-iso /path/to/archlinux.iso`

## Package Cache Proxy (WIP)

The package cache proxy ensures reproducible tests by serving packages from a local cache instead of upstream mirrors.

### How It Works

1. The proxy starts an HTTP server on a random available port
2. VM's pacman mirrorlist points to the proxy
3. On first request, packages are fetched from upstream and cached
4. Subsequent requests serve from cache
5. In offline mode, only cached packages are served (test fails if package missing)

### Usage

```bash
# use default temporary cache (cleaned after test)
poetry run pytest tests/qemu/

# persist cache for faster subsequent runs
poetry run pytest tests/qemu/ --package-cache-dir ~/.cache/arch-installer-tests

# run in offline mode (fail if any package not cached)
poetry run pytest tests/qemu/ --offline-mode --package-cache-dir ~/.cache/arch-installer-tests
```

### Pre-caching Packages

For CI environments or offline testing, pre-cache required packages:

```python
from tests.qemu.package_cache import PackageCacheProxy, PackageCacheConfig, ESSENTIAL_PACKAGES

config = PackageCacheConfig(cache_dir=Path("./package-cache"))
proxy = PackageCacheProxy(config)
proxy.precache_packages(ESSENTIAL_PACKAGES)
```

### VM Fixtures

The fixtures boot the Arch ISO under UEFI in setup mode, set the live root password
over the console and wait until SSH works:

- `qemu_vm_with_network`: one virtio disk (`/dev/vda`)
- `qemu_vm_with_backup_disk_and_network`: plus a second virtio disk (`/dev/vdb`) to back up to
- `qemu_vm_with_usb_drives_and_network`: plus two USB mass storage drives on an xHCI controller (`/dev/sda`, `/dev/sdb`), removable like sticks; a test unplugs one by leaving it out of `vm.paths.attached_usb_disk_images` before the next start

## Running Tests

### Prerequisites

```bash
# install QEMU (macOS)
brew install qemu

# install QEMU + OVMF + sshpass (Arch Linux)
pacman -S qemu-full edk2-ovmf sshpass

# download Arch ISO
curl -LO https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso
```

### Full Test Suite

```bash
# unit
poetry run pytest tests/unit/

# QEMU tests (slow)
poetry run pytest tests/qemu/ --arch-iso ./archlinux-x86_64.iso -v

# QEMU tests on 3 VMs in parallel (each VM uses 4 GB RAM and its own SSH port range)
poetry run pytest tests/qemu/ --arch-iso ./archlinux-x86_64.iso -v -n 3

# everything
poetry run pytest --arch-iso ./archlinux-x86_64.iso
```

### Debugging Failed Tests

```bash
# keep VM running after test for SSH access
poetry run pytest tests/qemu/... --keep-vm
```

## Test Configuration

### Command Line Options

| Option                | Default | Description                                      |
| --------------------- | ------- | ------------------------------------------------ |
| `--arch-iso`          | None    | Path to Arch Linux ISO (required for QEMU tests) |
| `--qemu-memory`       | 4096    | VM memory in MB                                  |
| `--qemu-cpus`         | 6       | VM CPU count                                     |
| `--qemu-disk-size`    | 20      | VM disk size in GB                               |
| `--qemu-display`      | false   | Show the VM in an SDL window instead of headless |
| `--qemu-work-dir`     | `~/.cache/arch-installer-qemu` | Where VM disk images live |
| `--keep-vm`           | false   | Keep VM running after test                       |
| `--package-cache-dir` | temp    | Directory for package cache                      |
| `--offline-mode`      | false   | Fail if package not in cache                     |

### Markers

- `@pytest.mark.qemu`: Requires QEMU and ISO
- `@pytest.mark.slow`: Long-running test (>3 minutes)

Skip slow tests: `pytest -m "not slow"`

## CI Integration

### GitLab CI Example

```yaml
stages:
  - test

variables:
  CACHE_DIR: /cache/arch-installer-tests

unit-tests:
  stage: test
  image: python:3.13
  script:
    - pip install poetry
    - poetry install
    - poetry run pytest tests/unit/ -v

qemu-tests:
  stage: test
  image: archlinux:latest
  cache:
    key: pacman-cache-$CI_COMMIT_REF_SLUG
    paths:
      - /cache/arch-installer-tests/
  before_script:
    - pacman -Sy --noconfirm qemu-full edk2-ovmf python python-pip
    - pip install poetry
    - poetry install
  script:
    - curl -LO https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso
    - |
      poetry run pytest tests/qemu/ \
        --arch-iso ./archlinux-x86_64.iso \
        --package-cache-dir $CACHE_DIR
  tags:
    - kvm # requires KVM-enabled runner
```

## Writing New Tests

### Fixture Usage Pattern

```python
@pytest.mark.qemu
def test_feature_works(
    qemu_vm_with_network: QemuVm,  # explicit dependency
    installer_config: dict,        # explicit dependency
) -> None:
    # test uses exactly what it depends on
    exit_code, stdout, _ = qemu_vm_with_network.run_ssh_command("...")
    assert exit_code == 0
```

### Assertion Helpers

```python
from tests.qemu.assertions import InstallationAssertions

def test_installation_correct(qemu_vm_with_network: QemuVm) -> None:
    assertions = InstallationAssertions(qemu_vm_with_network)

    assertions.assert_partitions_exist("/dev/vda")
    assertions.assert_btrfs_subvolumes_exist(["@", "@home"])
    assertions.assert_secure_boot_keys_created()

    assertions.raise_if_failed()  # raises with all failures
```

## Troubleshooting

### QEMU Won't Start

Check OVMF firmware paths:

```bash
ls /opt/homebrew/share/qemu/edk2-*  # macOS
ls /usr/share/edk2-ovmf/            # Linux
```

### SSH Connection Fails

The Arch ISO's root account has no password, so the fixtures type `echo root:root | chpasswd`
on the VM console through the QEMU monitor before connecting. `sshpass` must be installed.

### Disk Images and tmpfs

VM disk images go to `--qemu-work-dir`, not `/tmp`. On Arch `/tmp` is usually a RAM-backed
tmpfs, and a `secure` wipe fills the whole 20 GB virtual disk, which would end up in RAM.

### Port Conflicts

Each test gets unique random ports. If you see "address already in use", wait a moment and retry - previous test may still be cleaning up.
