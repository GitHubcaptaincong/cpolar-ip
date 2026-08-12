from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
from pathlib import Path
from typing import Dict

from .config import app_data_dir, atomic_write_json


class SecretStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob_from_bytes(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    return blob, buffer


def protect_for_current_user(value: str) -> str:
    if os.name != "nt":
        raise SecretStoreError("安全凭据存储目前只支持 Windows")
    raw = value.encode("utf-8")
    input_blob, input_buffer = _blob_from_bytes(raw)
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "CpolarNotifier",
        None,
        None,
        None,
        flags,
        ctypes.byref(output_blob),
    ):
        raise SecretStoreError(f"Windows 凭据加密失败，错误码 {ctypes.get_last_error()}")
    try:
        protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(protected).decode("ascii")
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        del input_buffer


def unprotect_for_current_user(value: str) -> str:
    if os.name != "nt":
        raise SecretStoreError("安全凭据存储目前只支持 Windows")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise SecretStoreError("加密凭据格式无效") from exc
    input_blob, input_buffer = _blob_from_bytes(raw)
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    description = ctypes.c_wchar_p()
    flags = 0x1
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        None,
        None,
        None,
        flags,
        ctypes.byref(output_blob),
    ):
        raise SecretStoreError(f"Windows 凭据解密失败，错误码 {ctypes.get_last_error()}")
    try:
        plain = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return plain.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretStoreError("解密后的凭据不是有效文本") from exc
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
        del input_buffer


class SecretStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or app_data_dir()
        self.path = self.base_dir / "secrets.json"
        self._lock = threading.RLock()

    def _load_encrypted(self) -> Dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SecretStoreError("无法读取本地加密凭据文件") from exc
        if not isinstance(value, dict):
            raise SecretStoreError("本地加密凭据文件格式无效")
        return {str(key): str(item) for key, item in value.items()}

    def get(self, key: str, default: str = "") -> str:
        with self._lock:
            encrypted = self._load_encrypted().get(key)
            if not encrypted:
                return default
            return unprotect_for_current_user(encrypted)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            values = self._load_encrypted()
            if value:
                values[key] = protect_for_current_user(value)
            else:
                values.pop(key, None)
            atomic_write_json(self.path, values)

    def set_many(self, values: Dict[str, str]) -> None:
        with self._lock:
            encrypted = self._load_encrypted()
            for key, value in values.items():
                if value:
                    encrypted[key] = protect_for_current_user(value)
                else:
                    encrypted.pop(key, None)
            atomic_write_json(self.path, encrypted)
