"""AES-256-GCM encryption of the passwords that config.yaml may store in its secrets section.

the AES key is derived from the secrets key with Argon2id and a random salt per secret, so
a leaked config.yaml can only be attacked at Argon2id speed, one secret at a time. every
blob names the setting it belongs to (AES-GCM associated data), so two blobs cannot be
swapped. a blob describes how it was made, so the cost can be raised later:

    dali-v2$argon2id$m=<memory KiB>,t=<iterations>,p=<lanes>$<salt>$<nonce + ciphertext>

with the salt and the nonce + ciphertext in base64.
"""

import base64
import binascii
import os
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from arch_installer.errors import ConfigurationError

FORMAT_VERSION = "dali-v2"
KEY_DERIVATION_NAME = "argon2id"
SALT_SIZE_BYTES = 16
NONCE_SIZE_BYTES = 12
AES_KEY_SIZE_BYTES = 32
GCM_TAG_SIZE_BYTES = 16
COST_PATTERN = re.compile(r"m=(\d+),t=(\d+),p=(\d+)")


@dataclass(frozen=True)
class Argon2Cost:
    memory_kib: int
    iterations: int
    lanes: int

    def encoded(self) -> str:
        return f"m={self.memory_kib},t={self.iterations},p={self.lanes}"


# about 0.3 s and 256 MiB per secret on a current desktop
NEW_SECRET_COST = Argon2Cost(memory_kib=262144, iterations=3, lanes=4)
# a blob demanding more than this is refused instead of exhausting memory or time
MAXIMUM_COST = Argon2Cost(memory_kib=4194304, iterations=64, lanes=64)


def _aes_key(secrets_key: str, salt: bytes, cost: Argon2Cost) -> bytes:
    key_derivation = Argon2id(
        salt=salt,
        length=AES_KEY_SIZE_BYTES,
        iterations=cost.iterations,
        lanes=cost.lanes,
        memory_cost=cost.memory_kib,
    )
    return key_derivation.derive(secrets_key.encode())


def _associated_data(setting: str) -> bytes:
    return f"{FORMAT_VERSION}:{setting}".encode()


def encrypt_secret(plaintext: str, secrets_key: str, setting: str) -> str:
    salt = os.urandom(SALT_SIZE_BYTES)
    nonce = os.urandom(NONCE_SIZE_BYTES)
    aes_key = _aes_key(secrets_key, salt, NEW_SECRET_COST)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext.encode(), _associated_data(setting))
    return "$".join(
        (
            FORMAT_VERSION,
            KEY_DERIVATION_NAME,
            NEW_SECRET_COST.encoded(),
            base64.b64encode(salt).decode(),
            base64.b64encode(nonce + ciphertext).decode(),
        )
    )


def _parse_cost(encoded: str) -> Argon2Cost:
    match = COST_PATTERN.fullmatch(encoded)
    if not match:
        raise ConfigurationError(f"Encrypted secret has malformed Argon2id parameters: {encoded}")
    cost = Argon2Cost(*(int(number) for number in match.groups()))
    if (
        not 0 < cost.memory_kib <= MAXIMUM_COST.memory_kib
        or not 0 < cost.iterations <= MAXIMUM_COST.iterations
        or not 0 < cost.lanes <= MAXIMUM_COST.lanes
    ):
        raise ConfigurationError(f"Encrypted secret asks for an unreasonable cost: {encoded}")
    return cost


def _base64_part(text: str, part_name: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ConfigurationError(f"Encrypted secret has an invalid {part_name}") from error


def decrypt_secret(blob: str, secrets_key: str, setting: str) -> str:
    parts = blob.split("$")
    if len(parts) != 5 or parts[0] != FORMAT_VERSION or parts[1] != KEY_DERIVATION_NAME:
        raise ConfigurationError(
            f"The encrypted {setting} uses an old or unknown format; "
            "run `make encrypt-secrets` to encrypt it again"
        )
    cost = _parse_cost(parts[2])
    salt = _base64_part(parts[3], "salt")
    payload = _base64_part(parts[4], "payload")
    if len(salt) != SALT_SIZE_BYTES or len(payload) < NONCE_SIZE_BYTES + GCM_TAG_SIZE_BYTES:
        raise ConfigurationError(f"The encrypted {setting} is truncated")

    nonce, ciphertext = payload[:NONCE_SIZE_BYTES], payload[NONCE_SIZE_BYTES:]
    aes_key = _aes_key(secrets_key, salt, cost)
    try:
        return AESGCM(aes_key).decrypt(nonce, ciphertext, _associated_data(setting)).decode()
    except InvalidTag as error:
        raise ConfigurationError(
            f"Failed to decrypt the {setting} (wrong key, or the secret was altered)"
        ) from error
