"""Core module for aria2bot - constants, config, exceptions, and system utilities."""
from src.core.constants import (
    HOME,
    ARIA2_BIN,
    ARIA2_CONFIG_DIR,
    ARIA2_CONF,
    ARIA2_SESSION,
    ARIA2_LOG,
    DOWNLOAD_DIR,
    SYSTEMD_USER_DIR,
    ARIA2_SERVICE,
)
from src.core.exceptions import (
    Aria2Error,
    UnsupportedOSError,
    UnsupportedArchError,
    DownloadError,
    ConfigError,
    ServiceError,
    NotInstalledError,
)
from src.core.config import Aria2Config, BotConfig
from src.core.system import (
    detect_os,
    detect_arch,
    generate_rpc_secret,
    is_aria2_installed,
    get_aria2_version,
)

__all__ = [
    "HOME",
    "ARIA2_BIN",
    "ARIA2_CONFIG_DIR",
    "ARIA2_CONF",
    "ARIA2_SESSION",
    "ARIA2_LOG",
    "DOWNLOAD_DIR",
    "SYSTEMD_USER_DIR",
    "ARIA2_SERVICE",
    "Aria2Error",
    "UnsupportedOSError",
    "UnsupportedArchError",
    "DownloadError",
    "ConfigError",
    "ServiceError",
    "NotInstalledError",
    "Aria2Config",
    "BotConfig",
    "detect_os",
    "detect_arch",
    "generate_rpc_secret",
    "is_aria2_installed",
    "get_aria2_version",
]
