"""OneDrive 云存储客户端"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

from O365 import Account
from O365.utils import BaseTokenBackend

from src.cloud.base import CloudStorageBase, UploadProgress, UploadStatus
from src.core.config import OneDriveConfig
from src.core.constants import CLOUD_TOKEN_DIR
from src.utils.logger import get_logger

logger = get_logger("onedrive")


class FileTokenBackend(BaseTokenBackend):
    """文件系统 Token 存储后端"""

    def __init__(self, token_path: Path):
        super().__init__()
        self.token_path = token_path

    def load_token(self):
        """从文件加载 Token"""
        if self.token_path.exists():
            try:
                token_data = self.deserialize(self.token_path.read_text())
                self._cache = token_data
                return True
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"加载 Token 失败: {e}")
        return False

    def save_token(self, force=False):
        """保存 Token 到文件"""
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(self.serialize())
            return True
        except OSError as e:
            logger.error(f"保存 Token 失败: {e}")
            return False

    def delete_token(self):
        """删除 Token 文件"""
        try:
            if self.token_path.exists():
                self.token_path.unlink()
            self._cache = {}
            return True
        except OSError as e:
            logger.error(f"删除 Token 失败: {e}")
            return False

    def check_token(self):
        """检查 Token 是否存在"""
        return self.token_path.exists() and self.has_data


class OneDriveClient(CloudStorageBase):
    """OneDrive 客户端实现"""

    # OneDrive 所需的权限范围
    SCOPES = ["Files.ReadWrite", "offline_access"]

    def __init__(self, config: OneDriveConfig):
        self.config = config
        self._account: Account | None = None
        self._token_backend = FileTokenBackend(CLOUD_TOKEN_DIR / "onedrive_token.json")

    def _get_account(self) -> Account:
        """获取或创建 Account 实例"""
        if self._account is None:
            # 公共客户端只需要 client_id，不需要 client_secret
            credentials = (self.config.client_id,)
            self._account = Account(
                credentials,
                auth_flow_type="public",
                tenant_id=self.config.tenant_id,
                token_backend=self._token_backend,
                scopes=self.SCOPES,
            )
        return self._account

    async def is_authenticated(self) -> bool:
        """检查是否已认证"""
        account = self._get_account()
        return account.is_authenticated

    async def get_auth_url(self) -> tuple[str, str]:
        """获取认证 URL"""
        account = self._get_account()
        # MSAL 保留的 scope 不能传入，会自动处理
        reserved = {"openid", "offline_access", "profile"}
        scopes = [s for s in self.SCOPES if s not in reserved]
        redirect_uri = "https://login.microsoftonline.com/common/oauth2/nativeclient"
        url, state = account.con.get_authorization_url(
            requested_scopes=scopes,
            redirect_uri=redirect_uri
        )
        return url, state

    async def authenticate_with_code(self, callback_url: str, flow: dict | None = None) -> bool:
        """使用回调 URL 完成认证"""
        account = self._get_account()
        try:
            redirect_uri = "https://login.microsoftonline.com/common/oauth2/nativeclient"
            result = account.con.request_token(
                callback_url,
                redirect_uri=redirect_uri,
                flow=flow
            )
            if result:
                logger.info("OneDrive 认证成功")
            return bool(result)
        except Exception as e:
            import traceback
            logger.error(f"OneDrive 认证失败: {e}\n{traceback.format_exc()}")
            return False

    async def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        progress_callback: Callable[[UploadProgress], None] | None = None
    ) -> bool:
        """上传文件到 OneDrive"""
        account = self._get_account()
        if not account.is_authenticated:
            raise RuntimeError("OneDrive 未认证")

        try:
            # 获取存储和驱动器
            storage = account.storage()
            drive = storage.get_default_drive()
            root = drive.get_root_folder()

            # 确保远程目录存在
            target_folder = await self._ensure_folder_path(root, remote_path)

            file_size = local_path.stat().st_size
            file_name = local_path.name

            # 发送上传开始通知
            if progress_callback:
                progress_callback(UploadProgress(
                    file_name=file_name,
                    total_size=file_size,
                    uploaded_size=0,
                    status=UploadStatus.UPLOADING
                ))

            # 执行上传（python-o365 会自动处理大文件分块）
            uploaded = await asyncio.to_thread(
                target_folder.upload_file,
                item=str(local_path)
            )

            if uploaded:
                if progress_callback:
                    progress_callback(UploadProgress(
                        file_name=file_name,
                        total_size=file_size,
                        uploaded_size=file_size,
                        status=UploadStatus.COMPLETED
                    ))
                logger.info(f"文件上传成功: {file_name}")
                return True

            return False

        except Exception as e:
            logger.error(f"上传失败: {e}")
            if progress_callback:
                progress_callback(UploadProgress(
                    file_name=local_path.name,
                    total_size=0,
                    uploaded_size=0,
                    status=UploadStatus.FAILED,
                    error_message=str(e)
                ))
            return False

    async def _ensure_folder_path(self, root_folder, path: str):
        """确保远程目录路径存在，不存在则创建

        Args:
            root_folder: OneDrive 根文件夹对象
            path: 目标路径，如 "/aria2bot/downloads"

        Returns:
            目标文件夹对象
        """
        parts = [p for p in path.strip("/").split("/") if p]
        current = root_folder

        for part in parts:
            # 查找子文件夹
            items = await asyncio.to_thread(lambda: list(current.get_items()))
            found = None
            for item in items:
                if item.is_folder and item.name == part:
                    found = item
                    break

            if found:
                current = found
            else:
                # 创建新文件夹
                current = await asyncio.to_thread(
                    current.create_child_folder, part
                )
                logger.info(f"创建远程目录: {part}")

        return current

    async def logout(self) -> bool:
        """清除认证"""
        try:
            self._token_backend.delete_token()
            self._account = None
            logger.info("OneDrive 已登出")
            return True
        except Exception as e:
            logger.error(f"登出失败: {e}")
            return False
