"""Configuration dataclass for aria2bot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.core.constants import DOWNLOAD_DIR


@dataclass
class Aria2Config:
    rpc_port: int = 6800
    rpc_secret: str = ""
    download_dir: Path = DOWNLOAD_DIR
    max_concurrent_downloads: int = 5
    max_connection_per_server: int = 16
    bt_tracker_update: bool = True


@dataclass
class BotConfig:
    token: str = ""
    api_base_url: str = ""
    allowed_users: set[int] = field(default_factory=set)
    aria2: Aria2Config = field(default_factory=Aria2Config)

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
        return cls(
            token=token,
            api_base_url=os.environ.get("TELEGRAM_API_BASE_URL", ""),
            allowed_users=allowed_users,
            aria2=aria2,
        )
