"""AES-256-GCM encryption of the passwords that config.yaml may store in its secrets section.

the decryption key comes from the environment or the TUI; see config.environment.
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from arch_installer.errors import ConfigurationError

NONCE_SIZE_BYTES = 12
GCM_TAG_SIZE_BYTES = 16


def _cipher_key(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def encrypt_secret(plaintext: str, key: str) -> str:
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(_cipher_key(key)).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_secret(encrypted_blob: str, key: str) -> str:
    try:
        encrypted_data = base64.b64decode(encrypted_blob)
    except ValueError as error:
        raise ConfigurationError(f"Invalid encrypted blob format: {error}") from error

    if len(encrypted_data) < NONCE_SIZE_BYTES + GCM_TAG_SIZE_BYTES:
        raise ConfigurationError("Encrypted data too short")

    nonce, ciphertext = encrypted_data[:NONCE_SIZE_BYTES], encrypted_data[NONCE_SIZE_BYTES:]
    try:
        return AESGCM(_cipher_key(key)).decrypt(nonce, ciphertext, None).decode()
    except Exception as error:
        raise ConfigurationError(
            "Failed to decrypt secret (wrong key or corrupted data)"
        ) from error
