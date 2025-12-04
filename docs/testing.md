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

- Complete installation workflows
- Secure Boot enrollment and verification
- BTRFS snapshot functionality
- System bootability validation
- GUI installer with keyboard simulation

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

### VM State Fixtures (progressive)

```
qemu_vm
    ↓
qemu_vm_booted_from_iso  (VM started with ISO)
    ↓
qemu_vm_with_network     (VM with working network)
    ↓
qemu_vm_with_sbctl       (VM with sbctl installed via cache proxy)
```

## Running Tests

### Prerequisites

```bash
# install QEMU (macOS)
brew install qemu

# install QEMU + OVMF (Arch Linux)
pacman -S qemu-full edk2-ovmf

# download Arch ISO
curl -LO https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso
```

### Full Test Suite

```bash
# unit
poetry run pytest tests/unit/

# QEMU tests (slow)
poetry run pytest tests/qemu/ --arch-iso ./archlinux-x86_64.iso -v

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
| `--qemu-headless`     | true    | Run VM without display                           |
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

The Arch ISO needs manual network setup. The `qemu_vm_with_network` fixture handles this, but raw `qemu_vm_booted_from_iso` may not have networking.

### Port Conflicts

Each test gets unique random ports. If you see "address already in use", wait a moment and retry - previous test may still be cleaning up.
