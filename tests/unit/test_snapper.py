from dataclasses import replace

from arch_installer.config.models import SnapperConfig
from arch_installer.executors.snapper import SnapperExecutor, SnapshotBootExecutor
from tests.unit.conftest import build_config


def snapper_config(**overrides) -> SnapperConfig:
    return replace(SnapperConfig(), **overrides)


class TestSnapperExecutor:
    def test_installs_snapper_when_missing(self, fake_runner):
        fake_runner.set_response("pacman -Q snapper", exit_code=1)
        fake_runner.set_response("test -f", exit_code=1)
        SnapperExecutor(build_config(), fake_runner).execute()
        fake_runner.assert_command_called("pacman -S --noconfirm snapper snap-pac")

    def test_writes_a_config_for_each_volume(self, fake_runner):
        fake_runner.set_response("pacman -Q snapper", exit_code=0)
        fake_runner.set_response("test -f", exit_code=1)
        SnapperExecutor(build_config(), fake_runner).execute()
        fake_runner.written_content("/mnt/etc/snapper/configs/root")
        fake_runner.written_content("/mnt/etc/snapper/configs/home")

    def test_enables_timeline_and_cleanup_timers(self, fake_runner):
        fake_runner.set_response("pacman -Q snapper", exit_code=0)
        fake_runner.set_response("test -f", exit_code=1)
        SnapperExecutor(build_config(), fake_runner).execute()
        fake_runner.assert_command_called("systemctl enable snapper-timeline.timer")
        fake_runner.assert_command_called("systemctl enable snapper-cleanup.timer")

    def test_writes_snap_pac_config_when_enabled(self, fake_runner):
        fake_runner.set_response("pacman -Q snapper", exit_code=0)
        fake_runner.set_response("test -f", exit_code=1)
        SnapperExecutor(build_config(snapper=snapper_config(snap_pac=True)), fake_runner).execute()
        fake_runner.written_content("/mnt/etc/snap-pac.d/root.conf")

    def test_omits_snap_pac_config_when_disabled(self, fake_runner):
        fake_runner.set_response("pacman -Q snapper", exit_code=0)
        fake_runner.set_response("test -f", exit_code=1)
        SnapperExecutor(build_config(snapper=snapper_config(snap_pac=False)), fake_runner).execute()
        fake_runner.assert_command_not_called("snap-pac.d")


class TestSnapshotBootExecutor:
    def test_deploys_the_refresh_hook_and_settings(self, fake_runner):
        config = build_config(boot=replace(build_config().boot, enable_snapshot_boot=True))
        SnapshotBootExecutor(config, fake_runner).execute()
        fake_runner.written_content("/mnt/etc/default/manage-snapshot-ukis")
        fake_runner.written_content("/mnt/etc/pacman.d/hooks/95-snapshot-uki-refresh.hook")
        fake_runner.assert_command_called("systemctl enable snapper-boot-entries.path")
