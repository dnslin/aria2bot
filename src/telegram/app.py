"""Telegram application builder and runner."""
from __future__ import annotations

import sys

from telegram import Bot, BotCommand
from telegram.ext import Application

from src.core import BotConfig, is_aria2_installed
from src.core.config import apply_saved_config
from src.aria2.service import Aria2ServiceManager, get_service_mode
from src.telegram.handlers import Aria2BotAPI, build_handlers
from src.utils import setup_logger

# 全局 bot 实例，用于自动上传等功能发送消息
_bot_instance: Bot | None = None

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
    BotCommand("menu", "显示快捷菜单"),
    BotCommand("help", "显示帮助"),
    BotCommand("add", "添加下载任务"),
    BotCommand("list", "查看下载列表"),
    BotCommand("stats", "全局下载统计"),
    BotCommand("cloud", "云存储管理"),
]


async def post_init(application: Application) -> None:
    """应用初始化后设置命令菜单"""
    global _bot_instance
    logger = setup_logger()
    logger.info("Setting bot commands...")
    await application.bot.set_my_commands(BOT_COMMANDS)
    _bot_instance = application.bot


def create_app(config: BotConfig) -> Application:
    """创建 Telegram Application"""
    # 应用保存的云存储配置
    apply_saved_config(config.onedrive, config.telegram_channel)

    builder = Application.builder().token(config.token).post_init(post_init)
    if config.api_base_url:
        builder = builder.base_url(config.api_base_url).base_file_url(config.api_base_url + "/file")
    app = builder.build()

    api = Aria2BotAPI(config.aria2, config.allowed_users, config.onedrive, config.telegram_channel, config.api_base_url)
    for handler in build_handlers(api):
        app.add_handler(handler)

    return app


def _auto_start_aria2() -> None:
    """子进程模式下自动启动 aria2（如果已安装）"""
    logger = setup_logger()
    mode = get_service_mode()

    if mode != "subprocess":
        logger.info(f"服务管理模式: {mode}，跳过自动启动")
        return

    if not is_aria2_installed():
        logger.info("aria2 未安装，跳过自动启动")
        return

    try:
        service = Aria2ServiceManager()
        service.start()
        logger.info("aria2 子进程已自动启动")
    except Exception as e:
        logger.warning(f"自动启动 aria2 失败: {e}")


def run() -> None:
    """加载配置并启动 bot"""
    import asyncio

    logger = setup_logger()
    config = BotConfig.from_env()

    if not config.token:
        logger.error("Please set TELEGRAM_BOT_TOKEN in .env or environment")
        sys.exit(1)

    # 子进程模式下自动启动 aria2
    _auto_start_aria2()

    app = create_app(config)
    logger.info("Bot starting...")

    async def main():
        async with app:
            await app.start()
            await post_init(app)
            await app.updater.start_polling()
            await asyncio.Event().wait()

    asyncio.run(main())
