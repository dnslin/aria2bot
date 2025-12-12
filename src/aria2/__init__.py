"""Aria2 operations module - installer, service management, and RPC client."""
from src.aria2.installer import Aria2Installer
from src.aria2.service import Aria2ServiceManager
from src.aria2.rpc import Aria2RpcClient, DownloadTask

__all__ = ["Aria2Installer", "Aria2ServiceManager", "Aria2RpcClient", "DownloadTask"]
