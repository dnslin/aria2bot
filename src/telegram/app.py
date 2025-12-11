"""Telegram application builder and runner."""
from __future__ import annotations

import sys

from telegram.ext import Application

from src.core import BotConfig
from src.telegram.handlers import Aria2BotAPI, build_handlers
from src.utils import setup_logger


def create_app(config: BotConfig) -> Application:
    """创建 Telegram Application"""
    builder = Application.builder().token(config.token)
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
