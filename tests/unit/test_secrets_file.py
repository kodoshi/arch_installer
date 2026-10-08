import logging
from pathlib import Path

import pytest

from arch_installer.cli import decrypt_secrets, encrypt_secrets
from arch_installer.config.loader import load_config
from arch_installer.config.models import EncryptedSecretsConfig
from arch_installer.config.secrets_file import write_encrypted_secrets
from arch_installer.core.log import PACKAGE_LOGGER_NAME
from arch_installer.errors import ConfigurationError

MINIMAL_CONFIG_PATH = Path(__file__).parent.parent / "data" / "minimal_config.yaml"
# the minimal test config is complete except for its secrets section, which ends the file
BASE_CONFIG = MINIMAL_CONFIG_PATH.read_text().split("\nsecrets:")[0] + "\n"
CONFIG_WITH_SECRETS = BASE_CONFIG + (
    "\nsecrets:\n"
    "  # filled by make encrypt-secrets\n"
    "  luks_password_encrypted: 'old-luks'\n"
    "  user_password_encrypted: 'old-user'\n"
)
CONFIG_WITH_EMPTY_SECTION = BASE_CONFIG + "\nsecrets:\n"
CONFIG_WITHOUT_SECTION = BASE_CONFIG


@pytest.fixture
def config_file(tmp_path):
    def write(text: str):
        path = tmp_path / "config.yaml"
        path.write_text(text)
        return path

    return write


class TestWriteEncryptedSecrets:
    def test_existing_values_are_replaced_and_comments_survive(self, config_file):
        path = config_file(CONFIG_WITH_SECRETS)

        write_encrypted_secrets(path, EncryptedSecretsConfig("new-luks", "new-user"))

        text = path.read_text()
        assert "# filled by make encrypt-secrets" in text
        assert text.startswith(BASE_CONFIG)
        assert load_config(path).secrets == EncryptedSecretsConfig("new-luks", "new-user")

    def test_empty_value_keeps_what_the_file_already_has(self, config_file):
        path = config_file(CONFIG_WITH_SECRETS)

        write_encrypted_secrets(path, EncryptedSecretsConfig(luks_password_encrypted="new-luks"))

        assert load_config(path).secrets == EncryptedSecretsConfig("new-luks", "old-user")

    def test_values_are_added_to_an_empty_secrets_section(self, config_file):
        path = config_file(CONFIG_WITH_EMPTY_SECTION)

        write_encrypted_secrets(path, EncryptedSecretsConfig("luks", "user"))

        assert load_config(path).secrets == EncryptedSecretsConfig("luks", "user")
        assert path.read_text().startswith(BASE_CONFIG)

    def test_secrets_section_is_appended_when_missing(self, config_file):
        path = config_file(CONFIG_WITHOUT_SECTION)

        write_encrypted_secrets(path, EncryptedSecretsConfig("luks", ""))

        assert load_config(path).secrets.luks_password_encrypted == "luks"

    def test_missing_config_file_is_reported(self, tmp_path):
        with pytest.raises(ConfigurationError, match="not found"):
            write_encrypted_secrets(tmp_path / "absent.yaml", EncryptedSecretsConfig("x", ""))


# the commands configure the package logger like any entry point; undo it afterwards
@pytest.fixture
def restored_package_logging():
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    handlers, level, propagate = (
        package_logger.handlers[:],
        package_logger.level,
        package_logger.propagate,
    )
    yield
    package_logger.handlers = handlers
    package_logger.setLevel(level)
    package_logger.propagate = propagate


@pytest.mark.usefixtures("restored_package_logging")
class TestSecretsCommands:
    def test_encrypted_passwords_decrypt_back_with_the_same_key(
        self, config_file, monkeypatch, capsys
    ):
        path = config_file(CONFIG_WITH_SECRETS)
        monkeypatch.setenv("CONFIG_PATH", str(path))
        monkeypatch.setenv("ARCH_INSTALLER_SECRETS_KEY", "key")
        monkeypatch.setenv("LUKS_PASSWORD", 'it\'s "quoted"')
        monkeypatch.setenv("USER_PASSWORD", "plain")

        assert encrypt_secrets() == 0
        capsys.readouterr()
        assert decrypt_secrets() == 0

        output = capsys.readouterr().out
        assert 'LUKS: it\'s "quoted"' in output
        assert "User: plain" in output

    def test_no_write_prints_the_values_and_leaves_the_file_alone(
        self, config_file, monkeypatch, capsys
    ):
        path = config_file(CONFIG_WITH_SECRETS)
        monkeypatch.setenv("CONFIG_PATH", str(path))
        monkeypatch.setenv("ARCH_INSTALLER_SECRETS_KEY", "key")
        monkeypatch.setenv("LUKS_PASSWORD", "secret")
        monkeypatch.setenv("NO_WRITE", "true")

        assert encrypt_secrets() == 0

        assert path.read_text() == CONFIG_WITH_SECRETS
        assert "luks_password_encrypted:" in capsys.readouterr().out

    def test_missing_secrets_key_fails(self, config_file, monkeypatch):
        monkeypatch.setenv("CONFIG_PATH", str(config_file(CONFIG_WITH_SECRETS)))
        monkeypatch.delenv("ARCH_INSTALLER_SECRETS_KEY", raising=False)
        monkeypatch.setenv("LUKS_PASSWORD", "secret")

        assert encrypt_secrets() == 1
