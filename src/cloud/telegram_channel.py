"""Telegram 频道存储客户端"""
from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import Bot

from src.core.config import TelegramChannelConfig
from src.utils.logger import get_logger

logger = get_logger("telegram_channel")

# 文件大小限制
STANDARD_LIMIT = 50 * 1024 * 1024  # 50MB
LOCAL_API_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒


class TelegramChannelClient:
    """Telegram 频道上传客户端"""

    def __init__(self, config: TelegramChannelConfig, bot: Bot, is_local_api: bool = False):
        self.config = config
        self.bot = bot
        self.max_size = LOCAL_API_LIMIT if is_local_api else STANDARD_LIMIT

    def get_max_size(self) -> int:
        """获取最大文件大小限制"""
        return self.max_size

    def get_max_size_mb(self) -> int:
        """获取最大文件大小限制（MB）"""
        return self.max_size // (1024 * 1024)

    async def upload_file(self, local_path: Path) -> tuple[bool, str]:
        """上传文件到频道

        Args:
            local_path: 本地文件路径

        Returns:
            tuple[bool, str]: (成功与否, file_id 或错误信息)
        """
        if not local_path.exists():
            return False, "文件不存在"

        file_size = local_path.stat().st_size
        if file_size > self.max_size:
            limit_mb = self.get_max_size_mb()
            return False, f"文件超过 {limit_mb}MB 限制"

        if not self.config.channel_id:
            return False, "频道 ID 未配置"

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                with open(local_path, "rb") as f:
                    message = await self.bot.send_document(
                        chat_id=self.config.channel_id,
                        document=f,
                        filename=local_path.name,
                        caption=f"📁 {local_path.name}",
                        read_timeout=300,
                        write_timeout=300,
                        connect_timeout=30,
                    )
                file_id = message.document.file_id
                logger.info(f"文件上传成功: {local_path.name}, file_id={file_id}")
                return True, file_id
            except Exception as e:
                last_error = e
                logger.warning(f"上传失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)

        logger.error(f"上传到频道失败: {last_error}")
        return False, str(last_error)
