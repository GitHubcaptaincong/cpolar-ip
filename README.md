# cpolar 地址监控

一个面向 Windows 10/11 的本地桌面工具，用来监控 cpolar 免费隧道的随机公网地址。当域名、IP 或 TCP 端口发生变化时，它会通过微信、邮件或 Telegram 通知你。

工具不会暴露 cpolar 管理端口，也不需要保存 cpolar 账号密码。它从本机 `cpolar.yml` 读取隧道定义，并从 cpolar 服务日志中识别当前公网地址。

## 功能

- 自动发现本机 cpolar 隧道及 HTTP、HTTPS、TCP 等公网地址。
- 在可视化界面中选择需要监听的隧道。
- 地址变化检测、持久化去重及失败重试冷却。
- 一次检查发现多个变化时合并通知，减少消息数量。
- 支持微信 Server酱、QQ/163/通用 SMTP 邮件和 Telegram Bot。
- 通知设置修改后自动保存，关闭窗口、隐藏到托盘或退出前也会补保存。
- 通知凭据由 Windows DPAPI 加密，仅当前 Windows 用户能够解密。
- Windows 系统托盘控制：开始监听、暂停监听、立即检查、显示窗口、退出。
- 全局单实例：重复启动不会创建多个进程，而会唤醒已经运行的窗口。
- 支持登录 Windows 后自动隐藏到托盘并开始监听。
- 仅使用 Python 标准库，无需安装第三方依赖。

## 系统要求

- Windows 10 或 Windows 11。
- Python 3.10 或更高版本，并包含 Tkinter。
- cpolar 3.x 已安装，并以 Windows 服务方式运行。

默认读取位置：

```text
%USERPROFILE%\.cpolar\cpolar.yml
%USERPROFILE%\.cpolar\logs\cpolar_service.log
```

如果你的文件位于其他位置，可以在界面中重新选择。

## 快速开始

下载源码并进入项目目录后，双击：

```text
启动界面.cmd
```

也可以在 PowerShell 中运行：

```powershell
python app.py
```

首次使用：

1. 点击“刷新隧道”。
2. 在表格第一列勾选需要监听的隧道。
3. 打开“通知设置”，配置至少一种通知渠道。
4. 点击对应渠道的“测试通知”，确认能够收到消息。
5. 保存设置并点击“开始监听”。
6. 关闭窗口后，程序会继续在 Windows 系统托盘运行。

通知设置停止输入约 1.2 秒后会自动保存。页面底部会显示“有未保存修改”“已自动保存”或保存错误；也可以随时点击“保存全部设置”。

## 系统托盘与单实例

托盘图标颜色表示当前状态：

| 颜色 | 状态 |
|---|---|
| 绿色 | 正在监听，最近一次检查正常 |
| 灰色 | 监听已暂停 |
| 红色 | 最近一次检查或通知发生异常 |

- 双击托盘图标：显示主界面。
- 右键托盘图标：立即检查、开始/暂停监听或退出程序。
- 点击窗口右上角关闭：只隐藏窗口，不停止监听。
- 需要彻底结束程序：使用托盘菜单中的“退出程序”。
- 再次运行 `app.py` 或双击启动脚本：不会打开第二个实例，而会显示现有窗口。

## 通知配置

### 微信 Server酱

1. 打开 [Server酱](https://sct.ftqq.com/)并使用微信登录。
2. 复制 SendKey，粘贴到“微信 Server酱”页面。
3. 点击“测试微信通知”。

本工具只调用服务商公开的推送接口，不模拟登录或控制个人微信客户端。免费额度和平台规则可能调整，请以 Server酱网站的最新说明为准。

### QQ 邮箱

先在 QQ 邮箱网页版开启 SMTP 服务并生成授权码，然后填写：

界面中的“QQ 邮箱推荐设置”会自动填入服务器、端口和 SSL，并明确提示下一步需要填写的邮箱地址与授权码。

| 字段 | 内容 |
|---|---|
| SMTP 服务器 | `smtp.qq.com` |
| 端口 | `465` |
| 加密方式 | `SSL` |
| 登录邮箱 | 完整 QQ 邮箱地址 |
| 发件邮箱 | 与登录邮箱相同 |
| 授权码 / SMTP 密码 | QQ 邮箱生成的授权码 |

不要填写 QQ 登录密码。

### 163 邮箱

先在 163 邮箱网页版的“设置 → POP3/SMTP/IMAP”中开启 SMTP，并生成客户端授权密码，然后填写：

也可以点击界面中的“163 邮箱推荐设置”自动填入 SMTP 参数。

| 字段 | 内容 |
|---|---|
| SMTP 服务器 | `smtp.163.com` |
| 端口 | `465` |
| 加密方式 | `SSL` |
| 登录邮箱 | 完整 163 邮箱地址 |
| 发件邮箱 | 与登录邮箱相同 |
| 授权码 / SMTP 密码 | 163 客户端授权密码 |

收件邮箱可以填写多个，用逗号分隔，例如：

```text
your-name@qq.com,your-name@163.com
```

### Telegram Bot

1. 通过 Telegram 官方的 `@BotFather` 创建 Bot，取得 Bot Token。
2. 主动给新 Bot 发送一条消息。
3. 通过 Bot API 的 `getUpdates` 获取 Chat ID。
4. 在工具中填写 Bot Token 和 Chat ID，并发送测试通知。

Telegram Bot API 在部分网络环境中可能需要可用的国际网络连接。

## 后台运行和开机自启

- `启动后台监控.cmd`：隐藏主窗口到托盘并开始监听。
- `停止后台监控.cmd`：退出已经运行的程序。
- “登录 Windows 后自动后台监控”：写入当前用户的 Windows 启动项，不需要管理员权限；取消勾选并保存即可移除。

命令行参数：

```powershell
# 显示主界面；已有实例时唤醒现有窗口
python app.py

# 隐藏到托盘并开始监听
python app.py --headless

# 立即检查一次；已有实例时把检查请求交给现有实例
python app.py --check-once

# 退出已运行的实例
python app.py --exit
```

## 数据与安全

运行数据保存在：

```text
%LOCALAPPDATA%\CpolarNotifier
```

其中包括：

- `config.json`：隧道选择、检查间隔和非敏感通知配置。
- `secrets.json`：经 Windows DPAPI 加密的 SendKey、SMTP 授权码和 Bot Token。
- `state.json`：上次发现及已通知的地址，用于去重。
- `app.log`：不包含通知凭据的运行日志。

请不要把 `%LOCALAPPDATA%\CpolarNotifier` 中的文件提交到 GitHub。提交 Issue 时也应先检查日志中是否包含不希望公开的公网隧道地址。

## 项目结构

```text
app.py                         程序入口
cpolar_notifier/
  app.py                       单实例和命令行入口
  gui.py                       Tkinter 主界面
  tray.py                      Windows 系统托盘
  single_instance.py           命名互斥锁和实例间命令
  discovery.py                 隧道发现
  log_parser.py                cpolar 日志解析
  monitor.py                   变化检测和通知去重
  notifications.py             通知渠道
  secrets.py                   Windows DPAPI 凭据存储
tests/                         自动化测试
```

## 开发与测试

项目没有第三方运行依赖。运行全部测试：

```powershell
python -m unittest discover -v
```

执行语法检查：

```powershell
python -m compileall -q app.py cpolar_notifier tests
```

系统托盘和 GUI 冒烟测试会短暂创建 Windows 窗口或托盘图标。

## 已知边界

- 当前实现针对 Windows 和 cpolar 3.x 服务日志格式；没有提供 macOS/Linux 托盘实现。
- 若 cpolar 将来修改结构化日志格式，隧道定义仍可显示，但公网地址解析可能需要同步更新。
- 工具只能在电脑开机、cpolar 服务运行且网络可用时检测和通知。
- SMTP、Server酱和 Telegram 的真实投递需要使用者自己的凭据测试；自动化测试不会向外部服务发送消息。
- 本工具与 cpolar 官方无隶属或背书关系。

## 贡献

欢迎提交 Issue 或 Pull Request。修复日志兼容性问题时，请使用脱敏后的日志片段，不要提交 cpolar Authtoken、通知密钥、邮箱授权码或真实私人隧道地址。
