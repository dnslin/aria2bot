"""云存储抽象基类"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class UploadStatus(Enum):
    """上传状态"""
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UploadProgress:
    """上传进度"""
    file_name: str
    total_size: int
    uploaded_size: int
    status: UploadStatus
    error_message: str = ""

    @property
    def progress(self) -> float:
        """计算上传进度百分比"""
        if self.total_size == 0:
            return 0.0
        return (self.uploaded_size / self.total_size) * 100


class CloudStorageBase(ABC):
    """云存储抽象基类，用于扩展不同云存储服务"""

    @abstractmethod
    async def is_authenticated(self) -> bool:
        """检查是否已认证"""
        pass

    @abstractmethod
    async def get_auth_url(self) -> tuple[str, str]:
        """获取认证 URL

        Returns:
            tuple[str, str]: (认证URL, state)
        """
        pass

    @abstractmethod
    async def authenticate_with_code(self, callback_url: str) -> bool:
        """使用回调 URL 完成认证

        Args:
            callback_url: 授权后的回调 URL

        Returns:
            bool: 认证是否成功
        """
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        progress_callback: Callable[[UploadProgress], None] | None = None
    ) -> bool:
        """上传文件

        Args:
            local_path: 本地文件路径
            remote_path: 远程目录路径
            progress_callback: 进度回调函数

        Returns:
            bool: 上传是否成功
        """
        pass

    @abstractmethod
    async def logout(self) -> bool:
        """登出/清除认证

        Returns:
            bool: 登出是否成功
        """
        pass
