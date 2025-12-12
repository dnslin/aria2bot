"""云存储模块"""
from src.cloud.base import CloudStorageBase, UploadProgress, UploadStatus

__all__ = ["CloudStorageBase", "UploadProgress", "UploadStatus"]

# OneDriveClient 延迟导入，避免在 O365 未安装时报错
try:
    from src.cloud.onedrive import OneDriveClient
    __all__.append("OneDriveClient")
except ImportError:
    OneDriveClient = None
