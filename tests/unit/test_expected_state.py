import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from arch_installer.expected_state import (
    EXPECTED_STATE_PATH,
    expected_state_file,
    expected_state_values,
)
from arch_installer.installer import Installer
from tests.unit.conftest import build_config

VERIFY_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "verify_install.sh"


def expectations_seen_by_the_script(expectations_file: Path) -> tuple[str, dict[str, str]]:
    names = " ".join(expected_state_values(build_config()))
    script = (
        f"source {VERIFY_SCRIPT}; EXPECTED_STATE_FILE={expectations_file}; load_expectations; "
        f'for name in {names}; do printf "%s=%s\\n" "$name" "${{!name:-}}"; done'
    )
    completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    values = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if line.startswith("EXPECTED_")
    )
    return completed.stdout, values


@pytest.fixture
def expectations_file(tmp_path):
    def written(config, mode: int = 0o644) -> Path:
        path = tmp_path / "expected-state.env"
        path.write_text(expected_state_file(config))
        path.chmod(mode)
        return path

    return written


class TestExpectedStateFile:
    def test_verify_script_reads_back_every_value(self, expectations_file):
        config = build_config()

        _, seen = expectations_seen_by_the_script(expectations_file(config))

        assert seen["EXPECTED_HOSTNAME"] == config.system.hostname
        assert seen["EXPECTED_LOCALE"] == config.system.locale.full_locale
        assert seen["EXPECTED_SWAP_ENABLED"] == "true"
        assert seen["EXPECTED_KERNEL_PACKAGES"] == " ".join(config.boot.kernel_packages)

    def test_shell_syntax_in_a_value_stays_text(self, expectations_file, tmp_path):
        marker = tmp_path / "executed"
        hostile = f"host$(touch {marker})`touch {marker}`'\""
        config = build_config(system=replace(build_config().system, hostname=hostile))

        _, seen = expectations_seen_by_the_script(expectations_file(config))

        assert seen["EXPECTED_HOSTNAME"] == hostile
        assert not marker.exists()

    def test_file_writable_by_others_is_refused(self, expectations_file):
        output, seen = expectations_seen_by_the_script(
            expectations_file(build_config(), mode=0o664)
        )

        assert "writable only by it" in output
        assert seen["EXPECTED_HOSTNAME"] == ""

    def test_script_and_installer_agree_on_the_variable_names(self):
        script_names = set(re.findall(r"\bEXPECTED_[A-Z_]+", VERIFY_SCRIPT.read_text()))
        script_names.discard("EXPECTED_STATE_FILE")

        assert script_names == set(expected_state_values(build_config()))


class TestInstallerWritesExpectations:
    def test_installation_leaves_expectations_that_only_root_may_change(
        self, fake_runner, monkeypatch
    ):
        monkeypatch.setattr("arch_installer.installer.PIPELINE", ())
        config = build_config()

        Installer(config, fake_runner).install()

        target_path = f"/mnt{EXPECTED_STATE_PATH}"
        assert fake_runner.written_content(target_path) == expected_state_file(config)
        fake_runner.assert_command_called(f"chmod 644 {target_path}")
