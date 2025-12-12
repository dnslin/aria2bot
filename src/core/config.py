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
        load_dotenv()

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        # 解析允许的用户 ID 列表
        allowed_users_str = os.environ.get("ALLOWED_USERS", "")
        allowed_users = set()
        if allowed_users_str:
            allowed_users = {
                int(uid.strip()) for uid in allowed_users_str.split(",")
                if uid.strip().isdigit()
            }

        aria2 = Aria2Config(
            rpc_port=int(os.environ.get("ARIA2_RPC_PORT", "6800")),
            rpc_secret=os.environ.get("ARIA2_RPC_SECRET", ""),
        )
        return cls(
            token=token,
            api_base_url=os.environ.get("TELEGRAM_API_BASE_URL", ""),
            allowed_users=allowed_users,
            aria2=aria2,
        )
