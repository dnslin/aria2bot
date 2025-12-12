"""Aria2 service manager - 支持 systemd 和子进程两种模式."""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod

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
    detect_service_mode,
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


class ServiceManagerBase(ABC):
    """服务管理器抽象基类"""

    @abstractmethod
    def start(self) -> None:
        """启动 aria2 服务"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止 aria2 服务"""
        pass

    @abstractmethod
    def restart(self) -> None:
        """重启 aria2 服务"""
        pass

    @abstractmethod
    def status(self) -> dict:
        """获取服务状态，返回 {installed, running, pid, enabled}"""
        pass

    @abstractmethod
    def get_pid(self) -> int | None:
        """获取 aria2 进程 PID"""
        pass

    def enable(self) -> None:
        """启用开机自启（子进程模式下静默忽略）"""
        pass

    def disable(self) -> None:
        """禁用开机自启（子进程模式下静默忽略）"""
        pass

    def view_log(self, lines: int = 50) -> str:
        """查看日志"""
        if lines <= 0 or not ARIA2_LOG.exists():
            return ""
        try:
            content = ARIA2_LOG.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise ServiceError(f"读取日志失败: {exc}") from exc
        log_lines = content.splitlines(keepends=True)
        return "".join(log_lines[-lines:])

    def clear_log(self) -> None:
        """清空日志"""
        try:
            ARIA2_LOG.parent.mkdir(parents=True, exist_ok=True)
            ARIA2_LOG.write_text("", encoding="utf-8")
        except OSError as exc:
            raise ServiceError(f"清空日志失败: {exc}") from exc

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

    def remove_service(self) -> None:
        """移除服务（子类可覆盖）"""
        self.stop()


class SystemdServiceManager(ServiceManagerBase):
    """基于 systemctl --user 的服务管理器"""

    def _run_systemctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["systemctl", "--user", *args],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise ServiceError("systemctl command not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ServiceError(f"systemctl 命令超时: {args}") from exc
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
            raise ServiceError(f"写入服务文件失败: {exc}") from exc

    def start(self) -> None:
        logger.info("正在启动 aria2 服务 (systemd)...")
        if not is_aria2_installed():
            raise NotInstalledError("aria2 未安装")
        self._ensure_service_file()
        self._run_systemctl("start", "aria2")
        logger.info("aria2 服务已启动")

    def stop(self) -> None:
        logger.info("正在停止 aria2 服务 (systemd)...")
        self._run_systemctl("stop", "aria2")
        logger.info("aria2 服务已停止")

    def restart(self) -> None:
        logger.info("正在重启 aria2 服务 (systemd)...")
        self._run_systemctl("restart", "aria2")
        logger.info("aria2 服务已重启")

    def enable(self) -> None:
        self._run_systemctl("enable", "aria2")

    def disable(self) -> None:
        self._run_systemctl("disable", "aria2")

    def status(self) -> dict:
        logger.info("正在获取 aria2 服务状态 (systemd)...")
        installed = is_aria2_installed()
        pid = self.get_pid() if installed else None

        try:
            active_proc = subprocess.run(
                ["systemctl", "--user", "is-active", "aria2"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            enabled_proc = subprocess.run(
                ["systemctl", "--user", "is-enabled", "aria2"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except FileNotFoundError as exc:
            raise ServiceError("systemctl command not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ServiceError("获取服务状态超时") from exc

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
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
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
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        for line in ps_result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
        return None

    def remove_service(self) -> None:
        self.stop()
        try:
            ARIA2_SERVICE.unlink(missing_ok=True)
        except OSError as exc:
            raise ServiceError(f"删除服务文件失败: {exc}") from exc
        self._run_systemctl("daemon-reload")


class SubprocessServiceManager(ServiceManagerBase):
    """基于子进程的服务管理器（用于 Docker 等无 systemd 环境）"""

    _instance: "SubprocessServiceManager | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "SubprocessServiceManager":
        """单例模式，确保只有一个子进程管理器"""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._process: subprocess.Popen | None = None
                instance._registered_cleanup = False
                cls._instance = instance
            return cls._instance

    def _register_cleanup(self) -> None:
        """注册退出清理函数"""
        if not self._registered_cleanup:
            atexit.register(self._cleanup)
            # 保存原始信号处理器
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            self._original_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
            self._registered_cleanup = True

    def _signal_handler(self, signum: int, frame) -> None:
        """信号处理器"""
        self._cleanup()
        # 调用原始处理器或默认退出
        if signum == signal.SIGTERM and callable(self._original_sigterm):
            self._original_sigterm(signum, frame)
        elif signum == signal.SIGINT and callable(self._original_sigint):
            self._original_sigint(signum, frame)
        else:
            sys.exit(0)

    def _cleanup(self) -> None:
        """清理子进程"""
        if self._process and self._process.poll() is None:
            logger.info("正在停止 aria2 子进程...")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            logger.info("aria2 子进程已停止")

    def start(self) -> None:
        """启动 aria2 子进程"""
        logger.info("正在启动 aria2 子进程...")
        if not is_aria2_installed():
            raise NotInstalledError("aria2 未安装")

        if self._process and self._process.poll() is None:
            logger.info("aria2 已在运行")
            return

        if not ARIA2_CONF.exists():
            raise ConfigError("aria2.conf 不存在")

        self._register_cleanup()

        # 确保日志文件目录存在
        ARIA2_LOG.parent.mkdir(parents=True, exist_ok=True)

        # 启动子进程，日志输出到文件
        log_file = open(ARIA2_LOG, "a", encoding="utf-8")
        self._process = subprocess.Popen(
            [str(ARIA2_BIN), f"--conf-path={ARIA2_CONF}"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # 创建新会话，避免信号传播
        )

        # 等待短暂时间检查是否启动成功
        time.sleep(0.5)
        if self._process.poll() is not None:
            raise ServiceError(f"aria2 启动失败，退出码: {self._process.returncode}")

        logger.info(f"aria2 子进程已启动，PID={self._process.pid}")

    def stop(self) -> None:
        """停止 aria2 子进程"""
        logger.info("正在停止 aria2 子进程...")
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None
            logger.info("aria2 子进程已停止")
            return

        # 尝试通过 PID 查找并停止
        pid = self.get_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"已发送 SIGTERM 到 PID={pid}")
            except OSError:
                pass

    def restart(self) -> None:
        """重启 aria2 子进程"""
        logger.info("正在重启 aria2 子进程...")
        self.stop()
        time.sleep(1)
        self.start()
        logger.info("aria2 子进程已重启")

    def status(self) -> dict:
        """获取服务状态"""
        logger.info("正在获取 aria2 子进程状态...")
        installed = is_aria2_installed()
        pid = self.get_pid() if installed else None
        running = pid is not None

        logger.info(f"aria2 状态: 已安装={installed}, 运行中={running}, PID={pid}")
        return {
            "installed": installed,
            "running": running,
            "pid": pid,
            "enabled": False,  # 子进程模式不支持开机自启
        }

    def get_pid(self) -> int | None:
        """获取 aria2 进程 PID"""
        # 优先检查管理的子进程
        if self._process and self._process.poll() is None:
            return self._process.pid

        # 回退到 pgrep 查找
        try:
            result = subprocess.run(
                ["pgrep", "-u", str(os.getuid()), "-f", "aria2c"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        return int(line)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None


# 缓存服务管理器实例
_service_manager: ServiceManagerBase | None = None
_service_mode: str | None = None


def Aria2ServiceManager() -> ServiceManagerBase:
    """工厂函数：根据环境自动选择服务管理器

    保持与现有代码的兼容性，调用方式不变：
        service = Aria2ServiceManager()
        service.start()
    """
    global _service_manager, _service_mode

    if _service_manager is not None:
        return _service_manager

    mode = detect_service_mode()
    _service_mode = mode
    logger.info(f"检测到服务管理模式: {mode}")

    if mode == "systemd":
        _service_manager = SystemdServiceManager()
    else:
        _service_manager = SubprocessServiceManager()

    return _service_manager


def get_service_mode() -> str:
    """获取当前服务管理模式"""
    global _service_mode
    if _service_mode is None:
        _service_mode = detect_service_mode()
    return _service_mode
