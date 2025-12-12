# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

You must communicate in Chinese and logs and comments must also be in Chinese, in the use of third-party libraries if you are not clear on the use of api's please use context7 mcp to get the documentation.

## Commands

```bash
# Install dependencies
pip install -e .

# Run the bot
python main.py
# or after install:
aria2bot
```

## Configuration

Copy `.env.example` to `.env` and set:

- `TELEGRAM_BOT_TOKEN` (required) - Bot token from @BotFather
- `TELEGRAM_API_BASE_URL` (optional) - Custom API endpoint for self-hosted bot API
- `ARIA2_RPC_PORT` (default: 6800)
- `ARIA2_RPC_SECRET` (optional, auto-generated)

## Architecture

Three-layer design:

- `src/telegram/` - Bot interface (handlers.py defines commands, keyboards.py builds inline keyboards, app.py runs polling)
- `src/aria2/` - aria2 management (installer.py downloads/configures, service.py manages systemd, rpc.py communicates with aria2)
- `src/core/` - Shared utilities (constants, config dataclasses, exceptions, system detection)

Flow: Telegram command → `Aria2BotAPI` handler → `Aria2Installer` or `Aria2ServiceManager` or `Aria2RpcClient` → system/aria2

## Key Paths (defined in src/core/constants.py)

- Binary: `~/.local/bin/aria2c`
- Config: `~/.config/aria2/aria2.conf`
- Service: `~/.config/systemd/user/aria2.service`
- Downloads: `~/downloads`

## Bot Commands

服务管理: /install, /uninstall, /start, /stop, /restart, /status, /logs, /clear_logs, /set_secret, /reset_secret

下载管理: /add <URL>, /list, /stats

其他: /help

支持发送 .torrent 文件直接添加下载任务

