from __future__ import annotations

import argparse
import logging
import logging.handlers
from pathlib import Path
from typing import Any, Mapping, Tuple

from .config import (
    ConfigStore,
    app_data_dir,
    default_cpolar_config_path,
    default_cpolar_log_path,
)
from .discovery import TunnelDiscovery
from .monitor import MonitorEngine, StateStore
from .secrets import SecretStore
from .single_instance import SingleInstance


LOGGER = logging.getLogger("cpolar_notifier")


def configure_logging() -> None:
    base_dir = app_data_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        base_dir / "app.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)


def resolved_paths(config: Mapping[str, Any]) -> Tuple[Path, Path]:
    config_value = str(config.get("cpolar_config_path") or "").strip()
    config_path = (
        Path(config_value).expanduser() if config_value else default_cpolar_config_path()
    )
    log_value = str(config.get("cpolar_log_path") or "").strip()
    log_path = (
        Path(log_value).expanduser()
        if log_value
        else default_cpolar_log_path(config_path)
    )
    return config_path, log_path


def _make_engine(
    config: Mapping[str, Any],
    secrets: SecretStore,
    state_store: StateStore,
) -> MonitorEngine:
    config_path, log_path = resolved_paths(config)
    discovery = TunnelDiscovery(config_path, log_path)
    return MonitorEngine(discovery, secrets, state_store)


def run_check_once() -> int:
    configure_logging()
    config = ConfigStore().load()
    engine = _make_engine(config, SecretStore(), StateStore())
    report = engine.check_once(config)
    for error in report.errors:
        LOGGER.error("检查失败：%s", error)
    for result in report.notifications:
        level = logging.INFO if result.success else logging.WARNING
        LOGGER.log(level, "%s：%s", result.provider, result.message)
    if report.changes:
        LOGGER.info("发现 %d 个隧道地址变化", len(report.changes))
    return 0 if not report.errors else 1


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="监控 cpolar 随机公网地址变化")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="隐藏主窗口到系统托盘并自动开始监听",
    )
    parser.add_argument("--check-once", action="store_true", help="执行一次检查后退出")
    parser.add_argument("--exit", action="store_true", help="退出已运行的应用实例")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    configure_logging()
    instance = SingleInstance()
    try:
        is_owner = instance.acquire()
        if not is_owner:
            if arguments.exit:
                instance.signal("exit")
            elif arguments.check_once:
                instance.signal("check")
            elif arguments.headless:
                instance.signal("start")
            else:
                instance.signal("show")
            LOGGER.info("应用已在运行，本次启动已转交给现有实例")
            return 0

        if arguments.exit:
            return 0
        if arguments.check_once:
            return run_check_once()

        from .gui import run_gui

        return run_gui(
            start_hidden=arguments.headless,
            start_monitoring=arguments.headless,
            instance=instance,
        )
    finally:
        instance.release()
