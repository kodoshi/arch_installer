from pathlib import Path

import pytest

from arch_installer.config.loader import load_main_yaml_config
from arch_installer.config.models import DeclaredConfig
from arch_installer.core.runtime_state import (
    RuntimeConfig,
    create_default_runtime_config,
)
from tests.unit import FakeCommandRunner


@pytest.fixture
def fake_runner() -> FakeCommandRunner:
    return FakeCommandRunner()


@pytest.fixture
def test_config() -> DeclaredConfig:
    config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
    return load_main_yaml_config(config_path)


@pytest.fixture
def minimal_config(tmp_path) -> DeclaredConfig:
    config_content = """
system:
  hostname: testhost
  timezone: UTC
  locale:
    language: en_US
    encoding: UTF-8
    keymap: us
  user:
    name: testuser
    groups:
      - wheel
  mirrors:
    mirrors:
      - https://geo.mirror.pkgbuild.com/$repo/os/$arch

storage:
  target_disk: /dev/loop0
  efi_size_mb: 512
  luks:
    type: luks2
    pbkdf: argon2id
    pbkdf_memory: 1048576
    pbkdf_parallel: 4
    pbkdf_time_ms: 4000
  btrfs:
    label: archroot
    mount_options: compress=zstd,noatime
    subvolumes:
      - name: '@'
        mountpoint: /
      - name: '@home'
        mountpoint: /home
  swap:
    enabled: true
    size_mb: 1024
    path: /.swap/swapfile
    hibernation:
      enabled: false

boot:
  kernels:
    - name: mainline
      package: linux
  variants:
    - suffix: default
      params: ''
  cmdline:
    rootflags: subvol=@
    rootfstype: btrfs
    rw: true
    quiet: true
    hardening:
      lockdown: integrity
      pti: 'on'
  loader:
    timeout: 5
    console_mode: max
    editor: false
  hooks:
    - systemd
    - autodetect
    - modconf
    - kms
    - keyboard
    - sd-vconsole
    - block
    - sd-encrypt
    - filesystems
    - fsck

packages:
  profile: base
  base:
    - base
    - linux
    - linux-firmware
    - btrfs-progs
  desktops:
    kde: []
    gnome: []
    hyprland: []
  display_manager: []

gpu:
  enabled: false
  vendor: none
  driver: ''
  drivers:
    amd: []
    intel: []
    nouveau: []
    nvidia_dkms: []
    nvidia_open: []

snapper:
  enabled: true
  allow_groups:
    - wheel
  root:
    subvolume: /
    timeline: true
    cleanup: true
    retention:
      hourly: 5
      daily: 7
      weekly: 4
      monthly: 6
      yearly: 2
  home:
    subvolume: /home
    timeline: true
    cleanup: true
    retention:
      hourly: 5
      daily: 7
      weekly: 4
      monthly: 3
      yearly: 1
  snap_pac:
    enabled: true

docker:
  enabled: false
  storage_driver: overlay2
  data_root: /var/lib/docker
  access_group: docker_access

migration:
  enabled: false
  preserve_home: true
  preserve_secure_boot_keys: true
  preserve_ssh_keys: true
  additional_paths: []
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)
    return load_main_yaml_config(config_file)


@pytest.fixture
def runtime_state() -> RuntimeConfig:
    state = create_default_runtime_config()
    state.target_disk = "/dev/loop0"
    state.luks_password = "testpassword"
    state.source_luks_password = ""
    state.user_password = "userpassword"
    state.non_interactive = True
    state.selected_kernels = ["linux"]
    state.package_profile = "base"
    return state
