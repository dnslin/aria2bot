"""aria2 JSON-RPC 2.0 客户端"""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.core.exceptions import RpcError
from src.utils.logger import get_logger

logger = get_logger("rpc")


def _format_size(size: int) -> str:
    """格式化字节大小"""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


@dataclass
class DownloadTask:
    """下载任务数据类"""
    gid: str
    status: str  # active, waiting, paused, error, complete, removed
    name: str
    total_length: int
    completed_length: int
    download_speed: int
    upload_speed: int = 0
    error_message: str = ""
    dir: str = ""

    @property
    def progress(self) -> float:
        """计算下载进度百分比"""
        if self.total_length == 0:
            return 0.0
        return (self.completed_length / self.total_length) * 100

    @property
    def progress_bar(self) -> str:
        """生成进度条"""
        pct = int(self.progress / 10)
        return "█" * pct + "░" * (10 - pct)

    @property
    def speed_str(self) -> str:
        """格式化下载速度"""
        return _format_size(self.download_speed) + "/s"

    @property
    def size_str(self) -> str:
        """格式化文件大小"""
        return f"{_format_size(self.completed_length)}/{_format_size(self.total_length)}"


class Aria2RpcClient:
    """aria2 RPC 客户端"""

    def __init__(self, host: str = "localhost", port: int = 6800, secret: str = ""):
        self.url = f"http://{host}:{port}/jsonrpc"
        self.secret = secret

    async def _call(self, method: str, params: list | None = None) -> Any:
        """发送 RPC 请求"""
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": [],
        }
        # 添加 token 认证
        if self.secret:
            payload["params"].append(f"token:{self.secret}")
        if params:
            payload["params"].extend(params)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            raise RpcError("aria2 服务可能未运行，请先使用 /start 命令启动服务") from None
        except httpx.TimeoutException:
            raise RpcError("RPC 请求超时，aria2 服务响应缓慢") from None
        except httpx.HTTPStatusError as e:
            raise RpcError(f"RPC 请求失败，HTTP 状态码: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise RpcError(f"RPC 请求失败: {e}") from e
        except json.JSONDecodeError as e:
            raise RpcError(f"RPC 响应解析失败: {e}") from e

        if "error" in data:
            raise RpcError(data["error"].get("message", "未知错误"))
        return data.get("result")

    # === 添加任务 ===

    async def add_uri(self, uri: str) -> str:
        """添加 URL 下载任务，返回 GID"""
        result = await self._call("aria2.addUri", [[uri]])
        logger.info(f"添加下载任务: {uri[:50]}..., GID={result}")
        return result

    async def add_torrent(self, torrent_data: bytes) -> str:
        """添加种子下载任务，返回 GID"""
        b64_data = base64.b64encode(torrent_data).decode("utf-8")
        result = await self._call("aria2.addTorrent", [b64_data])
        logger.info(f"添加种子任务, GID={result}")
        return result

    # === 任务控制 ===

    async def pause(self, gid: str) -> str:
        """暂停任务"""
        return await self._call("aria2.pause", [gid])

    async def unpause(self, gid: str) -> str:
        """恢复任务"""
        return await self._call("aria2.unpause", [gid])

    async def remove(self, gid: str) -> str:
        """删除任务（仅从队列移除）"""
        return await self._call("aria2.remove", [gid])

    async def force_remove(self, gid: str) -> str:
        """强制删除任务"""
        return await self._call("aria2.forceRemove", [gid])

    async def remove_download_result(self, gid: str) -> str:
        """删除已完成/错误任务的记录"""
        return await self._call("aria2.removeDownloadResult", [gid])

    # === 查询任务 ===

    async def get_status(self, gid: str) -> DownloadTask:
        """获取单个任务状态"""
        keys = ["gid", "status", "totalLength", "completedLength",
                "downloadSpeed", "uploadSpeed", "files", "errorMessage", "dir"]
        result = await self._call("aria2.tellStatus", [gid, keys])
        return self._parse_task(result)

    async def get_active(self) -> list[DownloadTask]:
        """获取活动任务列表"""
        keys = ["gid", "status", "totalLength", "completedLength",
                "downloadSpeed", "uploadSpeed", "files", "dir"]
        result = await self._call("aria2.tellActive", [keys])
        return [self._parse_task(t) for t in result]

    async def get_waiting(self, offset: int = 0, num: int = 100) -> list[DownloadTask]:
        """获取等待/暂停任务列表"""
        keys = ["gid", "status", "totalLength", "completedLength",
                "downloadSpeed", "uploadSpeed", "files", "dir"]
        result = await self._call("aria2.tellWaiting", [offset, num, keys])
        return [self._parse_task(t) for t in result]

    async def get_stopped(self, offset: int = 0, num: int = 100) -> list[DownloadTask]:
        """获取已停止任务列表（完成/错误）"""
        keys = ["gid", "status", "totalLength", "completedLength",
                "downloadSpeed", "uploadSpeed", "files", "errorMessage", "dir"]
        result = await self._call("aria2.tellStopped", [offset, num, keys])
        return [self._parse_task(t) for t in result]

    async def get_global_stat(self) -> dict:
        """获取全局统计"""
        return await self._call("aria2.getGlobalStat")

    # === 文件操作 ===

    async def get_files(self, gid: str) -> list[dict]:
        """获取任务文件列表"""
        return await self._call("aria2.getFiles", [gid])

    def delete_files(self, task: DownloadTask) -> bool:
        """删除任务对应的文件（同步方法）"""
        if not task.dir or not task.name:
            return False
        try:
            file_path = (Path(task.dir) / task.name).resolve()
            # 安全检查：验证路径在下载目录内，防止路径遍历攻击
            from src.core.constants import DOWNLOAD_DIR
            download_dir = DOWNLOAD_DIR.resolve()
            try:
                file_path.relative_to(download_dir)
            except ValueError:
                logger.error(f"路径遍历尝试被阻止: {file_path}")
                return False
            if file_path.exists():
                if file_path.is_dir():
                    import shutil
                    shutil.rmtree(file_path)
                else:
                    file_path.unlink()
                logger.info(f"已删除文件: {file_path}")
                return True
        except OSError as e:
            logger.error(f"删除文件失败: {e}")
        return False

    # === 内部方法 ===

    def _parse_task(self, data: dict) -> DownloadTask:
        """解析任务数据"""
        # 从 files 中提取文件名
        name = "未知文件"
        if data.get("files"):
            path = data["files"][0].get("path", "")
            if path:
                name = path.split("/")[-1]
            elif data["files"][0].get("uris"):
                uris = data["files"][0]["uris"]
                if uris:
                    uri = uris[0].get("uri", "")
                    name = uri.split("/")[-1].split("?")[0] or uri[:30]

        return DownloadTask(
            gid=data.get("gid", ""),
            status=data.get("status", "unknown"),
            name=name,  # 保留完整文件名，显示时再截断
            total_length=int(data.get("totalLength", 0)),
            completed_length=int(data.get("completedLength", 0)),
            download_speed=int(data.get("downloadSpeed", 0)),
            upload_speed=int(data.get("uploadSpeed", 0)),
            error_message=data.get("errorMessage", ""),
            dir=data.get("dir", ""),
        )
