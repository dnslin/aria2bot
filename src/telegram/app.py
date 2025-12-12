"""Telegram application builder and runner."""
from __future__ import annotations

import sys

from telegram import BotCommand
from telegram.ext import Application

from src.core import BotConfig
from src.telegram.handlers import Aria2BotAPI, build_handlers
from src.utils import setup_logger


# Bot 命令列表，用于 Telegram 命令自动补全
BOT_COMMANDS = [
    BotCommand("install", "安装 aria2"),
    BotCommand("uninstall", "卸载 aria2"),
    BotCommand("start", "启动 aria2 服务"),
    BotCommand("stop", "停止 aria2 服务"),
    BotCommand("restart", "重启 aria2 服务"),
    BotCommand("status", "查看 aria2 状态"),
    BotCommand("logs", "查看最近日志"),
    BotCommand("clear_logs", "清空日志"),
    BotCommand("set_secret", "设置自定义 RPC 密钥"),
    BotCommand("reset_secret", "重新生成随机 RPC 密钥"),
    BotCommand("help", "显示帮助"),
]


async def post_init(application: Application) -> None:
    """应用初始化后设置命令菜单"""
    await application.bot.set_my_commands(BOT_COMMANDS)


def create_app(config: BotConfig) -> Application:
    """创建 Telegram Application"""
    builder = Application.builder().token(config.token).post_init(post_init)
    if config.api_base_url:
        builder = builder.base_url(config.api_base_url).base_file_url(config.api_base_url + "/file")
    app = builder.build()

    api = Aria2BotAPI(config.aria2)
    for handler in build_handlers(api):
        app.add_handler(handler)

    return app


def run() -> None:
    """加载配置并启动 bot"""
    logger = setup_logger()
    config = BotConfig.from_env()

    if not config.token:
        logger.error("Please set TELEGRAM_BOT_TOKEN in .env or environment")
        sys.exit(1)

    app = create_app(config)
    logger.info("Bot starting...")
    app.run_polling()
