import pytest

from arch_installer.core.secrets import (
    SECRETS_KEY_ENV_VAR_LABEL,
    decrypt_secret,
    decrypt_secrets_from_config,
    encrypt_secret,
    is_non_interactive_mode,
)
from arch_installer.errors import ConfigurationError


class TestSecretHandling:
    def test_should_produce_different_outputs_when_encrypting_same_input(self):
        # random nonce should make outputs different each time
        key = "my-secret-key"
        plaintext = "test-password"

        first_encryption = encrypt_secret(plaintext, key)
        second_encryption = encrypt_secret(plaintext, key)

        assert first_encryption != second_encryption

    def test_should_raise_error_when_key_is_wrong(self):
        correct_key = "correct-key"
        wrong_key = "wrong-key"
        plaintext = "secret-data"

        encrypted = encrypt_secret(plaintext, correct_key)

        with pytest.raises(ConfigurationError, match="Failed to decrypt"):
            decrypt_secret(encrypted, wrong_key)

    def test_should_handle_empty_string_when_decrypting(self):
        key = "test-key"
        plaintext = ""

        encrypted = encrypt_secret(plaintext, key)
        decrypted = decrypt_secret(encrypted, key)

        assert decrypted == ""

    def test_should_decrypt_both_passwords_when_key_provided(self):
        key = "config-key"
        luks_pass = "luks-password-123"
        user_pass = "user-password-456"

        luks_encrypted = encrypt_secret(luks_pass, key)
        user_encrypted = encrypt_secret(user_pass, key)

        luks_decrypted, user_decrypted = decrypt_secrets_from_config(
            luks_encrypted, user_encrypted, key
        )

        assert luks_decrypted == luks_pass
        assert user_decrypted == user_pass

    def test_should_use_env_key_when_key_not_provided(self, monkeypatch):
        key = "env-key"
        monkeypatch.setenv(SECRETS_KEY_ENV_VAR_LABEL, key)

        luks_pass = "luks-secret"
        user_pass = "user-secret"

        luks_encrypted = encrypt_secret(luks_pass, key)
        user_encrypted = encrypt_secret(user_pass, key)

        luks_decrypted, user_decrypted = decrypt_secrets_from_config(
            luks_encrypted, user_encrypted
        )

        assert luks_decrypted == luks_pass
        assert user_decrypted == user_pass

    def test_should_raise_error_when_no_key_available(self, monkeypatch):
        monkeypatch.delenv(SECRETS_KEY_ENV_VAR_LABEL, raising=False)

        with pytest.raises(ConfigurationError, match="No decryption key"):
            decrypt_secrets_from_config("encrypted-luks", "encrypted-user")
