"""AES-GCM envelope encryption for provider API keys (ADR-0005).

MODEL_SECRET_KEY (32-byte base64) encrypts API keys at rest. The previous
key/version stays readable so MODEL_SECRET_KEY can be rotated online by
re-encrypting rows (see app/tasks/key_rotation.py).
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.common.errors import ApiError

_NONCE_BYTES = 12
_KEY_BYTES = 32


def _decode_key(raw: SecretStr, setting_name: str) -> bytes:
    value = raw.get_secret_value().strip()
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ApiError(
            "MODEL_SECRET_NOT_CONFIGURED",
            f"{setting_name} 不是有效的 base64",
            409,
        ) from exc
    if len(key) != _KEY_BYTES:
        raise ApiError(
            "MODEL_SECRET_NOT_CONFIGURED",
            f"{setting_name} 必须编码 32 字节密钥",
            409,
        )
    return key


class ModelSecretBox:
    """Encrypts new writes with the current key; decrypts current+previous."""

    def __init__(
        self,
        key: SecretStr,
        *,
        version: int = 1,
        previous_key: SecretStr | None = None,
        previous_version: int | None = None,
    ) -> None:
        if version < 1:
            raise ApiError("MODEL_SECRET_NOT_CONFIGURED", "MODEL_SECRET_KEY_VERSION 无效", 409)
        self._aes = AESGCM(_decode_key(key, "MODEL_SECRET_KEY"))
        self._version = version
        self._previous_aes: AESGCM | None = None
        self._previous_version: int | None = None
        if previous_key is not None:
            if previous_version is None or previous_version >= version:
                raise ApiError(
                    "MODEL_SECRET_NOT_CONFIGURED",
                    "MODEL_SECRET_KEY_PREVIOUS_VERSION 必须小于当前版本",
                    409,
                )
            self._previous_aes = AESGCM(_decode_key(previous_key, "MODEL_SECRET_KEY_PREVIOUS"))
            self._previous_version = previous_version

    @property
    def version(self) -> int:
        return self._version

    def encrypt(self, plaintext: str) -> tuple[bytes, bytes]:
        """Returns (ciphertext, nonce) tagged with the current key version."""
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return ciphertext, nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes, key_version: int) -> SecretStr:
        aes = self._aes if key_version == self._version else None
        if aes is None and self._previous_aes is not None:
            if key_version == self._previous_version:
                aes = self._previous_aes
        if aes is None:
            raise ApiError(
                "MODEL_SECRET_NOT_CONFIGURED",
                f"密钥版本 {key_version} 无法解密，请先轮换 MODEL_SECRET_KEY",
                409,
            )
        try:
            plaintext = aes.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ApiError("API_KEY_DECRYPT_FAILED", "API 密钥解密失败", 500) from exc
        return SecretStr(plaintext.decode("utf-8"))
