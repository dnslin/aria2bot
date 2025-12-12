"""System detection utilities for aria2bot."""
from __future__ import annotations

import platform
import secrets
import shutil
import string
import subprocess
from pathlib import Path

from src.core.constants import ARIA2_BIN
from src.core.exceptions import UnsupportedOSError, UnsupportedArchError


def detect_os() -> str:
    """检测操作系统，返回 'centos', 'debian', 'ubuntu' 或抛出 UnsupportedOSError"""
    os_release_path = Path("/etc/os-release")
    if os_release_path.exists():
        info: dict[str, str] = {}
        for line in os_release_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            info[key.strip()] = value.strip().strip('"').lower()
        os_id = info.get("ID")
        if os_id in {"ubuntu", "debian"}:
            return os_id
        if os_id in {"centos", "rhel", "rocky", "almalinux"}:
            return "centos"

    redhat_release = Path("/etc/redhat-release")
    if redhat_release.exists():
        content = redhat_release.read_text(encoding="utf-8", errors="ignore").lower()
        if any(name in content for name in ("centos", "red hat", "rocky", "alma")):
            return "centos"

    raise UnsupportedOSError("Unsupported operating system")


def detect_arch() -> str:
    """检测 CPU 架构，返回 'amd64', 'arm64', 'armhf', 'i386' 或抛出 UnsupportedArchError"""
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"aarch64", "arm64", "armv8"}:
        return "arm64"
    if machine.startswith("armv7") or machine.startswith("armv6"):
        return "armhf"
    if machine in {"i386", "i686", "x86"}:
        return "i386"
    raise UnsupportedArchError(f"Unsupported CPU architecture: {machine}")


def generate_rpc_secret() -> str:
    """生成 20 位随机 RPC 密钥"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20))


def is_aria2_installed() -> bool:
    """检查 aria2c 是否已安装"""
    if ARIA2_BIN.exists():
        return True
    return shutil.which("aria2c") is not None


def get_aria2_version() -> str | None:
    """获取已安装的 aria2 版本"""
    candidates = [ARIA2_BIN] if ARIA2_BIN.exists() else []
    path_cmd = shutil.which("aria2c")
    if path_cmd:
        candidates.append(Path(path_cmd))

    if not candidates:
        return None

    for cmd in candidates:
        try:
            result = subprocess.run(
                [str(cmd), "-v"], capture_output=True, text=True, check=False, timeout=5
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            lowered = line.lower()
            if "aria2 version" in lowered:
                parts = line.split()
                return parts[-1] if parts else line.strip()
        if result.stdout.strip():
            return result.stdout.splitlines()[0].strip()

    return None
