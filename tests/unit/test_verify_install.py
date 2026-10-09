import os
import re
import subprocess
from pathlib import Path

import pytest

VERIFY_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "verify_install.sh"
EFI_GLOBAL_VARIABLE_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
# attributes NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS, as the kernel prefixes them
EFI_VARIABLE_ATTRIBUTES = b"\x06\x00\x00\x00"
UFW_DEFAULTS_OF_A_FRESH_INSTALL = "Default: deny (incoming), allow (outgoing), deny (routed)"
ANSI_COLOR = re.compile(r"\x1b\[[0-9;]*m")

# sbctl, ufw and systemctl stand in for the installed system's tools: shell functions win
# over commands, and these answer like a healthy system
HEALTHY_SYSTEM_TOOLS = """
sbctl() { :; }
systemctl() { return 0; }
ufw() {
    if [[ "$*" == "status verbose" ]]; then
        printf 'Status: active\\nLogging: on (low)\\n%s\\n' "$UFW_DEFAULTS"
    else
        echo "Status: active"
    fi
}
"""


def run_check(check_function: str, environment: dict[str, str]) -> list[str]:
    script = f"source {VERIFY_SCRIPT}\n{HEALTHY_SYSTEM_TOOLS}\n{check_function}"
    completed = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **environment},
    )
    return [ANSI_COLOR.sub("", line).strip() for line in completed.stdout.splitlines()]


@pytest.fixture
def efi_variables(tmp_path):
    def with_secure_boot_value(value: int | None) -> Path:
        directory = tmp_path / "efivars"
        directory.mkdir()
        if value is not None:
            variable = directory / f"SecureBoot-{EFI_GLOBAL_VARIABLE_GUID}"
            variable.write_bytes(EFI_VARIABLE_ATTRIBUTES + bytes([value]))
        return directory

    return with_secure_boot_value


def secure_boot_check(efi_variables_directory: Path) -> list[str]:
    return run_check(
        f"EFI_VARIABLES_DIRECTORY={efi_variables_directory}; verify_secure_boot",
        {"UFW_DEFAULTS": UFW_DEFAULTS_OF_A_FRESH_INSTALL},
    )


class TestSecureBootCheck:
    def test_enforced_secure_boot_passes(self, efi_variables):
        output = secure_boot_check(efi_variables(1))

        assert "✓ Secure Boot is ENABLED" in output
        assert not any(line.startswith("⚠") for line in output)

    def test_disabled_secure_boot_fails(self, efi_variables):
        output = secure_boot_check(efi_variables(0))

        assert "✗ Secure Boot is DISABLED" in output

    def test_firmware_without_the_variable_is_a_warning(self, efi_variables):
        output = secure_boot_check(efi_variables(None))

        assert any(line.startswith("⚠ Could not determine Secure Boot state") for line in output)


def firewall_check(ufw_defaults: str) -> list[str]:
    return run_check("verify_firewall", {"UFW_DEFAULTS": ufw_defaults})


class TestFirewallPolicyCheck:
    def test_defaults_of_a_fresh_install_pass(self):
        output = firewall_check(UFW_DEFAULTS_OF_A_FRESH_INSTALL)

        assert "✓ Default incoming: DENY" in output
        assert "✓ Default outgoing: ALLOW" in output
        assert not any(line.startswith("⚠") for line in output)

    def test_blocked_outgoing_traffic_is_a_warning_naming_the_policy(self):
        output = firewall_check("Default: deny (incoming), deny (outgoing), disabled (routed)")

        assert "✓ Default incoming: DENY" in output
        assert "⚠ Default outgoing policy is deny, expected allow" in output

    def test_missing_policy_line_is_a_warning(self):
        output = firewall_check("")

        assert "⚠ Default incoming policy is unknown, expected deny" in output
