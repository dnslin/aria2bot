"""Telegram bot 基础类和工具方法。"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import ContextTypes

from src.utils.logger import get_logger
from src.core import (
    Aria2Config,
    ARIA2_CONF,
)
from src.core.config import OneDriveConfig, TelegramChannelConfig, save_cloud_config
from src.aria2 import Aria2Installer, Aria2ServiceManager
from src.aria2.rpc import Aria2RpcClient

# Reply Keyboard 按钮文本到命令的映射
BUTTON_COMMANDS = {
    "📥 下载列表": "list",
    "📊 统计": "stats",
    "▶️ 启动": "start",
    "⏹ 停止": "stop",
    "🔄 重启": "restart",
    "📋 状态": "status",
    "📜 日志": "logs",
    "❓ 帮助": "help",
}

logger = get_logger("handlers")


def _get_user_info(update: Update) -> str:
    """获取用户信息用于日志"""
    user = update.effective_user
    if user:
        return f"用户ID={user.id}, 用户名={user.username or 'N/A'}"
    return "未知用户"


def _validate_download_url(url: str) -> tuple[bool, str]:
    """验证下载 URL 的有效性，防止恶意输入"""
    # 检查 URL 长度
    if len(url) > 2048:
        return False, "URL 过长（最大 2048 字符）"

    # 磁力链接直接通过
    if url.startswith("magnet:"):
        return True, ""

    # 验证 HTTP/HTTPS URL
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"不支持的协议: {parsed.scheme or '无'}，仅支持 HTTP/HTTPS/磁力链接"
        if not parsed.netloc:
            return False, "无效的 URL 格式"
        return True, ""
    except Exception:
        return False, "URL 解析失败"


class Aria2BotAPIBase:
    """Aria2 Bot API 基础类，包含初始化和工具方法"""

    def __init__(
        self,
        config: Aria2Config | None = None,
        allowed_users: set[int] | None = None,
        onedrive_config: OneDriveConfig | None = None,
        telegram_channel_config: TelegramChannelConfig | None = None,
        api_base_url: str = "",
    ):
        self.config = config or Aria2Config()
        self.allowed_users = allowed_users or set()
        self.installer = Aria2Installer(self.config)
        self.service = Aria2ServiceManager()
        self._rpc: Aria2RpcClient | None = None
        self._auto_refresh_tasks: dict[str, asyncio.Task] = {}  # chat_id:msg_id -> task
        self._auto_uploaded_gids: set[str] = set()  # 已自动上传的任务GID，防止重复上传
        self._download_monitors: dict[str, asyncio.Task] = {}  # gid -> 监控任务
        self._notified_gids: set[str] = set()  # 已通知的 GID，防止重复通知
        # 云存储相关
        self._onedrive_config = onedrive_config
        self._onedrive = None
        self._pending_auth: dict[int, dict] = {}  # user_id -> flow
        # Telegram 频道存储
        self._telegram_channel_config = telegram_channel_config
        self._telegram_channel = None
        self._api_base_url = api_base_url
        self._channel_uploaded_gids: set[str] = set()  # 已上传到频道的 GID
        self._pending_channel_input: dict[int, bool] = {}  # 等待用户输入频道ID

    async def _check_permission(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """检查用户权限，返回 True 表示有权限"""
        # 未配置白名单时拒绝所有用户
        if not self.allowed_users:
            logger.warning(f"未配置 ALLOWED_USERS，拒绝访问 - {_get_user_info(update)}")
            await self._reply(update, context, "⚠️ Bot 未配置允许的用户，请联系管理员")
            return False
        user_id = update.effective_user.id if update.effective_user else None
        if user_id and user_id in self.allowed_users:
            return True
        logger.warning(f"未授权访问 - {_get_user_info(update)}")
        await self._reply(update, context, "🚫 您没有权限使用此 Bot")
        return False

    def _get_rpc_client(self) -> Aria2RpcClient:
        """获取或创建 RPC 客户端"""
        if self._rpc is None:
            secret = self._get_rpc_secret()
            port = self._get_rpc_port() or 6800
            self._rpc = Aria2RpcClient(port=port, secret=secret)
        return self._rpc

    def _get_onedrive_client(self):
        """获取或创建 OneDrive 客户端"""
        if self._onedrive is None and self._onedrive_config and self._onedrive_config.enabled:
            from src.cloud.onedrive import OneDriveClient

            self._onedrive = OneDriveClient(self._onedrive_config)
        return self._onedrive

    def _get_telegram_channel_client(self, bot):
        """获取或创建 Telegram 频道客户端"""
        if (
            self._telegram_channel is None
            and self._telegram_channel_config
            and self._telegram_channel_config.enabled
        ):
            from src.cloud.telegram_channel import TelegramChannelClient

            is_local_api = bool(self._api_base_url)
            self._telegram_channel = TelegramChannelClient(
                self._telegram_channel_config, bot, is_local_api
            )
        return self._telegram_channel

    def _recreate_telegram_channel_client(self, bot):
        """重新创建 Telegram 频道客户端（配置更新后调用）"""
        self._telegram_channel = None
        return self._get_telegram_channel_client(bot)

    async def _delete_local_file(self, local_path, gid: str) -> tuple[bool, str]:
        """删除本地文件，返回 (成功, 消息)"""
        import shutil
        from pathlib import Path

        if isinstance(local_path, str):
            local_path = Path(local_path)
        try:
            if local_path.is_dir():
                shutil.rmtree(local_path)
            else:
                local_path.unlink()
            logger.info(f"已删除本地文件 GID={gid}: {local_path}")
            return True, "🗑️ 本地文件已删除"
        except Exception as e:
            logger.error(f"删除本地文件失败 GID={gid}: {e}")
            return False, f"⚠️ 删除本地文件失败: {e}"

    def _save_cloud_config(self) -> bool:
        """保存云存储配置"""
        if self._onedrive_config and self._telegram_channel_config:
            return save_cloud_config(self._onedrive_config, self._telegram_channel_config)
        return False

    async def _reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs
    ):
        if update.effective_message:
            return await update.effective_message.reply_text(text, **kwargs)
        if update.effective_chat:
            return await context.bot.send_message(
                chat_id=update.effective_chat.id, text=text, **kwargs
            )
        return None

    async def _delayed_delete_messages(self, messages: list, delay: int = 5) -> None:
        """延迟删除多条消息"""
        try:
            await asyncio.sleep(delay)
            for msg in messages:
                try:
                    await msg.delete()
                except Exception as e:
                    logger.warning(f"删除消息失败: {e}")
            logger.debug("已删除敏感认证消息")
        except Exception as e:
            logger.warning(f"延迟删除任务失败: {e}")

    def _get_rpc_secret(self) -> str:
        if self.config.rpc_secret:
            return self.config.rpc_secret
        if ARIA2_CONF.exists():
            try:
                for line in ARIA2_CONF.read_text(encoding="utf-8", errors="ignore").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("rpc-secret="):
                        secret = stripped.split("=", 1)[1].strip()
                        if secret:
                            self.config.rpc_secret = secret
                            return secret
            except OSError:
                return ""
        return ""

    def _get_rpc_port(self) -> int | None:
        if ARIA2_CONF.exists():
            try:
                for line in ARIA2_CONF.read_text(encoding="utf-8", errors="ignore").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("rpc-listen-port="):
                        port_str = stripped.split("=", 1)[1].strip()
                        if port_str.isdigit():
                            return int(port_str)
            except OSError:
                return None
        return self.config.rpc_port
