"""Path constants for aria2bot."""
from pathlib import Path

HOME = Path.home()
ARIA2_BIN = HOME / ".local" / "bin" / "aria2c"
ARIA2_CONFIG_DIR = HOME / ".config" / "aria2"
ARIA2_CONF = ARIA2_CONFIG_DIR / "aria2.conf"
ARIA2_SESSION = ARIA2_CONFIG_DIR / "aria2.session"
ARIA2_LOG = ARIA2_CONFIG_DIR / "aria2.log"
ARIA2_DHT = ARIA2_CONFIG_DIR / "dht.dat"
ARIA2_DHT6 = ARIA2_CONFIG_DIR / "dht6.dat"
DOWNLOAD_DIR = HOME / "downloads"
SYSTEMD_USER_DIR = HOME / ".config" / "systemd" / "user"
ARIA2_SERVICE = SYSTEMD_USER_DIR / "aria2.service"

# 云存储相关路径
CLOUD_TOKEN_DIR = ARIA2_CONFIG_DIR / "cloud_tokens"
