"""AES-256-GCM application-layer encryption (layer 2). MCP path exempt."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def new_cek() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def cek_to_b64(cek: bytes) -> str:
    return base64.urlsafe_b64encode(cek).decode("ascii")


def cek_from_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def encrypt_payload(cek: bytes, obj: Any) -> dict[str, str]:
    aes = AESGCM(cek)
    iv = os.urandom(12)
    plaintext = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ct = aes.encrypt(iv, plaintext, None)
    # last 16 bytes are tag
    ciphertext, tag = ct[:-16], ct[-16:]
    return {
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def decrypt_payload(cek: bytes, envelope: dict[str, str]) -> Any:
    aes = AESGCM(cek)
    iv = base64.b64decode(envelope["iv"])
    ciphertext = base64.b64decode(envelope["ciphertext"])
    tag = base64.b64decode(envelope["tag"])
    plaintext = aes.decrypt(iv, ciphertext + tag, None)
    return json.loads(plaintext.decode("utf-8"))


def seal_secret(key: bytes, plaintext: str) -> str:
    """Encrypt a string for DB storage; returns urlsafe blob iv.ct."""
    aes = AESGCM(key)
    iv = os.urandom(12)
    ct = aes.encrypt(iv, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(iv + ct).decode("ascii")


def open_secret(key: bytes, blob: str) -> str:
    raw = base64.urlsafe_b64decode(blob.encode("ascii"))
    iv, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
