import base64

import pytest

from arch_installer.core import secrets
from arch_installer.core.secrets import NEW_SECRET_COST, decrypt_secret, encrypt_secret
from arch_installer.errors import ConfigurationError

LUKS = "luks_password"
USER = "user_password"


def blob_parts(blob: str) -> list[str]:
    return blob.split("$")


class TestSecretCrypto:
    def test_round_trips_through_the_same_key(self):
        encrypted = encrypt_secret("disk-password", "key", LUKS)

        assert decrypt_secret(encrypted, "key", LUKS) == "disk-password"

    def test_round_trips_the_empty_string(self):
        assert decrypt_secret(encrypt_secret("", "key", LUKS), "key", LUKS) == ""

    def test_encrypting_the_same_input_twice_differs(self):
        # a fresh salt and nonce every time
        first, second = encrypt_secret("pw", "key", LUKS), encrypt_secret("pw", "key", LUKS)

        assert blob_parts(first)[3] != blob_parts(second)[3]
        assert first != second

    def test_wrong_key_is_rejected(self):
        encrypted = encrypt_secret("secret", "right", LUKS)

        with pytest.raises(ConfigurationError, match="Failed to decrypt"):
            decrypt_secret(encrypted, "wrong", LUKS)

    def test_blob_of_one_setting_does_not_open_as_another(self):
        encrypted = encrypt_secret("disk-password", "key", LUKS)

        with pytest.raises(ConfigurationError, match="Failed to decrypt"):
            decrypt_secret(encrypted, "key", USER)

    def test_altered_salt_is_rejected(self):
        parts = blob_parts(encrypt_secret("secret", "key", LUKS))
        parts[3] = base64.b64encode(b"\x00" * 16).decode()

        with pytest.raises(ConfigurationError, match="Failed to decrypt"):
            decrypt_secret("$".join(parts), "key", LUKS)

    def test_old_sha256_format_asks_to_encrypt_again(self):
        old_blob = base64.b64encode(b"\x01" * 40).decode()

        with pytest.raises(ConfigurationError, match="make encrypt-secrets"):
            decrypt_secret(old_blob, "key", LUKS)

    def test_blob_demanding_an_unreasonable_cost_is_refused(self):
        parts = blob_parts(encrypt_secret("secret", "key", LUKS))
        parts[2] = "m=99999999,t=3,p=4"

        with pytest.raises(ConfigurationError, match="unreasonable cost"):
            decrypt_secret("$".join(parts), "key", LUKS)

    def test_malformed_payload_is_rejected(self):
        parts = blob_parts(encrypt_secret("secret", "key", LUKS))
        parts[4] = "not-base64!!"

        with pytest.raises(ConfigurationError, match="invalid payload"):
            decrypt_secret("$".join(parts), "key", LUKS)


class TestProductionCost:
    def test_new_secrets_record_the_argon2id_cost_they_were_made_with(self, monkeypatch):
        # the other tests run with a cheap cost (see conftest); this one uses the real one
        monkeypatch.setattr(secrets, "NEW_SECRET_COST", NEW_SECRET_COST)

        encrypted = encrypt_secret("secret", "key", LUKS)

        assert blob_parts(encrypted)[:3] == ["dali-v2", "argon2id", "m=262144,t=3,p=4"]
        assert decrypt_secret(encrypted, "key", LUKS) == "secret"
