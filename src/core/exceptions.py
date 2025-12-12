"""Exception classes for aria2bot."""


class Aria2Error(Exception):
    """Base exception"""


class UnsupportedOSError(Aria2Error):
    """不支持的操作系统"""


class UnsupportedArchError(Aria2Error):
    """不支持的 CPU 架构"""


class DownloadError(Aria2Error):
    """下载失败"""


class ConfigError(Aria2Error):
    """配置错误"""


class ServiceError(Aria2Error):
    """服务操作失败"""


class NotInstalledError(Aria2Error):
    """aria2 未安装"""


class RpcError(Aria2Error):
    """RPC 调用失败"""
