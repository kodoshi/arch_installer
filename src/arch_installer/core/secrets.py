"""
encrypted secrets handling for non-interactive installations

passwords can be stored encrypted in config.yaml and decrypted at runtime
using a symmetric key provided via environment variable
"""

import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from arch_installer.errors import ConfigurationError

# environment variable for the decryption key
SECRETS_KEY_ENV_VAR_LABEL = "ARCH_INSTALLER_SECRETS_KEY"
NON_INTERACTIVE_ENV_VAR_LABEL = "ARCH_INSTALLER_NON_INTERACTIVE_MODE"


def _get_cipher_key(key: str) -> bytes:
    """Derive a 32-byte key from the input key using SHA-256."""
    return hashlib.sha256(key.encode()).digest()


def encrypt_secret(plaintext: str, key: str) -> str:
    """
    encrypt a secret using AES-256-GCM
    """

    cipher_key = _get_cipher_key(key)
    aesgcm = AESGCM(cipher_key)
    nonce = os.urandom(12)

    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    encrypted_data = nonce + ciphertext

    return base64.b64encode(encrypted_data).decode()


def decrypt_secret(encrypted_blob: str, key: str) -> str:
    """
    decrypt a secret encrypted with encrypt_secret
    """
    try:
        encrypted_data = base64.b64decode(encrypted_blob)
    except Exception as error:
        raise ConfigurationError(f"Invalid encrypted blob format: {error}") from error

    if len(encrypted_data) < 28:
        raise ConfigurationError("Encrypted data too short")

    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]

    cipher_key = _get_cipher_key(key)
    aesgcm = AESGCM(cipher_key)

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode()
    except Exception as error:
        raise ConfigurationError(
            f"Failed to decrypt secret (wrong key or corrupted data): {error}"
        ) from error


def get_secrets_key_from_env() -> Optional[str]:
    return os.environ.get(SECRETS_KEY_ENV_VAR_LABEL)


def is_non_interactive_mode() -> bool:
    return os.environ.get(NON_INTERACTIVE_ENV_VAR_LABEL, "").lower() in (
        "1",
        "true",
        "yes",
    )


def decrypt_secrets_from_config(
    luks_encrypted: str,
    user_encrypted: str,
    key: Optional[str] = None,
) -> tuple[str, str]:
    """
    decrypt LUKS and user passwords from encrypted config values
    """
    if key is None:
        key = get_secrets_key_from_env()

    if not key:
        raise ConfigurationError(
            f"No decryption key provided. Set {SECRETS_KEY_ENV_VAR_LABEL} environment variable."
        )

    luks_password = decrypt_secret(luks_encrypted, key) if luks_encrypted else ""
    user_password = decrypt_secret(user_encrypted, key) if user_encrypted else ""

    return luks_password, user_password
