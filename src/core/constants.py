"""Path constants for aria2bot."""
from pathlib import Path

HOME = Path.home()
ARIA2_BIN = HOME / ".local" / "bin" / "aria2c"
ARIA2_CONFIG_DIR = HOME / ".config" / "aria2"
ARIA2_CONF = ARIA2_CONFIG_DIR / "aria2.conf"
ARIA2_SESSION = ARIA2_CONFIG_DIR / "aria2.session"
ARIA2_LOG = ARIA2_CONFIG_DIR / "aria2.log"
DOWNLOAD_DIR = HOME / "downloads"
SYSTEMD_USER_DIR = HOME / ".config" / "systemd" / "user"
ARIA2_SERVICE = SYSTEMD_USER_DIR / "aria2.service"
