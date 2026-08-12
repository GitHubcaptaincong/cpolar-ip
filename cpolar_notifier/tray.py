from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import wintypes
from typing import Literal


TrayState = Literal["healthy", "paused", "error"]

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_UPDATE = WM_APP + 2

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_GRAYED = 0x00000001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

CMD_SHOW = 1001
CMD_CHECK = 1002
CMD_TOGGLE = 1003
CMD_EXIT = 1004


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


if os.name == "nt":
    LRESULT = ctypes.c_ssize_t
    WPARAM_T = ctypes.c_size_t
    LPARAM_T = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        WPARAM_T,
        LPARAM_T,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]


_WINDOWS: dict[int, "WindowsTray"] = {}


def _windows_wndproc(
    hwnd: wintypes.HWND,
    message: int,
    wparam: wintypes.WPARAM,
    lparam: wintypes.LPARAM,
) -> int:
    tray = _WINDOWS.get(int(hwnd))
    if tray is not None:
        try:
            return tray._wndproc(hwnd, message, wparam, lparam)
        except Exception:
            return tray._def_window_proc(hwnd, message, wparam, lparam)
    if os.name == "nt":
        return int(ctypes.windll.user32.DefWindowProcW(hwnd, message, wparam, lparam))
    return 0


_WNDPROC_CALLBACK = WNDPROC(_windows_wndproc) if os.name == "nt" else None


class WindowsTray:
    """Dependency-free Windows notification-area icon with a context menu."""

    def __init__(self, commands: "queue.Queue[str]") -> None:
        self.commands = commands
        self._state: TrayState = "paused"
        self._listening = False
        self._tooltip = "cpolar 地址监控：已暂停"
        self._hwnd: int | None = None
        self._icons: dict[TrayState, int] = {}
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self.error = ""
        self._taskbar_created = 0
        self._class_name = f"CpolarNotifierTray.{os.getpid()}.{id(self)}"

    @property
    def supported(self) -> bool:
        return os.name == "nt" and bool(self._hwnd) and not self.error

    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self._thread and self._thread.is_alive():
            return self.supported
        self._thread = threading.Thread(
            target=self._message_loop,
            name="cpolar-tray",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(5)
        return self.supported

    def update(
        self, state: TrayState, tooltip: str, listening: bool | None = None
    ) -> None:
        self._state = state
        self._listening = state == "healthy" if listening is None else listening
        self._tooltip = tooltip[:127]
        if self._hwnd and os.name == "nt":
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_UPDATE, 0, 0)

    def close(self) -> None:
        if self._hwnd and os.name == "nt":
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._closed.wait(3)

    def _configure_apis(self) -> None:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            WPARAM_T,
            LPARAM_T,
        ]
        user32.DefWindowProcW.restype = LRESULT
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(NOTIFYICONDATAW),
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    def _message_loop(self) -> None:
        try:
            self._configure_apis()
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            instance = kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = _WNDPROC_CALLBACK
            window_class.hInstance = instance
            window_class.lpszClassName = self._class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                error = ctypes.get_last_error()
                if error != 1410:  # class already exists
                    raise OSError(error, "无法注册系统托盘窗口")
            hwnd = user32.CreateWindowExW(
                0,
                self._class_name,
                "cpolar 地址监控",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                raise OSError(ctypes.get_last_error(), "无法创建系统托盘窗口")
            self._hwnd = int(hwnd)
            _WINDOWS[self._hwnd] = self
            self._taskbar_created = int(user32.RegisterWindowMessageW("TaskbarCreated"))
            self._icons = {
                "healthy": self._create_status_icon((42, 170, 92)),
                "paused": self._create_status_icon((126, 134, 148)),
                "error": self._create_status_icon((220, 62, 55)),
            }
            self._add_icon()
            self._ready.set()

            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.error = str(exc)
            self._ready.set()
        finally:
            self._remove_icon()
            if self._hwnd:
                _WINDOWS.pop(self._hwnd, None)
            self._hwnd = None
            self._destroy_icons()
            self._closed.set()

    def _create_status_icon(self, color: tuple[int, int, int]) -> int:
        width = height = 32
        row_bytes = ((width + 31) // 32) * 4
        and_mask = bytearray(row_bytes * height)
        xor_mask = bytearray(width * height * 4)
        red, green, blue = color
        center = (width - 1) / 2
        radius_squared = 12.5**2
        for y in range(height):
            source_y = height - 1 - y
            for x in range(width):
                outside = (x - center) ** 2 + (source_y - center) ** 2 > radius_squared
                if outside:
                    byte_index = y * row_bytes + x // 8
                    and_mask[byte_index] |= 0x80 >> (x % 8)
                    continue
                offset = (y * width + x) * 4
                xor_mask[offset : offset + 4] = bytes((blue, green, red, 0))
        user32 = ctypes.windll.user32
        user32.CreateIcon.argtypes = [
            wintypes.HINSTANCE,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ubyte,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.CreateIcon.restype = wintypes.HICON
        and_buffer = (ctypes.c_ubyte * len(and_mask)).from_buffer(and_mask)
        xor_buffer = (ctypes.c_ubyte * len(xor_mask)).from_buffer(xor_mask)
        icon = user32.CreateIcon(None, width, height, 1, 32, and_buffer, xor_buffer)
        if not icon:
            raise OSError(ctypes.get_last_error(), "无法创建系统托盘图标")
        return int(icon)

    def _icon_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icons.get(self._state, 0)
        data.szTip = self._tooltip[:127]
        return data

    def _add_icon(self) -> None:
        if not self._hwnd or not self._icons:
            return
        data = self._icon_data()
        if not ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError(ctypes.get_last_error(), "无法添加系统托盘图标")

    def _modify_icon(self) -> None:
        if not self._hwnd or not self._icons:
            return
        data = self._icon_data()
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _remove_icon(self) -> None:
        if os.name != "nt" or not self._hwnd:
            return
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))

    def _destroy_icons(self) -> None:
        if os.name == "nt":
            for icon in self._icons.values():
                if icon:
                    ctypes.windll.user32.DestroyIcon(icon)
        self._icons.clear()

    def _show_menu(self) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, MF_STRING, CMD_SHOW, "打开主界面")
            user32.AppendMenuW(menu, MF_STRING, CMD_CHECK, "立即检查")
            toggle_text = "暂停监听" if self._listening else "开始监听"
            user32.AppendMenuW(menu, MF_STRING, CMD_TOGGLE, toggle_text)
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "退出程序")
            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self._hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                point.x,
                point.y,
                0,
                self._hwnd,
                None,
            )
            mapping = {
                CMD_SHOW: "show",
                CMD_CHECK: "check",
                CMD_TOGGLE: "toggle",
                CMD_EXIT: "exit",
            }
            if command in mapping:
                self.commands.put(mapping[command])
        finally:
            user32.DestroyMenu(menu)

    def _wndproc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        user32 = ctypes.windll.user32
        if message == self._taskbar_created:
            self._add_icon()
            return 0
        if message == WM_UPDATE:
            self._modify_icon()
            return 0
        if message == WM_TRAY:
            mouse_message = int(lparam) & 0xFFFF
            if mouse_message == WM_LBUTTONDBLCLK:
                self.commands.put("show")
            elif mouse_message in {WM_RBUTTONUP, WM_CONTEXTMENU}:
                self._show_menu()
            return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return self._def_window_proc(hwnd, message, wparam, lparam)

    @staticmethod
    def _def_window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
        return int(ctypes.windll.user32.DefWindowProcW(hwnd, message, wparam, lparam))
