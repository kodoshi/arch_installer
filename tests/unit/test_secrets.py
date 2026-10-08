import pytest

from arch_installer.core.secrets import decrypt_secret, encrypt_secret
from arch_installer.errors import ConfigurationError


class TestSecretCrypto:
    def test_encrypting_the_same_input_twice_differs(self):
        # a random nonce makes the ciphertext differ every time
        assert encrypt_secret("password", "key") != encrypt_secret("password", "key")

    def test_round_trips_through_the_same_key(self):
        encrypted = encrypt_secret("disk-password", "key")
        assert decrypt_secret(encrypted, "key") == "disk-password"

    def test_round_trips_the_empty_string(self):
        assert decrypt_secret(encrypt_secret("", "key"), "key") == ""

    def test_wrong_key_is_rejected(self):
        encrypted = encrypt_secret("secret", "right")
        with pytest.raises(ConfigurationError, match="Failed to decrypt"):
            decrypt_secret(encrypted, "wrong")

    def test_malformed_blob_is_rejected(self):
        with pytest.raises(ConfigurationError):
            decrypt_secret("not-base64!!", "key")
