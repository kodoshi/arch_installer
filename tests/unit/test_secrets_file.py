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


SECRET_VARIABLES = ("ARCH_INSTALLER_SECRETS_KEY", "LUKS_PASSWORD", "USER_PASSWORD", "NO_WRITE")


# stands in for the terminal behind getpass: answers the prompts in order, then has no
# more input, the way a closed stdin does
class ScriptedTerminal:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def getpass(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self._answers:
            raise EOFError
        return self._answers.pop(0)


@pytest.fixture
def terminal(monkeypatch):
    def answering(*answers: str) -> ScriptedTerminal:
        scripted = ScriptedTerminal(list(answers))
        monkeypatch.setattr("getpass.getpass", scripted.getpass)
        return scripted

    return answering


@pytest.fixture
def secrets_environment(monkeypatch, config_file):
    for variable in SECRET_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    def using(config_text: str, **variables: str):
        path = config_file(config_text)
        monkeypatch.setenv("CONFIG_PATH", str(path))
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        return path

    return using


@pytest.mark.usefixtures("restored_package_logging")
class TestSecretsCommands:
    def test_encrypted_passwords_decrypt_back_with_the_same_key(self, secrets_environment, capsys):
        secrets_environment(
            CONFIG_WITH_SECRETS,
            ARCH_INSTALLER_SECRETS_KEY="key",
            LUKS_PASSWORD='it\'s "quoted"',
            USER_PASSWORD="plain",
        )

        assert encrypt_secrets() == 0
        capsys.readouterr()
        assert decrypt_secrets() == 0

        output = capsys.readouterr().out
        assert 'LUKS: it\'s "quoted"' in output
        assert "User: plain" in output

    def test_missing_secrets_are_asked_for_without_echo(
        self, secrets_environment, terminal, capsys
    ):
        secrets_environment(CONFIG_WITH_EMPTY_SECTION)
        terminal("key", "key", "disk-secret", "disk-secret", "")

        assert encrypt_secrets() == 0
        capsys.readouterr()
        terminal("key")
        assert decrypt_secrets() == 0

        output = capsys.readouterr().out
        assert "LUKS: disk-secret" in output
        assert "User: N/A" in output

    def test_mistyped_confirmation_asks_again(self, secrets_environment, terminal):
        path = secrets_environment(CONFIG_WITH_EMPTY_SECTION, ARCH_INSTALLER_SECRETS_KEY="key")
        scripted = terminal("first", "typo", "second", "second", "")

        assert encrypt_secrets() == 0

        assert len(scripted.prompts) == 5
        assert load_config(path).secrets.luks_password_encrypted

    def test_kept_password_encrypted_with_another_key_is_refused(
        self, secrets_environment, terminal
    ):
        path = secrets_environment(
            CONFIG_WITH_SECRETS, ARCH_INSTALLER_SECRETS_KEY="new-key", LUKS_PASSWORD="secret"
        )
        terminal("")

        assert encrypt_secrets() == 1

        assert path.read_text() == CONFIG_WITH_SECRETS

    def test_no_write_prints_the_values_and_leaves_the_file_alone(
        self, secrets_environment, terminal, capsys
    ):
        path = secrets_environment(
            CONFIG_WITH_SECRETS,
            ARCH_INSTALLER_SECRETS_KEY="key",
            LUKS_PASSWORD="secret",
            NO_WRITE="true",
        )
        terminal("")

        assert encrypt_secrets() == 0

        assert path.read_text() == CONFIG_WITH_SECRETS
        assert "luks_password_encrypted:" in capsys.readouterr().out

    def test_empty_secrets_key_fails(self, secrets_environment, terminal):
        secrets_environment(CONFIG_WITH_SECRETS, LUKS_PASSWORD="secret")
        terminal("")

        assert encrypt_secrets() == 1

    def test_missing_secret_without_a_terminal_fails(self, secrets_environment, terminal):
        secrets_environment(CONFIG_WITH_SECRETS, LUKS_PASSWORD="secret")
        terminal()

        assert encrypt_secrets() == 1
