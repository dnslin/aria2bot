"""Aria2 installer - download, install, and configure aria2."""
from __future__ import annotations

import asyncio
import functools
import json
import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib import error, request

from src.utils.logger import get_logger

from src.core import (
    ARIA2_BIN,
    ARIA2_CONFIG_DIR,
    ARIA2_CONF,
    ARIA2_DHT,
    ARIA2_DHT6,
    ARIA2_LOG,
    ARIA2_SESSION,
    Aria2Config,
    Aria2Error,
    ConfigError,
    DownloadError,
    detect_arch,
    detect_os,
    generate_rpc_secret,
    is_aria2_installed,
)


logger = get_logger("installer")


class Aria2Installer:
    GITHUB_API = "https://api.github.com/repos/P3TERX/Aria2-Pro-Core/releases/latest"
    GITHUB_MIRROR = "https://gh-api.p3terx.com/repos/P3TERX/Aria2-Pro-Core/releases/latest"
    CONFIG_URLS = [
        "https://p3terx.github.io/aria2.conf",
        "https://cdn.jsdelivr.net/gh/P3TERX/aria2.conf@master",
    ]
    CONFIG_FILES = ["aria2.conf", "script.conf", "dht.dat", "dht6.dat"]

    def __init__(self, config: Aria2Config | None = None):
        self.config = config or Aria2Config()
        self.os_type = detect_os()
        self.arch = detect_arch()
        self._executor = ThreadPoolExecutor(max_workers=4)

    def __del__(self):
        """确保线程池被关闭，防止资源泄漏"""
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)

    def close(self):
        """显式关闭资源"""
        self._executor.shutdown(wait=True)

    async def get_latest_version(self) -> str:
        """从 GitHub API 获取最新版本号"""
        logger.info("正在获取 aria2 最新版本...")
        loop = asyncio.get_running_loop()
        last_error: Exception | None = None

        for url in (self.GITHUB_API, self.GITHUB_MIRROR):
            try:
                logger.info(f"尝试从 {url} 获取版本信息")
                data = await loop.run_in_executor(
                    self._executor, functools.partial(self._fetch_url, url)
                )
                payload = json.loads(data.decode("utf-8"))
                tag_name = payload.get("tag_name")
                if not tag_name:
                    raise DownloadError("tag_name missing in GitHub API response")
                logger.info(f"获取到最新版本: {tag_name}")
                return tag_name
            except Exception as exc:  # noqa: PERF203
                logger.error(f"从 {url} 获取版本失败: {exc}")
                last_error = exc
                continue

        raise DownloadError(f"Failed to fetch latest version: {last_error}") from last_error

    async def download_binary(self, version: str | None = None) -> Path:
        """下载并解压 aria2 静态二进制到 ~/.local/bin/"""
        resolved_version = version or await self.get_latest_version()
        version_name = resolved_version.lstrip("v").split("_")[0]
        archive_name = f"aria2-{version_name}-static-linux-{self.arch}.tar.gz"
        download_url = (
            f"https://github.com/P3TERX/Aria2-Pro-Core/releases/download/"
            f"{resolved_version}/{archive_name}"
        )

        logger.info(f"正在下载 aria2 二进制文件: {archive_name}")
        logger.info(f"下载地址: {download_url}")
        loop = asyncio.get_running_loop()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir_path = Path(tmpdir)
            archive_path = tmp_dir_path / archive_name
            extract_dir = tmp_dir_path / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)

            try:
                data = await loop.run_in_executor(
                    self._executor, functools.partial(self._fetch_url, download_url)
                )
                await loop.run_in_executor(
                    self._executor, functools.partial(self._write_file, archive_path, data)
                )
                logger.info("二进制文件下载完成")
            except Exception as exc:  # noqa: PERF203
                logger.error(f"下载二进制文件失败: {exc}")
                raise DownloadError(f"Failed to download aria2 binary: {exc}") from exc

            try:
                logger.info("正在解压二进制文件...")
                binary_path = await loop.run_in_executor(
                    self._executor, functools.partial(self._extract_binary, archive_path, extract_dir)
                )
            except Exception as exc:  # noqa: PERF203
                logger.error(f"解压二进制文件失败: {exc}")
                raise DownloadError(f"Failed to extract aria2 binary: {exc}") from exc

            try:
                ARIA2_BIN.parent.mkdir(parents=True, exist_ok=True)
                if ARIA2_BIN.exists():
                    ARIA2_BIN.unlink()
                shutil.move(str(binary_path), ARIA2_BIN)
                ARIA2_BIN.chmod(0o755)
                logger.info(f"aria2 二进制文件已安装到: {ARIA2_BIN}")
            except Exception as exc:  # noqa: PERF203
                logger.error(f"安装二进制文件失败: {exc}")
                raise DownloadError(f"Failed to install aria2 binary: {exc}") from exc

        return ARIA2_BIN

    async def download_config(self) -> None:
        """下载配置模板文件"""
        logger.info("正在下载配置文件...")
        ARIA2_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()

        for filename in self.CONFIG_FILES:
            last_error: Exception | None = None
            for base in self.CONFIG_URLS:
                url = f"{base.rstrip('/')}/{filename}"
                try:
                    data = await loop.run_in_executor(
                        self._executor, functools.partial(self._fetch_url, url)
                    )
                    target = ARIA2_CONFIG_DIR / filename
                    await loop.run_in_executor(
                        self._executor, functools.partial(self._write_file, target, data)
                    )
                    logger.info(f"配置文件已下载: {filename}")
                    last_error = None
                    break
                except Exception as exc:  # noqa: PERF203
                    last_error = exc
                    continue
            if last_error is not None:
                logger.error(f"下载配置文件失败: {filename} - {last_error}")
                raise DownloadError(f"Failed to download {filename}: {last_error}") from last_error

    def render_config(self) -> None:
        """渲染配置文件，注入用户参数"""
        logger.info("正在渲染配置文件...")
        if not ARIA2_CONF.exists():
            raise ConfigError("Config template not found. Run download_config first.")

        try:
            content = ARIA2_CONF.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Failed to read config: {exc}") from exc

        rpc_secret = self.config.rpc_secret or generate_rpc_secret()
        self.config.rpc_secret = rpc_secret

        replacements = {
            "dir=": str(self.config.download_dir),
            "rpc-listen-port=": str(self.config.rpc_port),
            "rpc-secret=": rpc_secret,
            "max-concurrent-downloads=": str(self.config.max_concurrent_downloads),
            "max-connection-per-server=": str(self.config.max_connection_per_server),
            "dht-file-path=": str(ARIA2_DHT),
            "dht-file-path6=": str(ARIA2_DHT6),
            "input-file=": str(ARIA2_SESSION),
            "save-session=": str(ARIA2_SESSION),
        }

        logger.info(f"配置参数: RPC端口={self.config.rpc_port}, 下载目录={self.config.download_dir}")

        new_lines: list[str] = []
        for line in content.splitlines():
            stripped = line.lstrip()
            # 跳过注释行，不进行替换
            if stripped.startswith("#"):
                new_lines.append(line)
                continue
            replaced = False
            for key, value in replacements.items():
                if stripped.startswith(key):
                    prefix = line[: len(line) - len(stripped)]
                    new_lines.append(f"{prefix}{key}{value}")
                    replaced = True
                    break
            if not replaced:
                new_lines.append(line)

        try:
            ARIA2_CONF.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            # 设置配置文件权限为仅所有者可读写（包含敏感的 RPC 密钥）
            ARIA2_CONF.chmod(0o600)
            ARIA2_SESSION.touch(exist_ok=True)
            ARIA2_SESSION.chmod(0o600)
            self.config.download_dir.mkdir(parents=True, exist_ok=True)
            ARIA2_LOG.touch(exist_ok=True)
            logger.info(f"配置文件已保存: {ARIA2_CONF}")
        except OSError as exc:
            logger.error(f"保存配置文件失败: {exc}")
            raise ConfigError(f"Failed to render config: {exc}") from exc

    async def install(self, version: str | None = None) -> dict:
        """完整安装流程"""
        logger.info("开始安装 aria2...")
        resolved_version = version or await self.get_latest_version()
        await self.download_binary(resolved_version)
        await self.download_config()
        self.render_config()

        logger.info(f"aria2 安装完成! 版本: {resolved_version}, 路径: {ARIA2_BIN}")
        return {
            "version": resolved_version,
            "binary": str(ARIA2_BIN),
            "config_dir": str(ARIA2_CONFIG_DIR),
            "config": str(ARIA2_CONF),
            "session": str(ARIA2_SESSION),
            "installed": is_aria2_installed(),
        }

    def uninstall(self) -> None:
        """卸载 aria2"""
        logger.info("开始卸载 aria2...")
        errors: list[Exception] = []

        try:
            if ARIA2_BIN.exists():
                ARIA2_BIN.unlink()
                logger.info(f"已删除二进制文件: {ARIA2_BIN}")
        except Exception as exc:  # noqa: PERF203
            logger.error(f"删除二进制文件失败: {exc}")
            errors.append(exc)

        try:
            if ARIA2_CONFIG_DIR.exists():
                # 删除配置目录中的文件，而不是整个目录（兼容 Docker 挂载卷）
                for item in ARIA2_CONFIG_DIR.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                logger.info(f"已清空配置目录: {ARIA2_CONFIG_DIR}")
        except Exception as exc:  # noqa: PERF203
            logger.error(f"清空配置目录失败: {exc}")
            errors.append(exc)

        try:
            service_path = Path.home() / ".config" / "systemd" / "user" / "aria2.service"
            if service_path.exists():
                service_path.unlink()
                logger.info(f"已删除服务文件: {service_path}")
        except Exception as exc:  # noqa: PERF203
            logger.error(f"删除服务文件失败: {exc}")
            errors.append(exc)

        if errors:
            messages = "; ".join(str(err) for err in errors)
            raise Aria2Error(f"Failed to uninstall aria2: {messages}")

        logger.info("aria2 卸载完成")

    def _fetch_url(self, url: str) -> bytes:
        """阻塞式 URL 获取，放在线程池中运行"""
        req = request.Request(url, headers={"User-Agent": "aria2-installer"})
        try:
            with request.urlopen(req, timeout=30) as resp:
                # 检查 HTTP 状态码（urllib 使用 code 属性）
                status_code = getattr(resp, "code", 200)
                if status_code >= 400:
                    raise DownloadError(f"HTTP {status_code} for {url}")
                return resp.read()
        except (error.HTTPError, error.URLError) as exc:
            raise DownloadError(f"Network error for {url}: {exc}") from exc

    @staticmethod
    def _write_file(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def _extract_binary(archive_path: Path, extract_dir: Path) -> Path:
        with tarfile.open(archive_path, "r:gz") as tar:
            # 安全检查：验证所有成员路径，防止 Zip Slip 攻击
            for member in tar.getmembers():
                # 检查符号链接
                if member.issym() or member.islnk():
                    raise DownloadError(f"不安全的 tar 成员（符号链接）: {member.name}")
                # 检查路径遍历
                if member.name.startswith('/') or '..' in member.name:
                    raise DownloadError(f"不安全的 tar 成员: {member.name}")
                # 验证解压后的路径
                member_path = (extract_dir / member.name).resolve()
                if not str(member_path).startswith(str(extract_dir.resolve())):
                    raise DownloadError(f"不安全的 tar 成员（路径遍历）: {member.name}")
            tar.extractall(extract_dir)
        for candidate in extract_dir.rglob("aria2c"):
            if candidate.is_file():
                return candidate
        raise DownloadError("aria2c binary not found in archive")
