from __future__ import annotations

import ctypes
import os
import time
from typing import Dict


ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


class SingleInstance:
    """Windows named mutex plus small named events for second-launch commands."""

    COMMANDS = ("show", "check", "start", "exit")

    def __init__(self, name: str = "CpolarAddressNotifier") -> None:
        self.name = name
        self.mutex_handle: int | None = None
        self.event_handles: Dict[str, int] = {}
        self.is_owner = False

    @property
    def _mutex_name(self) -> str:
        return rf"Local\{self.name}.Mutex"

    def _event_name(self, command: str) -> str:
        if command not in self.COMMANDS:
            raise ValueError(f"未知的单实例命令：{command}")
        return rf"Local\{self.name}.{command.title()}"

    def acquire(self) -> bool:
        if os.name != "nt":
            self.is_owner = True
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法创建 Windows 单实例锁")
        self.mutex_handle = int(handle)
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.is_owner = False
            return False

        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateEventW.restype = ctypes.c_void_p
        for command in self.COMMANDS:
            event = kernel32.CreateEventW(None, False, False, self._event_name(command))
            if not event:
                self.release()
                raise OSError(ctypes.get_last_error(), "无法创建 Windows 单实例事件")
            self.event_handles[command] = int(event)
        self.is_owner = True
        return True

    def poll(self, command: str) -> bool:
        if os.name != "nt" or not self.is_owner:
            return False
        handle = self.event_handles.get(command)
        if not handle:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0

    def signal(self, command: str, retry_seconds: float = 1.0) -> bool:
        if os.name != "nt":
            return False
        event_name = self._event_name(command)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        kernel32.SetEvent.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        deadline = time.monotonic() + retry_seconds
        while True:
            handle = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, event_name)
            if handle:
                try:
                    return bool(kernel32.SetEvent(handle))
                finally:
                    kernel32.CloseHandle(handle)
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def release(self) -> None:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            for handle in self.event_handles.values():
                kernel32.CloseHandle(handle)
            if self.mutex_handle:
                kernel32.CloseHandle(self.mutex_handle)
        self.event_handles.clear()
        self.mutex_handle = None
        self.is_owner = False

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def signal_running_instance(command: str) -> bool:
    return SingleInstance().signal(command)
