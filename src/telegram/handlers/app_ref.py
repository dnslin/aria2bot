"""Bot 实例引用，用于避免循环导入。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Bot

# 全局 bot 实例引用
_bot_instance: "Bot | None" = None


def set_bot_instance(bot: "Bot") -> None:
    """设置 bot 实例"""
    global _bot_instance
    _bot_instance = bot


def get_bot_instance() -> "Bot | None":
    """获取 bot 实例"""
    return _bot_instance
