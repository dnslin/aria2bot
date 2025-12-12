"""Aria2 service manager - systemd service lifecycle management."""
from __future__ import annotations

import os
import subprocess

from src.utils.logger import get_logger

from src.core import (
    ARIA2_BIN,
    ARIA2_CONF,
    ARIA2_LOG,
    ARIA2_SERVICE,
    SYSTEMD_USER_DIR,
    ServiceError,
    NotInstalledError,
    ConfigError,
    is_aria2_installed,
)


SYSTEMD_SERVICE_TEMPLATE = """[Unit]
Description=Aria2 Download Manager
After=network.target

[Service]
Type=simple
ExecStart={aria2_bin} --conf-path={aria2_conf}
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

logger = get_logger("service")


class Aria2ServiceManager:
    def __init__(self) -> None:
        pass

    def _run_systemctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["systemctl", "--user", *args],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise ServiceError("systemctl command not found") from exc
        except subprocess.CalledProcessError as exc:
            output = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise ServiceError(output) from exc

    def _ensure_service_file(self) -> None:
        try:
            SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
            content = SYSTEMD_SERVICE_TEMPLATE.format(
                aria2_bin=str(ARIA2_BIN),
                aria2_conf=str(ARIA2_CONF),
            )
            ARIA2_SERVICE.write_text(content, encoding="utf-8")
            self._run_systemctl("daemon-reload")
        except OSError as exc:
            raise ServiceError(f"Failed to write service file: {exc}") from exc

    def start(self) -> None:
        logger.info("正在启动 aria2 服务...")
        if not is_aria2_installed():
            raise NotInstalledError("aria2 is not installed")
        self._ensure_service_file()
        self._run_systemctl("start", "aria2")
        logger.info("aria2 服务已启动")

    def stop(self) -> None:
        logger.info("正在停止 aria2 服务...")
        self._run_systemctl("stop", "aria2")
        logger.info("aria2 服务已停止")

    def restart(self) -> None:
        logger.info("正在重启 aria2 服务...")
        self._run_systemctl("restart", "aria2")
        logger.info("aria2 服务已重启")

    def enable(self) -> None:
        self._run_systemctl("enable", "aria2")

    def disable(self) -> None:
        self._run_systemctl("disable", "aria2")

    def status(self) -> dict:
        logger.info("正在获取 aria2 服务状态...")
        installed = is_aria2_installed()
        pid = self.get_pid() if installed else None

        try:
            active_proc = subprocess.run(
                ["systemctl", "--user", "is-active", "aria2"],
                capture_output=True,
                text=True,
                check=False,
            )
            enabled_proc = subprocess.run(
                ["systemctl", "--user", "is-enabled", "aria2"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ServiceError("systemctl command not found") from exc

        running = active_proc.returncode == 0
        enabled = enabled_proc.returncode == 0

        logger.info(f"aria2 状态: 已安装={installed}, 运行中={running}, PID={pid}")
        return {
            "installed": installed,
            "running": running,
            "pid": pid,
            "enabled": enabled,
        }

    def get_pid(self) -> int | None:
        try:
            result = subprocess.run(
                ["pgrep", "-u", str(os.getuid()), "-f", "aria2c"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            result = None

        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line)

        try:
            ps_result = subprocess.run(
                ["ps", "-C", "aria2c", "-o", "pid="],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None

        for line in ps_result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
        return None

    def view_log(self, lines: int = 50) -> str:
        if lines <= 0 or not ARIA2_LOG.exists():
            return ""
        try:
            content = ARIA2_LOG.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise ServiceError(f"Failed to read log: {exc}") from exc

        log_lines = content.splitlines(keepends=True)
        return "".join(log_lines[-lines:])

    def clear_log(self) -> None:
        try:
            ARIA2_LOG.parent.mkdir(parents=True, exist_ok=True)
            ARIA2_LOG.write_text("", encoding="utf-8")
        except OSError as exc:
            raise ServiceError(f"Failed to clear log: {exc}") from exc

    def remove_service(self) -> None:
        self.stop()
        try:
            ARIA2_SERVICE.unlink(missing_ok=True)
        except OSError as exc:
            raise ServiceError(f"Failed to remove service file: {exc}") from exc
        self._run_systemctl("daemon-reload")

    def update_rpc_secret(self, new_secret: str) -> None:
        """更新 aria2.conf 中的 rpc-secret 配置"""
        if not ARIA2_CONF.exists():
            raise ConfigError("aria2.conf 不存在，请先安装 aria2")
        try:
            content = ARIA2_CONF.read_text(encoding="utf-8")
            lines = content.splitlines()
            new_lines = []
            found = False
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith("rpc-secret="):
                    prefix = line[: len(line) - len(stripped)]
                    new_lines.append(f"{prefix}rpc-secret={new_secret}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"rpc-secret={new_secret}")
            ARIA2_CONF.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            logger.info("RPC 密钥已更新")
        except OSError as exc:
            raise ConfigError(f"更新配置文件失败: {exc}") from exc
