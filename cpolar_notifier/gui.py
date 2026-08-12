from __future__ import annotations

import os
import queue
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .app import resolved_paths
from .autostart import (
    is_enabled as autostart_is_enabled,
    set_enabled as set_autostart_enabled,
)
from .config import ConfigStore, app_data_dir
from .discovery import TunnelDiscovery
from .models import TunnelInfo
from .monitor import CheckReport, MonitorEngine, StateStore
from .notifications import NotificationResult, build_provider
from .secrets import SecretStore, SecretStoreError
from .single_instance import SingleInstance
from .tray import WindowsTray


class CpolarNotifierGui:
    def __init__(
        self,
        root: tk.Tk,
        *,
        start_hidden: bool = False,
        start_monitoring: bool = False,
        instance: SingleInstance | None = None,
        enable_tray: bool = True,
    ) -> None:
        self.root = root
        self.root.title("cpolar 地址监控")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)

        self.config_store = ConfigStore()
        self.secret_store = SecretStore()
        self.state_store = StateStore()
        self.config = self.config_store.load()
        self.engine: MonitorEngine | None = None
        self.engine_key: tuple[str, str] | None = None
        self.tunnels: Dict[str, TunnelInfo] = {}
        self.selected_tunnels = {
            str(name) for name in self.config.get("selected_tunnels", [])
        }
        self.monitoring = False
        self.monitor_busy = False
        self.monitor_after_id: str | None = None
        self.instance = instance
        self._start_monitoring_pending = start_monitoring
        self._exiting = False
        self._tray_error = False
        self._settings_dirty = False
        self._loading_settings = False
        self._autosave_after_id: str | None = None
        self.control_queue: "queue.Queue[str]" = queue.Queue()
        self.tray = WindowsTray(self.control_queue) if enable_tray else None

        self.status_var = tk.StringVar(value="正在读取 cpolar 隧道…")
        self.settings_status_var = tk.StringVar(value="设置已从本机加载")
        self.config_path_var = tk.StringVar()
        self.log_path_var = tk.StringVar()
        self.interval_var = tk.IntVar(value=int(self.config.get("interval_seconds", 60)))
        self.first_seen_var = tk.BooleanVar(
            value=bool(self.config.get("notify_on_first_seen", True))
        )
        self.autostart_var = tk.BooleanVar(value=autostart_is_enabled())

        providers = self.config.get("providers", {})
        serverchan = providers.get("serverchan", {})
        smtp = providers.get("smtp", {})
        telegram = providers.get("telegram", {})
        self.serverchan_enabled_var = tk.BooleanVar(value=bool(serverchan.get("enabled")))
        self.serverchan_key_var = tk.StringVar(value=self._read_secret("serverchan.send_key"))
        self.smtp_enabled_var = tk.BooleanVar(value=bool(smtp.get("enabled")))
        self.smtp_host_var = tk.StringVar(value=str(smtp.get("host") or "smtp.qq.com"))
        self.smtp_port_var = tk.IntVar(value=int(smtp.get("port") or 465))
        self.smtp_security_var = tk.StringVar(value=str(smtp.get("security") or "SSL"))
        self.smtp_username_var = tk.StringVar(value=str(smtp.get("username") or ""))
        self.smtp_sender_var = tk.StringVar(value=str(smtp.get("sender") or ""))
        self.smtp_recipients_var = tk.StringVar(value=str(smtp.get("recipients") or ""))
        self.smtp_password_var = tk.StringVar(value=self._read_secret("smtp.password"))
        self.telegram_enabled_var = tk.BooleanVar(value=bool(telegram.get("enabled")))
        self.telegram_token_var = tk.StringVar(value=self._read_secret("telegram.bot_token"))
        self.telegram_chat_id_var = tk.StringVar(value=str(telegram.get("chat_id") or ""))

        config_path, log_path = resolved_paths(self.config)
        self.config_path_var.set(str(config_path))
        self.log_path_var.set(str(log_path))

        self._configure_style()
        self._build_ui()
        self._register_setting_tracking()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._ensure_engine(self.config)
        stop_file = app_data_dir() / "monitor.stop"
        try:
            stop_file.unlink()
        except FileNotFoundError:
            pass
        if self.tray and not self.tray.start():
            self._append_log(f"系统托盘启动失败：{self.tray.error or '当前系统不支持'}")
            self.tray = None
        self._update_tray()
        if start_hidden and self.tray:
            self.root.withdraw()
        self.root.after(150, self.refresh_tunnels)
        self.root.after(200, self._poll_control_commands)

    def _read_secret(self, key: str) -> str:
        try:
            return self.secret_store.get(key)
        except SecretStoreError:
            return ""

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=30)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Subtitle.TLabel", foreground="#586174")
        style.configure("Status.TLabel", foreground="#245b91")
        style.configure("Card.TLabelframe", padding=12)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="cpolar 地址监控", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="自动发现随机公网地址，变化后通过微信、邮件或 Telegram 通知",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, rowspan=2, sticky="e", padx=(20, 0)
        )

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.monitor_tab = ttk.Frame(notebook, padding=14)
        self.notification_tab = ttk.Frame(notebook, padding=14)
        self.log_tab = ttk.Frame(notebook, padding=14)
        notebook.add(self.monitor_tab, text="隧道监控")
        notebook.add(self.notification_tab, text="通知设置")
        notebook.add(self.log_tab, text="运行记录")
        self._build_monitor_tab()
        self._build_notification_tab()
        self._build_log_tab()

    def _build_monitor_tab(self) -> None:
        tab = self.monitor_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        path_frame = ttk.LabelFrame(tab, text="cpolar 本地数据", style="Card.TLabelframe")
        path_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        path_frame.columnconfigure(1, weight=1)
        ttk.Label(path_frame, text="配置文件").grid(row=0, column=0, sticky="w")
        ttk.Entry(path_frame, textvariable=self.config_path_var).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(path_frame, text="选择…", command=self._browse_config).grid(row=0, column=2)
        ttk.Label(path_frame, text="服务日志").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(path_frame, textvariable=self.log_path_var).grid(
            row=1, column=1, sticky="ew", padx=8, pady=(8, 0)
        )
        ttk.Button(path_frame, text="选择…", command=self._browse_log).grid(
            row=1, column=2, pady=(8, 0)
        )

        list_frame = ttk.LabelFrame(tab, text="选择需要通知的隧道", style="Card.TLabelframe")
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        columns = ("watch", "name", "proto", "url", "local", "status", "updated")
        self.tunnel_tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        headings = {
            "watch": "通知",
            "name": "隧道名称",
            "proto": "协议",
            "url": "当前公网地址",
            "local": "本地地址",
            "status": "状态",
            "updated": "地址时间",
        }
        widths = {
            "watch": 58,
            "name": 130,
            "proto": 65,
            "url": 335,
            "local": 145,
            "status": 80,
            "updated": 145,
        }
        for column in columns:
            self.tunnel_tree.heading(column, text=headings[column])
            self.tunnel_tree.column(
                column,
                width=widths[column],
                minwidth=45,
                stretch=column in {"url", "local"},
                anchor="center" if column in {"watch", "proto", "status"} else "w",
            )
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tunnel_tree.yview)
        self.tunnel_tree.configure(yscrollcommand=scrollbar.set)
        self.tunnel_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tunnel_tree.bind("<Button-1>", self._on_tree_click)
        self.tunnel_tree.bind("<Double-1>", self._copy_address)

        row_buttons = ttk.Frame(list_frame)
        row_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(row_buttons, text="刷新隧道", command=self.refresh_tunnels).pack(side="left")
        ttk.Button(row_buttons, text="全部勾选", command=self._select_all).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(row_buttons, text="全部取消", command=self._select_none).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(row_buttons, text="复制地址", command=self._copy_address).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(row_buttons, text="立即检查", command=self.check_now).pack(side="right")

        options = ttk.LabelFrame(tab, text="监控运行", style="Card.TLabelframe")
        options.grid(row=2, column=0, sticky="ew")
        ttk.Label(options, text="检查间隔（秒）").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            options,
            from_=15,
            to=3600,
            increment=15,
            width=8,
            textvariable=self.interval_var,
        ).grid(row=0, column=1, sticky="w", padx=(8, 22))
        ttk.Checkbutton(
            options, text="首次发现地址也通知", variable=self.first_seen_var
        ).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            options, text="登录 Windows 后自动后台监控", variable=self.autostart_var
        ).grid(row=0, column=3, sticky="w", padx=(22, 0))

        controls = ttk.Frame(options)
        controls.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Button(controls, text="保存全部设置", command=self.save_settings).pack(side="left")
        self.monitor_button = ttk.Button(
            controls, text="开始监听", command=self.toggle_monitoring
        )
        self.monitor_button.pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="隐藏到系统托盘", command=self._hide_to_tray).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(controls, text="退出程序", command=self._exit_application).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            controls,
            text="关闭窗口只会隐藏到托盘；右键托盘图标可暂停监听或退出。",
            style="Subtitle.TLabel",
        ).pack(side="right")

    def _build_notification_tab(self) -> None:
        self.notification_tab.columnconfigure(0, weight=1)
        self.notification_tab.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self.notification_tab)
        notebook.grid(row=0, column=0, sticky="nsew")
        serverchan_tab = ttk.Frame(notebook, padding=18)
        smtp_tab = ttk.Frame(notebook, padding=18)
        telegram_tab = ttk.Frame(notebook, padding=18)
        notebook.add(serverchan_tab, text="微信 Server酱")
        notebook.add(smtp_tab, text="邮件 / QQ邮箱")
        notebook.add(telegram_tab, text="Telegram")
        self._build_serverchan_tab(serverchan_tab)
        self._build_smtp_tab(smtp_tab)
        self._build_telegram_tab(telegram_tab)

        footer = ttk.Frame(self.notification_tab)
        footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(
            footer,
            text="凭据使用 Windows DPAPI 加密，仅当前 Windows 用户可以解密；不会写入项目目录或运行日志。",
            style="Subtitle.TLabel",
        ).pack(side="left")
        ttk.Button(footer, text="保存全部设置", command=self.save_settings).pack(side="right")
        ttk.Label(
            footer,
            textvariable=self.settings_status_var,
            style="Status.TLabel",
        ).pack(side="right", padx=(0, 14))

    def _build_serverchan_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            frame, text="启用微信通知", variable=self.serverchan_enabled_var
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="SendKey").grid(row=1, column=0, sticky="w", pady=(16, 0))
        ttk.Entry(frame, textvariable=self.serverchan_key_var, show="●").grid(
            row=1, column=1, sticky="ew", padx=(12, 0), pady=(16, 0)
        )
        ttk.Label(
            frame,
            text=(
                "推荐国内使用。它通过 Server酱的微信服务通道推送，不控制个人微信客户端；"
                "免费版目前每天 5 条，地址变化会合并为一条。"
            ),
            wraplength=760,
            justify="left",
            style="Subtitle.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(18, 0))
        ttk.Button(
            actions,
            text="打开 Server酱获取 SendKey",
            command=lambda: webbrowser.open("https://sct.ftqq.com/"),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="测试微信通知",
            command=lambda: self.test_provider("serverchan"),
        ).pack(side="left", padx=(10, 0))

    def _build_smtp_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        ttk.Checkbutton(frame, text="启用邮件通知", variable=self.smtp_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        presets = ttk.Frame(frame)
        presets.grid(row=0, column=2, columnspan=2, sticky="e")
        ttk.Button(
            presets, text="QQ 邮箱推荐设置", command=self._apply_qq_preset
        ).pack(side="left")
        ttk.Button(
            presets, text="163 邮箱推荐设置", command=self._apply_163_preset
        ).pack(side="left", padx=(8, 0))
        fields = [
            ("SMTP 服务器", self.smtp_host_var, 1, 0),
            ("端口", self.smtp_port_var, 1, 2),
            ("登录邮箱", self.smtp_username_var, 2, 0),
            ("发件邮箱", self.smtp_sender_var, 2, 2),
            ("收件邮箱", self.smtp_recipients_var, 3, 0),
        ]
        for label, variable, row, column in fields:
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", pady=(14, 0))
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(
                row=row,
                column=column + 1,
                columnspan=3 if row == 3 else 1,
                sticky="ew",
                padx=(10, 18 if column == 0 and row != 3 else 0),
                pady=(14, 0),
            )
        ttk.Label(frame, text="加密方式").grid(row=4, column=0, sticky="w", pady=(14, 0))
        ttk.Combobox(
            frame,
            textvariable=self.smtp_security_var,
            values=("SSL", "STARTTLS", "NONE"),
            state="readonly",
            width=12,
        ).grid(row=4, column=1, sticky="w", padx=(10, 18), pady=(14, 0))
        ttk.Label(frame, text="授权码 / SMTP 密码").grid(
            row=4, column=2, sticky="w", pady=(14, 0)
        )
        ttk.Entry(frame, textvariable=self.smtp_password_var, show="●").grid(
            row=4, column=3, sticky="ew", padx=(10, 0), pady=(14, 0)
        )
        ttk.Label(
            frame,
            text=(
                "QQ 邮箱请使用网页端开启 SMTP 后生成的授权码，不要填写 QQ 登录密码。"
                "收件邮箱可以填写多个，用逗号分隔；发到 QQ 邮箱后可由 QQ/微信邮箱提醒接收。"
            ),
            wraplength=820,
            justify="left",
            style="Subtitle.TLabel",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(16, 0))
        ttk.Button(
            frame, text="测试邮件通知", command=lambda: self.test_provider("smtp")
        ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(18, 0))

    def _build_telegram_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            frame, text="启用 Telegram 通知", variable=self.telegram_enabled_var
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Bot Token").grid(row=1, column=0, sticky="w", pady=(16, 0))
        ttk.Entry(frame, textvariable=self.telegram_token_var, show="●").grid(
            row=1, column=1, sticky="ew", padx=(12, 0), pady=(16, 0)
        )
        ttk.Label(frame, text="Chat ID").grid(row=2, column=0, sticky="w", pady=(14, 0))
        ttk.Entry(frame, textvariable=self.telegram_chat_id_var).grid(
            row=2, column=1, sticky="ew", padx=(12, 0), pady=(14, 0)
        )
        ttk.Label(
            frame,
            text=(
                "先在 Telegram 中通过 @BotFather 创建 Bot，再主动给自己的 Bot 发一条消息。"
                "Bot API 是 Telegram 官方免费接口，不会模拟登录个人账号。"
            ),
            wraplength=780,
            justify="left",
            style="Subtitle.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(16, 0))
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=2, sticky="w", pady=(18, 0))
        ttk.Button(
            actions,
            text="打开 BotFather",
            command=lambda: webbrowser.open("https://t.me/BotFather"),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="测试 Telegram 通知",
            command=lambda: self.test_provider("telegram"),
        ).pack(side="left", padx=(10, 0))

    def _build_log_tab(self) -> None:
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.log_tab,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            padx=10,
            pady=10,
        )
        scrollbar = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        actions = ttk.Frame(self.log_tab)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="清空界面记录", command=self._clear_log).pack(side="left")
        ttk.Button(actions, text="打开数据目录", command=self._open_data_dir).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            actions,
            text="日志不会记录 SendKey、邮箱授权码或 Bot Token。",
            style="Subtitle.TLabel",
        ).pack(side="right")

    def _browse_config(self) -> None:
        value = filedialog.askopenfilename(
            title="选择 cpolar.yml",
            filetypes=(("YAML", "*.yml *.yaml"), ("所有文件", "*.*")),
            initialdir=str(Path(self.config_path_var.get()).parent),
        )
        if value:
            self.config_path_var.set(value)

    def _browse_log(self) -> None:
        value = filedialog.askopenfilename(
            title="选择 cpolar 服务日志",
            filetypes=(("日志文件", "*.log *.*"), ("所有文件", "*.*")),
            initialdir=str(Path(self.log_path_var.get()).parent),
        )
        if value:
            self.log_path_var.set(value)

    def _register_setting_tracking(self) -> None:
        variables = (
            self.config_path_var,
            self.log_path_var,
            self.interval_var,
            self.first_seen_var,
            self.autostart_var,
            self.serverchan_enabled_var,
            self.serverchan_key_var,
            self.smtp_enabled_var,
            self.smtp_host_var,
            self.smtp_port_var,
            self.smtp_security_var,
            self.smtp_username_var,
            self.smtp_sender_var,
            self.smtp_recipients_var,
            self.smtp_password_var,
            self.telegram_enabled_var,
            self.telegram_token_var,
            self.telegram_chat_id_var,
        )
        for variable in variables:
            variable.trace_add("write", self._mark_settings_dirty)

    def _mark_settings_dirty(self, *_args: object) -> None:
        if self._loading_settings or self._exiting:
            return
        self._settings_dirty = True
        self.settings_status_var.set("有未保存修改，稍后自动保存…")
        if self._autosave_after_id:
            self.root.after_cancel(self._autosave_after_id)
        self._autosave_after_id = self.root.after(1200, self._autosave_settings)

    def _autosave_settings(self) -> None:
        self._autosave_after_id = None
        if self._settings_dirty:
            self.save_settings(show_confirmation=False, automatic=True)

    def _reload_settings_from_disk(self) -> None:
        if self._settings_dirty:
            return
        try:
            config = self.config_store.load()
            providers = config.get("providers", {})
            serverchan = providers.get("serverchan", {})
            smtp = providers.get("smtp", {})
            telegram = providers.get("telegram", {})
            config_path, log_path = resolved_paths(config)
            self._loading_settings = True
            self.config_path_var.set(str(config_path))
            self.log_path_var.set(str(log_path))
            self.interval_var.set(int(config.get("interval_seconds", 60)))
            self.first_seen_var.set(bool(config.get("notify_on_first_seen", True)))
            self.autostart_var.set(autostart_is_enabled())
            self.serverchan_enabled_var.set(bool(serverchan.get("enabled")))
            self.serverchan_key_var.set(self._read_secret("serverchan.send_key"))
            self.smtp_enabled_var.set(bool(smtp.get("enabled")))
            self.smtp_host_var.set(str(smtp.get("host") or "smtp.qq.com"))
            self.smtp_port_var.set(int(smtp.get("port") or 465))
            self.smtp_security_var.set(str(smtp.get("security") or "SSL"))
            self.smtp_username_var.set(str(smtp.get("username") or ""))
            self.smtp_sender_var.set(str(smtp.get("sender") or ""))
            self.smtp_recipients_var.set(str(smtp.get("recipients") or ""))
            self.smtp_password_var.set(self._read_secret("smtp.password"))
            self.telegram_enabled_var.set(bool(telegram.get("enabled")))
            self.telegram_token_var.set(self._read_secret("telegram.bot_token"))
            self.telegram_chat_id_var.set(str(telegram.get("chat_id") or ""))
            self.selected_tunnels = {
                str(name) for name in config.get("selected_tunnels", [])
            }
            self.config = config
            self._ensure_engine(config)
            self._refresh_checks()
            self.settings_status_var.set("已重新载入本机保存的设置")
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self.settings_status_var.set(f"重新载入失败：{exc}")
        finally:
            self._loading_settings = False

    def _collect_config(self) -> Dict[str, Any]:
        try:
            interval = int(self.interval_var.get())
        except (TypeError, ValueError, tk.TclError) as exc:
            raise ValueError("检查间隔必须是数字") from exc
        if not 15 <= interval <= 3600:
            raise ValueError("检查间隔应在 15 到 3600 秒之间")
        return {
            "version": 1,
            "cpolar_config_path": self.config_path_var.get().strip(),
            "cpolar_log_path": self.log_path_var.get().strip(),
            "selected_tunnels": sorted(self.selected_tunnels, key=str.casefold),
            "interval_seconds": interval,
            "notify_on_first_seen": bool(self.first_seen_var.get()),
            "providers": {
                "serverchan": {"enabled": bool(self.serverchan_enabled_var.get())},
                "smtp": {
                    "enabled": bool(self.smtp_enabled_var.get()),
                    "host": self.smtp_host_var.get().strip(),
                    "port": int(self.smtp_port_var.get()),
                    "security": self.smtp_security_var.get().strip().upper(),
                    "username": self.smtp_username_var.get().strip(),
                    "sender": self.smtp_sender_var.get().strip(),
                    "recipients": self.smtp_recipients_var.get().strip(),
                },
                "telegram": {
                    "enabled": bool(self.telegram_enabled_var.get()),
                    "chat_id": self.telegram_chat_id_var.get().strip(),
                },
            },
        }

    def _ensure_engine(self, config: Mapping[str, Any]) -> None:
        config_path, log_path = resolved_paths(config)
        key = (str(config_path.resolve()), str(log_path.resolve()))
        if self.engine is None or key != self.engine_key:
            self.engine = MonitorEngine(
                TunnelDiscovery(config_path, log_path), self.secret_store, self.state_store
            )
            self.engine_key = key

    def save_settings(
        self, show_confirmation: bool = True, automatic: bool = False
    ) -> bool:
        if self._autosave_after_id:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except tk.TclError:
                pass
            self._autosave_after_id = None
        try:
            config = self._collect_config()
            self.secret_store.set_many(
                {
                    "serverchan.send_key": self.serverchan_key_var.get().strip(),
                    "smtp.password": self.smtp_password_var.get(),
                    "telegram.bot_token": self.telegram_token_var.get().strip(),
                }
            )
            self.config_store.save(config)
            set_autostart_enabled(bool(self.autostart_var.get()))
            self.config = config
            self._ensure_engine(config)
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self.settings_status_var.set(f"保存失败：{exc}")
            if not automatic:
                messagebox.showerror("保存失败", str(exc), parent=self.root)
            return False
        self._settings_dirty = False
        self._append_log("设置已保存。")
        self.status_var.set("设置已保存")
        self.settings_status_var.set(
            f"已自动保存 {datetime.now().strftime('%H:%M:%S')}"
            if automatic
            else f"已保存 {datetime.now().strftime('%H:%M:%S')}"
        )
        if show_confirmation:
            messagebox.showinfo("保存成功", "设置已经保存。", parent=self.root)
        return True

    def _run_async(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                callback = on_error or (lambda text: self._append_log(f"错误：{text}"))
                try:
                    self.root.after(0, lambda value=message: callback(value))
                except (RuntimeError, tk.TclError):
                    pass
                return
            try:
                self.root.after(0, lambda value=result: on_success(value))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=runner, daemon=True).start()

    def refresh_tunnels(self) -> None:
        try:
            config = self._collect_config()
            self._ensure_engine(config)
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            messagebox.showerror("无法刷新", str(exc), parent=self.root)
            return
        assert self.engine is not None
        self.status_var.set("正在刷新隧道…")
        self._run_async(
            self.engine.discovery.discover,
            self._set_tunnels,
            lambda error: self._show_operation_error("读取隧道失败", error),
        )

    def _set_tunnels(self, tunnels: list[TunnelInfo]) -> None:
        self.tunnels = {tunnel.name: tunnel for tunnel in tunnels}
        existing = set(self.tunnel_tree.get_children())
        for tunnel in tunnels:
            updated = (
                tunnel.updated_at.astimezone().strftime("%m-%d %H:%M:%S")
                if tunnel.updated_at
                else "—"
            )
            values = (
                "☑" if tunnel.name in self.selected_tunnels else "☐",
                tunnel.name,
                tunnel.proto or "—",
                tunnel.display_url or "等待 cpolar 生成地址",
                tunnel.local_addr or "—",
                tunnel.status_text,
                updated,
            )
            if tunnel.name in existing:
                self.tunnel_tree.item(tunnel.name, values=values)
                existing.remove(tunnel.name)
            else:
                self.tunnel_tree.insert("", "end", iid=tunnel.name, values=values)
        for stale in existing:
            self.tunnel_tree.delete(stale)
        active_count = sum(1 for tunnel in tunnels if tunnel.active is True)
        self.status_var.set(f"已发现 {len(tunnels)} 个隧道，{active_count} 个在线")
        self._append_log(f"刷新完成：发现 {len(tunnels)} 个隧道。")
        if self._start_monitoring_pending:
            self._start_monitoring_pending = False
            if self.selected_tunnels:
                self._set_monitoring(True, silent=True)
            else:
                self._append_log("尚未选择隧道，开机启动后保持暂停监听。")
                self._update_tray()

    def _on_tree_click(self, event: tk.Event) -> None:
        if self.tunnel_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tunnel_tree.identify_column(event.x) != "#1":
            return
        item = self.tunnel_tree.identify_row(event.y)
        if not item:
            return
        if item in self.selected_tunnels:
            self.selected_tunnels.remove(item)
        else:
            self.selected_tunnels.add(item)
        values = list(self.tunnel_tree.item(item, "values"))
        values[0] = "☑" if item in self.selected_tunnels else "☐"
        self.tunnel_tree.item(item, values=values)
        self._mark_settings_dirty()

    def _select_all(self) -> None:
        self.selected_tunnels.update(self.tunnels)
        self._refresh_checks()
        self._mark_settings_dirty()

    def _select_none(self) -> None:
        self.selected_tunnels.clear()
        self._refresh_checks()
        self._mark_settings_dirty()

    def _refresh_checks(self) -> None:
        for item in self.tunnel_tree.get_children():
            values = list(self.tunnel_tree.item(item, "values"))
            values[0] = "☑" if item in self.selected_tunnels else "☐"
            self.tunnel_tree.item(item, values=values)

    def _copy_address(self, _event: tk.Event | None = None) -> None:
        selected = self.tunnel_tree.selection()
        if not selected:
            return
        tunnel = self.tunnels.get(selected[0])
        if not tunnel or not tunnel.all_urls:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(tunnel.all_urls))
        self.status_var.set(f"已复制 {tunnel.name} 的地址")

    def check_now(self, silent: bool = False) -> None:
        if not self.selected_tunnels:
            self._show_window()
            if not silent:
                messagebox.showwarning(
                    "尚未选择", "请先勾选至少一个隧道。", parent=self.root
                )
            return
        if not self.save_settings(show_confirmation=False):
            return
        assert self.engine is not None
        self.status_var.set("正在检查地址变化…")
        config = dict(self.config)
        self._run_async(
            lambda: self.engine.check_once(config),
            self._handle_report,
            lambda error: self._show_operation_error("检查失败", error),
        )

    def _handle_report(self, report: CheckReport) -> None:
        if report.tunnels:
            self._set_tunnels(report.tunnels)
        for error in report.errors:
            self._append_log(f"检查错误：{error}")
        if report.changes:
            names = "、".join(change.tunnel.name for change in report.changes)
            self._append_log(f"检测到地址更新：{names}")
        else:
            self._append_log("检查完成：地址没有变化。")
        for result in report.notifications:
            state = "成功" if result.success else "失败"
            self._append_log(f"通知 {result.provider} {state}：{result.message}")
        if report.errors:
            self.status_var.set("检查完成，但有错误")
            self._tray_error = True
        elif any(not result.success for result in report.notifications):
            self.status_var.set("地址已检查，部分通知失败")
            self._tray_error = True
        elif report.notifications:
            self.status_var.set("地址已检查，通知已发送")
            self._tray_error = False
        else:
            self.status_var.set("地址检查完成")
            self._tray_error = False
        self._update_tray()

    def toggle_monitoring(self) -> None:
        self._set_monitoring(not self.monitoring)

    def _set_monitoring(self, enabled: bool, silent: bool = False) -> bool:
        if enabled:
            if not self.selected_tunnels:
                self._show_window()
                if not silent:
                    messagebox.showwarning(
                        "尚未选择", "请先勾选至少一个隧道。", parent=self.root
                    )
                self.status_var.set("尚未选择需要监听的隧道")
                self._update_tray()
                return False
            if not self.save_settings(show_confirmation=False):
                return False
            if self.monitoring:
                return True
            self.monitoring = True
            self._tray_error = False
            self.monitor_button.configure(text="暂停监听")
            self.status_var.set("正在监听 cpolar 地址变化")
            self._append_log("地址监听已启动。")
            self._update_tray()
            self._monitor_cycle()
            return True

        self.monitoring = False
        if self.monitor_after_id:
            self.root.after_cancel(self.monitor_after_id)
            self.monitor_after_id = None
        self.monitor_button.configure(text="开始监听")
        self.status_var.set("监听已暂停")
        self._append_log("地址监听已暂停。")
        self._update_tray()
        return True

    def _monitor_cycle(self) -> None:
        if not self.monitoring or self.monitor_busy:
            return
        self.monitor_busy = True
        self.config = self.config_store.load()
        self._ensure_engine(self.config)
        assert self.engine is not None

        def done(report: CheckReport) -> None:
            self.monitor_busy = False
            self._handle_report(report)
            if self.monitoring:
                delay = max(15, int(self.config.get("interval_seconds", 60))) * 1000
                self.monitor_after_id = self.root.after(delay, self._monitor_cycle)

        def failed(error: str) -> None:
            self.monitor_busy = False
            self._tray_error = True
            self._update_tray()
            self._show_operation_error("自动检查失败", error, popup=False)
            if self.monitoring:
                delay = max(15, int(self.config.get("interval_seconds", 60))) * 1000
                self.monitor_after_id = self.root.after(delay, self._monitor_cycle)

        self._run_async(lambda: self.engine.check_once(self.config), done, failed)

    def test_provider(self, name: str) -> None:
        if not self.save_settings(show_confirmation=False):
            return
        self.status_var.set("正在发送测试通知…")

        def work() -> NotificationResult:
            provider = build_provider(name, self.config, self.secret_store)
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            return provider.send(
                "cpolar 地址监控测试",
                f"这是一条测试通知。\n\n发送时间：{now}\n收到本消息说明通知渠道配置正确。",
            )

        def done(result: NotificationResult) -> None:
            self._append_log(
                f"测试 {result.provider} {'成功' if result.success else '失败'}：{result.message}"
            )
            self.status_var.set("测试通知成功" if result.success else "测试通知失败")
            if result.success:
                messagebox.showinfo("测试成功", result.message, parent=self.root)
            else:
                messagebox.showerror("测试失败", result.message, parent=self.root)

        self._run_async(work, done, lambda error: self._show_operation_error("测试失败", error))

    def _apply_qq_preset(self) -> None:
        self.smtp_enabled_var.set(True)
        self.smtp_host_var.set("smtp.qq.com")
        self.smtp_port_var.set(465)
        self.smtp_security_var.set("SSL")
        if self.smtp_username_var.get() and not self.smtp_sender_var.get():
            self.smtp_sender_var.set(self.smtp_username_var.get())
        message = "已填入 QQ 邮箱 SMTP：smtp.qq.com / 465 / SSL。请继续填写完整邮箱地址和 QQ 邮箱授权码。"
        self.status_var.set("QQ 邮箱推荐设置已填入")
        self.settings_status_var.set("QQ 推荐参数已填入，稍后自动保存")
        messagebox.showinfo("QQ 邮箱设置", message, parent=self.root)

    def _apply_163_preset(self) -> None:
        self.smtp_enabled_var.set(True)
        self.smtp_host_var.set("smtp.163.com")
        self.smtp_port_var.set(465)
        self.smtp_security_var.set("SSL")
        if self.smtp_username_var.get() and not self.smtp_sender_var.get():
            self.smtp_sender_var.set(self.smtp_username_var.get())
        message = "已填入 163 邮箱 SMTP：smtp.163.com / 465 / SSL。请继续填写完整邮箱地址和 163 客户端授权密码。"
        self.status_var.set("163 邮箱推荐设置已填入")
        self.settings_status_var.set("163 推荐参数已填入，稍后自动保存")
        messagebox.showinfo("163 邮箱设置", message, parent=self.root)

    def _hide_to_tray(self) -> None:
        if not self.tray:
            messagebox.showwarning(
                "系统托盘不可用",
                "当前系统无法创建托盘图标，因此不会隐藏主窗口。",
                parent=self.root,
            )
            return
        if not self.monitoring and self.selected_tunnels:
            if not self._set_monitoring(True):
                return
        elif self._settings_dirty:
            self.save_settings(show_confirmation=False, automatic=True)
        self.root.withdraw()
        self._append_log("主窗口已隐藏到 Windows 系统托盘。")

    def _show_window(self) -> None:
        self._reload_settings_from_disk()
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(250, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.root.focus_force()

    def _update_tray(self) -> None:
        if not self.tray:
            return
        if self._tray_error:
            self.tray.update(
                "error", "cpolar 地址监控：检查或通知异常", self.monitoring
            )
        elif self.monitoring:
            self.tray.update("healthy", "cpolar 地址监控：正在监听", True)
        else:
            self.tray.update("paused", "cpolar 地址监控：已暂停", False)

    def _poll_control_commands(self) -> None:
        if self._exiting:
            return
        commands: list[str] = []
        while True:
            try:
                commands.append(self.control_queue.get_nowait())
            except queue.Empty:
                break
        if self.instance:
            for command in ("show", "check", "start", "exit"):
                if self.instance.poll(command):
                    commands.append(command)

        stop_file = app_data_dir() / "monitor.stop"
        if stop_file.exists():
            try:
                stop_file.unlink()
            except OSError:
                pass
            commands.append("exit")

        for command in commands:
            if command == "show":
                self._show_window()
            elif command == "check":
                self.check_now(silent=True)
            elif command == "toggle":
                self._set_monitoring(not self.monitoring, silent=True)
            elif command == "start":
                if self._set_monitoring(True, silent=True) and self.tray:
                    self.root.withdraw()
            elif command == "exit":
                self._exit_application()
                return
        self.root.after(200, self._poll_control_commands)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _open_data_dir(self) -> None:
        path = app_data_dir()
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    def _show_operation_error(self, title: str, error: str, popup: bool = True) -> None:
        self.status_var.set(title)
        self._append_log(f"{title}：{error}")
        self._tray_error = True
        self._update_tray()
        if popup:
            messagebox.showerror(title, error, parent=self.root)

    def _on_close(self) -> None:
        if self.tray:
            if self._settings_dirty:
                self.save_settings(show_confirmation=False, automatic=True)
            self.root.withdraw()
            self._append_log("主窗口已关闭，程序继续在系统托盘运行。")
            return
        if self.monitoring and not messagebox.askokcancel(
            "退出程序",
            "系统托盘不可用。退出后地址监听会停止。\n\n确定退出吗？",
            parent=self.root,
        ):
            return
        self._exit_application()

    def _exit_application(self) -> None:
        if self._exiting:
            return
        if self._settings_dirty:
            self.save_settings(show_confirmation=False, automatic=True)
        self._exiting = True
        self.monitoring = False
        if self.monitor_after_id:
            try:
                self.root.after_cancel(self.monitor_after_id)
            except tk.TclError:
                pass
            self.monitor_after_id = None
        if self.tray:
            self.tray.close()
        self.root.destroy()


def run_gui(
    *,
    start_hidden: bool = False,
    start_monitoring: bool = False,
    instance: SingleInstance | None = None,
    enable_tray: bool = True,
) -> int:
    root = tk.Tk()
    CpolarNotifierGui(
        root,
        start_hidden=start_hidden,
        start_monitoring=start_monitoring,
        instance=instance,
        enable_tray=enable_tray,
    )
    root.mainloop()
    return 0
