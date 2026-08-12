from __future__ import annotations

import json
import re
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Dict, Mapping, Protocol

from .secrets import SecretStore


USER_AGENT = "CpolarNotifier/0.1"


@dataclass
class NotificationResult:
    provider: str
    success: bool
    message: str


class NotificationProvider(Protocol):
    name: str
    display_name: str

    def send(self, title: str, body: str) -> NotificationResult:
        ...


def _clean_title(title: str) -> str:
    return " ".join(title.replace("\r", " ").replace("\n", " ").split())[:120]


def serverchan_endpoint(send_key: str) -> str:
    if send_key.startswith("SCT"):
        return f"https://sctapi.ftqq.com/{urllib.parse.quote(send_key, safe='')}.send"
    match = re.match(r"^sctp(\d+)t", send_key)
    if match:
        uid = match.group(1)
        return (
            f"https://{uid}.push.ft07.com/send/"
            f"{urllib.parse.quote(send_key, safe='')}.send"
        )
    raise ValueError("SendKey 应以 SCT 或 sctp 开头")


def _post_form(url: str, fields: Mapping[str, str], timeout: int = 15) -> Dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(65536).decode("utf-8", errors="replace")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("服务返回了无法识别的响应") from exc
    if not isinstance(value, dict):
        raise RuntimeError("服务返回的数据格式不正确")
    return value


class ServerChanProvider:
    name = "serverchan"
    display_name = "微信 Server酱"

    def __init__(self, send_key: str) -> None:
        self.send_key = send_key.strip()
        if not self.send_key:
            raise ValueError("请填写 Server酱 SendKey")
        self.endpoint = serverchan_endpoint(self.send_key)

    def send(self, title: str, body: str) -> NotificationResult:
        try:
            result = _post_form(
                self.endpoint,
                {"title": _clean_title(title), "desp": body},
            )
            code = result.get("code")
            if code not in (0, "0"):
                message = str(result.get("message") or result.get("msg") or f"返回码 {code}")
                return NotificationResult(self.name, False, message[:300])
            return NotificationResult(self.name, True, "微信通知已发送")
        except urllib.error.HTTPError as exc:
            return NotificationResult(self.name, False, f"HTTP {exc.code}")
        except (OSError, RuntimeError, ValueError) as exc:
            return NotificationResult(self.name, False, str(exc)[:300])


class TelegramProvider:
    name = "telegram"
    display_name = "Telegram Bot"

    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        if not self.token:
            raise ValueError("请填写 Telegram Bot Token")
        if not self.chat_id:
            raise ValueError("请填写 Telegram Chat ID")

    def send(self, title: str, body: str) -> NotificationResult:
        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": f"{_clean_title(title)}\n\n{body}"[:4096],
                "disable_web_page_preview": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(65536).decode("utf-8", errors="replace")
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("ok") is not True:
                description = value.get("description") if isinstance(value, dict) else "响应格式错误"
                return NotificationResult(self.name, False, str(description)[:300])
            return NotificationResult(self.name, True, "Telegram 通知已发送")
        except urllib.error.HTTPError as exc:
            description = f"HTTP {exc.code}"
            try:
                value = json.loads(exc.read(65536).decode("utf-8", errors="replace"))
                if isinstance(value, dict) and value.get("description"):
                    description = str(value["description"])
            except (OSError, ValueError):
                pass
            return NotificationResult(self.name, False, description[:300])
        except (OSError, ValueError) as exc:
            return NotificationResult(self.name, False, str(exc)[:300])


class SmtpProvider:
    name = "smtp"
    display_name = "邮件"

    def __init__(self, settings: Mapping[str, Any], password: str) -> None:
        self.host = str(settings.get("host") or "").strip()
        self.port = int(settings.get("port") or 0)
        self.security = str(settings.get("security") or "SSL").upper().strip()
        self.username = str(settings.get("username") or "").strip()
        self.sender = str(settings.get("sender") or self.username).strip()
        recipients = str(settings.get("recipients") or "")
        self.recipients = [
            item.strip()
            for item in re.split(r"[,;，；\s]+", recipients)
            if item.strip()
        ]
        self.password = password
        if not self.host or not self.port:
            raise ValueError("请填写 SMTP 服务器和端口")
        if self.security not in {"SSL", "STARTTLS", "NONE"}:
            raise ValueError("SMTP 加密方式应为 SSL、STARTTLS 或 NONE")
        if not self.sender:
            raise ValueError("请填写发件邮箱")
        if not self.recipients:
            raise ValueError("请填写至少一个收件邮箱")
        if self.username and not self.password:
            raise ValueError("请填写邮箱授权码或 SMTP 密码")

    def send(self, title: str, body: str) -> NotificationResult:
        message = EmailMessage()
        message["Subject"] = _clean_title(title)
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(body)
        context = ssl.create_default_context()
        try:
            if self.security == "SSL":
                with smtplib.SMTP_SSL(
                    self.host, self.port, timeout=20, context=context
                ) as client:
                    if self.username:
                        client.login(self.username, self.password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20) as client:
                    client.ehlo()
                    if self.security == "STARTTLS":
                        client.starttls(context=context)
                        client.ehlo()
                    if self.username:
                        client.login(self.username, self.password)
                    client.send_message(message)
            return NotificationResult(self.name, True, "邮件已发送")
        except (OSError, smtplib.SMTPException) as exc:
            return NotificationResult(self.name, False, str(exc)[:300])


def build_provider(
    name: str, config: Mapping[str, Any], secrets: SecretStore
) -> NotificationProvider:
    providers = config.get("providers", {})
    if not isinstance(providers, Mapping):
        raise ValueError("通知配置格式不正确")
    settings = providers.get(name, {})
    if not isinstance(settings, Mapping):
        settings = {}
    if name == "serverchan":
        return ServerChanProvider(secrets.get("serverchan.send_key"))
    if name == "telegram":
        return TelegramProvider(
            secrets.get("telegram.bot_token"), str(settings.get("chat_id") or "")
        )
    if name == "smtp":
        return SmtpProvider(settings, secrets.get("smtp.password"))
    raise ValueError(f"未知通知渠道：{name}")
