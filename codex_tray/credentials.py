"""Encrypted credential storage. Backed by DPAPI on Windows.

Public entry points:
    load() -> dict   # may raise FileNotFoundError
    save(payload)    # idempotent
    clear()          # delete the file
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CRED_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "CodexTray" / "credentials.bin"


def _dpapi_available() -> bool:
    try:
        import win32crypt  # noqa: F401  type: ignore[import-not-found]
        return True
    except Exception:
        return False


def _dpapi_protect(plaintext: bytes) -> bytes:
    import win32crypt  # type: ignore[import-not-found]
    # Newer pywin32 returns a CRYPTPROTECT_PROMPTSTRUCT-like object with .encryptedData;
    # older builds and some 3.13 wheels return raw bytes directly. Handle both.
    result = win32crypt.CryptProtectData(plaintext, None, None, None, None, 0)
    if isinstance(result, bytes):
        return result
    # tuple fallback (name, data) or object with .encryptedData
    if isinstance(result, tuple) and len(result) >= 2:
        return result[-1] if isinstance(result[-1], (bytes, bytearray)) else bytes(result[-1])
    return bytes(result.encryptedData)


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    import win32crypt  # type: ignore[import-not-found]
    result = win32crypt.CryptUnprotectData(ciphertext, None, None, None, 0)
    if isinstance(result, bytes):
        return result
    if isinstance(result, tuple) and len(result) >= 2:
        return result[-1] if isinstance(result[-1], (bytes, bytearray)) else bytes(result[-1])
    return bytes(result.encryptedData)


def encrypt(plaintext: bytes) -> bytes:
    if _dpapi_available():
        return b"DPAPI:" + _dpapi_protect(plaintext)
    raise RuntimeError(
        "Windows DPAPI is unavailable; refusing to store credentials without encryption"
    )


def decrypt(ciphertext: bytes) -> bytes:
    if ciphertext.startswith(b"DPAPI:"):
        if not _dpapi_available():
            raise RuntimeError("credentials use DPAPI but it's unavailable on this host")
        return _dpapi_unprotect(ciphertext[len(b"DPAPI:"):])
    if ciphertext.startswith(b"B64:"):
        raise RuntimeError(
            "legacy Base64 credentials are insecure; import fresh credentials"
        )
    raise RuntimeError("unrecognized credential format")


def load() -> dict:
    if not CRED_PATH.exists():
        raise FileNotFoundError(f"No credentials at {CRED_PATH}. Run `python setup.py` first.")
    return json.loads(decrypt(CRED_PATH.read_bytes()).decode("utf-8"))


def save(payload: dict) -> None:
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRED_PATH.write_bytes(encrypt(json.dumps(payload).encode("utf-8")))


def clear() -> None:
    if CRED_PATH.exists():
        CRED_PATH.unlink()
