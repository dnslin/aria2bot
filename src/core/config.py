"""Configuration dataclass for aria2bot."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from src.core.constants import DOWNLOAD_DIR

# 云存储配置持久化文件路径
CLOUD_CONFIG_FILE = Path.home() / ".config" / "aria2bot" / "cloud_config.json"


@dataclass
class Aria2Config:
    rpc_port: int = 6800
    rpc_secret: str = ""
    download_dir: Path = DOWNLOAD_DIR
    max_concurrent_downloads: int = 5
    max_connection_per_server: int = 16
    bt_tracker_update: bool = True


@dataclass
class OneDriveConfig:
    """OneDrive 配置（使用公共客户端认证，不需要 client_secret）"""
    enabled: bool = False
    client_id: str = ""
    tenant_id: str = "common"
    auto_upload: bool = False
    delete_after_upload: bool = False
    remote_path: str = "/aria2bot"


@dataclass
class TelegramChannelConfig:
    """Telegram 频道存储配置"""
    enabled: bool = False
    channel_id: str = ""  # 频道 ID 或 @username
    auto_upload: bool = False
    delete_after_upload: bool = False


@dataclass
class BotConfig:
    token: str = ""
    api_base_url: str = ""
    allowed_users: set[int] = field(default_factory=set)
    aria2: Aria2Config = field(default_factory=Aria2Config)
    onedrive: OneDriveConfig = field(default_factory=OneDriveConfig)
    telegram_channel: TelegramChannelConfig = field(default_factory=TelegramChannelConfig)

    @classmethod
    def from_env(cls) -> "BotConfig":
        """从环境变量加载配置"""
        from dotenv import load_dotenv
        from src.core.exceptions import ConfigError
        load_dotenv()

        # 验证必需的 Token
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError("TELEGRAM_BOT_TOKEN 环境变量未设置")

        # 安全解析 RPC 端口
        port_str = os.environ.get("ARIA2_RPC_PORT", "6800")
        try:
            rpc_port = int(port_str)
            if not (1 <= rpc_port <= 65535):
                raise ValueError("端口必须在 1-65535 范围内")
        except ValueError as e:
            raise ConfigError(f"无效的 ARIA2_RPC_PORT: {e}") from e

        # 解析允许的用户 ID 列表
        allowed_users_str = os.environ.get("ALLOWED_USERS", "")
        allowed_users = set()
        if allowed_users_str:
            for uid in allowed_users_str.split(","):
                uid = uid.strip()
                if uid.isdigit():
                    user_id = int(uid)
                    # 验证用户 ID 在合理范围内
                    if 0 < user_id < 2**63:
                        allowed_users.add(user_id)

        aria2 = Aria2Config(
            rpc_port=rpc_port,
            rpc_secret=os.environ.get("ARIA2_RPC_SECRET", ""),
        )

        # 解析 OneDrive 配置（使用公共客户端认证，不需要 client_secret）
        onedrive = OneDriveConfig(
            enabled=os.environ.get("ONEDRIVE_ENABLED", "").lower() == "true",
            client_id=os.environ.get("ONEDRIVE_CLIENT_ID", ""),
            tenant_id=os.environ.get("ONEDRIVE_TENANT_ID", "common"),
            auto_upload=os.environ.get("ONEDRIVE_AUTO_UPLOAD", "").lower() == "true",
            delete_after_upload=os.environ.get("ONEDRIVE_DELETE_AFTER_UPLOAD", "").lower() == "true",
            remote_path=os.environ.get("ONEDRIVE_REMOTE_PATH", "/aria2bot"),
        )

        # 解析 Telegram 频道存储配置
        telegram_channel = TelegramChannelConfig(
            enabled=os.environ.get("TELEGRAM_CHANNEL_ENABLED", "").lower() == "true",
            channel_id=os.environ.get("TELEGRAM_CHANNEL_ID", ""),
            auto_upload=os.environ.get("TELEGRAM_CHANNEL_AUTO_UPLOAD", "").lower() == "true",
            delete_after_upload=os.environ.get("TELEGRAM_CHANNEL_DELETE_AFTER_UPLOAD", "").lower() == "true",
        )

        return cls(
            token=token,
            api_base_url=os.environ.get("TELEGRAM_API_BASE_URL", ""),
            allowed_users=allowed_users,
            aria2=aria2,
            onedrive=onedrive,
            telegram_channel=telegram_channel,
        )


def save_cloud_config(onedrive: OneDriveConfig, telegram: TelegramChannelConfig) -> bool:
    """保存云存储配置到文件

    Args:
        onedrive: OneDrive 配置
        telegram: Telegram 频道配置

    Returns:
        是否保存成功
    """
    try:
        CLOUD_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "onedrive": {
                "auto_upload": onedrive.auto_upload,
                "delete_after_upload": onedrive.delete_after_upload,
                "remote_path": onedrive.remote_path,
            },
            "telegram_channel": {
                "channel_id": telegram.channel_id,
                "auto_upload": telegram.auto_upload,
                "delete_after_upload": telegram.delete_after_upload,
            }
        }
        CLOUD_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return True
    except Exception:
        return False


def load_cloud_config() -> dict | None:
    """从文件加载云存储配置

    Returns:
        配置字典，如果文件不存在或解析失败则返回 None
    """
    try:
        if CLOUD_CONFIG_FILE.exists():
            return json.loads(CLOUD_CONFIG_FILE.read_text())
    except Exception:
        pass
    return None


def apply_saved_config(onedrive: OneDriveConfig, telegram: TelegramChannelConfig) -> None:
    """将保存的配置应用到配置对象（文件配置优先级低于环境变量）

    只有当环境变量未设置时，才使用文件中的配置
    """
    saved = load_cloud_config()
    if not saved:
        return

    # 应用 OneDrive 配置（仅当环境变量未明确设置时）
    if "onedrive" in saved:
        od = saved["onedrive"]
        # auto_upload: 如果环境变量未设置，使用文件配置
        if not os.environ.get("ONEDRIVE_AUTO_UPLOAD"):
            onedrive.auto_upload = od.get("auto_upload", False)
        if not os.environ.get("ONEDRIVE_DELETE_AFTER_UPLOAD"):
            onedrive.delete_after_upload = od.get("delete_after_upload", False)
        if not os.environ.get("ONEDRIVE_REMOTE_PATH"):
            onedrive.remote_path = od.get("remote_path", "/aria2bot")

    # 应用 Telegram 频道配置
    if "telegram_channel" in saved:
        tg = saved["telegram_channel"]
        if not os.environ.get("TELEGRAM_CHANNEL_ID"):
            telegram.channel_id = tg.get("channel_id", "")
        if not os.environ.get("TELEGRAM_CHANNEL_AUTO_UPLOAD"):
            telegram.auto_upload = tg.get("auto_upload", False)
        if not os.environ.get("TELEGRAM_CHANNEL_DELETE_AFTER_UPLOAD"):
            telegram.delete_after_upload = tg.get("delete_after_upload", False)
