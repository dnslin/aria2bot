"""Telegram bot handlers 模块。"""
from __future__ import annotations

from functools import wraps

from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from .base import Aria2BotAPIBase, BUTTON_COMMANDS, _get_user_info, _validate_download_url
from .service import ServiceHandlersMixin
from .download import DownloadHandlersMixin
from .cloud_onedrive import OneDriveHandlersMixin
from .cloud_channel import TelegramChannelHandlersMixin
from .cloud_coordinator import CloudCoordinatorMixin
from .callbacks import CallbackHandlersMixin


class Aria2BotAPI(
    CallbackHandlersMixin,
    CloudCoordinatorMixin,
    TelegramChannelHandlersMixin,
    OneDriveHandlersMixin,
    DownloadHandlersMixin,
    ServiceHandlersMixin,
    Aria2BotAPIBase,
):
    """Aria2 Bot API - 组合所有功能"""

    pass


def build_handlers(api: Aria2BotAPI) -> list:
    """构建 Handler 列表"""

    def wrap_with_permission(handler_func):
        """包装处理函数，添加权限检查"""

        @wraps(handler_func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await api._check_permission(update, context):
                return
            return await handler_func(update, context)

        return wrapped

    # 构建按钮文本过滤器
    button_pattern = (
        "^("
        + "|".join(BUTTON_COMMANDS.keys()).replace("▶️", "▶️").replace("⏹", "⏹")
        + ")$"
    )

    return [
        # 服务管理命令
        CommandHandler("install", wrap_with_permission(api.install)),
        CommandHandler("uninstall", wrap_with_permission(api.uninstall)),
        CommandHandler("start", wrap_with_permission(api.start_service)),
        CommandHandler("stop", wrap_with_permission(api.stop_service)),
        CommandHandler("restart", wrap_with_permission(api.restart_service)),
        CommandHandler("status", wrap_with_permission(api.status)),
        CommandHandler("logs", wrap_with_permission(api.view_logs)),
        CommandHandler("clear_logs", wrap_with_permission(api.clear_logs)),
        CommandHandler("set_secret", wrap_with_permission(api.set_secret)),
        CommandHandler("reset_secret", wrap_with_permission(api.reset_secret)),
        CommandHandler("help", wrap_with_permission(api.help_command)),
        CommandHandler("menu", wrap_with_permission(api.menu_command)),
        # 下载管理命令
        CommandHandler("add", wrap_with_permission(api.add_download)),
        CommandHandler("list", wrap_with_permission(api.list_downloads)),
        CommandHandler("stats", wrap_with_permission(api.global_stats)),
        # 云存储命令
        CommandHandler("cloud", wrap_with_permission(api.cloud_command)),
        # Reply Keyboard 按钮文本处理（也处理频道ID输入）
        MessageHandler(
            filters.TEXT & filters.Regex(button_pattern),
            wrap_with_permission(api.handle_text_message),
        ),
        # 频道ID输入处理（捕获 @channel 或 -100xxx 格式）
        MessageHandler(
            filters.TEXT & filters.Regex(r"^(@[\w]+|-?\d+)$"),
            wrap_with_permission(api.handle_channel_id_input),
        ),
        # OneDrive 认证回调 URL 处理
        MessageHandler(
            filters.TEXT & filters.Regex(r"^https://login\.microsoftonline\.com"),
            wrap_with_permission(api.handle_auth_callback),
        ),
        # 种子文件处理
        MessageHandler(
            filters.Document.FileExtension("torrent"),
            wrap_with_permission(api.handle_torrent),
        ),
        # 直接发送链接/磁力链接处理（放在最后，避免拦截其他文本消息）
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(r'(https?://|magnet:\?)'),
            wrap_with_permission(api.handle_url_message),
        ),
        # Callback Query 处理
        CallbackQueryHandler(wrap_with_permission(api.handle_callback)),
    ]


__all__ = [
    "Aria2BotAPI",
    "build_handlers",
    "BUTTON_COMMANDS",
    "_get_user_info",
    "_validate_download_url",
]
