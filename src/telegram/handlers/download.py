"""下载管理命令处理。"""
from __future__ import annotations

import asyncio
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.utils.logger import get_logger
from src.core import RpcError
from src.aria2.rpc import DownloadTask, _format_size
from src.telegram.keyboards import (
    build_list_type_keyboard,
    build_after_add_keyboard,
)

from .base import _get_user_info, _validate_download_url

# 匹配 HTTP/HTTPS 链接和磁力链接的正则表达式
URL_PATTERN = re.compile(r'(https?://[^\s<>"]+|magnet:\?[^\s<>"]+)')

logger = get_logger("handlers.download")


class DownloadHandlersMixin:
    """下载管理命令 Mixin"""

    async def add_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/add <url> - 添加下载任务"""
        logger.info(f"收到 /add 命令 - {_get_user_info(update)}")
        if not context.args:
            await self._reply(update, context, "用法: /add <URL>\n支持 HTTP/HTTPS/磁力链接")
            return

        url = context.args[0]

        # 验证 URL 格式
        is_valid, error_msg = _validate_download_url(url)
        if not is_valid:
            await self._reply(update, context, f"❌ URL 无效: {error_msg}")
            return

        try:
            rpc = self._get_rpc_client()
            gid = await rpc.add_uri(url)
            task = await rpc.get_status(gid)
            # 转义文件名中的 Markdown 特殊字符
            safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            text = f"✅ 任务已添加\n📄 {safe_name}\n🆔 GID: `{gid}`"
            keyboard = build_after_add_keyboard(gid)
            await self._reply(update, context, text, parse_mode="Markdown", reply_markup=keyboard)
            logger.info(f"/add 命令执行成功, GID={gid} - {_get_user_info(update)}")
            # 启动下载监控，完成或失败时通知用户
            chat_id = update.effective_chat.id
            asyncio.create_task(self._start_download_monitor(gid, chat_id))
        except RpcError as e:
            logger.error(f"/add 命令执行失败: {e} - {_get_user_info(update)}")
            await self._reply(update, context, f"❌ 添加失败: {e}")

    async def handle_torrent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理用户发送的种子文件"""
        logger.info(f"收到种子文件 - {_get_user_info(update)}")
        document = update.message.document
        if not document or not document.file_name.endswith(".torrent"):
            return

        try:
            file = await context.bot.get_file(document.file_id)
            torrent_data = await file.download_as_bytearray()
            rpc = self._get_rpc_client()
            gid = await rpc.add_torrent(bytes(torrent_data))
            task = await rpc.get_status(gid)
            # 转义文件名中的 Markdown 特殊字符
            safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            text = f"✅ 种子任务已添加\n📄 {safe_name}\n🆔 GID: `{gid}`"
            keyboard = build_after_add_keyboard(gid)
            await self._reply(update, context, text, parse_mode="Markdown", reply_markup=keyboard)
            logger.info(f"种子任务添加成功, GID={gid} - {_get_user_info(update)}")
            # 启动下载监控，完成或失败时通知用户
            chat_id = update.effective_chat.id
            asyncio.create_task(self._start_download_monitor(gid, chat_id))
        except RpcError as e:
            logger.error(f"种子任务添加失败: {e} - {_get_user_info(update)}")
            await self._reply(update, context, f"❌ 添加种子失败: {e}")

    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理用户直接发送的链接消息（HTTP/HTTPS/磁力链接）"""
        text = update.message.text or ""
        urls = URL_PATTERN.findall(text)
        if not urls:
            return

        logger.info(f"收到链接消息，提取到 {len(urls)} 个链接 - {_get_user_info(update)}")
        chat_id = update.effective_chat.id
        rpc = self._get_rpc_client()

        for url in urls:
            # 验证 URL 格式
            is_valid, error_msg = _validate_download_url(url)
            if not is_valid:
                await self._reply(update, context, f"❌ URL 无效: {error_msg}\n{url[:50]}...")
                continue

            try:
                gid = await rpc.add_uri(url)
                task = await rpc.get_status(gid)
                safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                reply_text = f"✅ 任务已添加\n📄 {safe_name}\n🆔 GID: `{gid}`"
                keyboard = build_after_add_keyboard(gid)
                await self._reply(update, context, reply_text, parse_mode="Markdown", reply_markup=keyboard)
                logger.info(f"链接任务添加成功, GID={gid} - {_get_user_info(update)}")
                asyncio.create_task(self._start_download_monitor(gid, chat_id))
            except RpcError as e:
                logger.error(f"链接任务添加失败: {e} - {_get_user_info(update)}")
                await self._reply(update, context, f"❌ 添加失败: {e}")

    async def list_downloads(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/list - 查看下载列表"""
        logger.info(f"收到 /list 命令 - {_get_user_info(update)}")
        try:
            rpc = self._get_rpc_client()
            stat = await rpc.get_global_stat()
            active_count = int(stat.get("numActive", 0))
            waiting_count = int(stat.get("numWaiting", 0))
            stopped_count = int(stat.get("numStopped", 0))

            keyboard = build_list_type_keyboard(active_count, waiting_count, stopped_count)
            await self._reply(update, context, "📥 选择查看类型：", reply_markup=keyboard)
        except RpcError as e:
            logger.error(f"/list 命令执行失败: {e} - {_get_user_info(update)}")
            await self._reply(update, context, f"❌ 获取列表失败: {e}")

    async def global_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/stats - 全局下载统计"""
        logger.info(f"收到 /stats 命令 - {_get_user_info(update)}")
        try:
            rpc = self._get_rpc_client()
            stat = await rpc.get_global_stat()
            text = (
                "📊 *全局统计*\n"
                f"⬇️ 下载速度: {_format_size(int(stat.get('downloadSpeed', 0)))}/s\n"
                f"⬆️ 上传速度: {_format_size(int(stat.get('uploadSpeed', 0)))}/s\n"
                f"▶️ 活动任务: {stat.get('numActive', 0)}\n"
                f"⏳ 等待任务: {stat.get('numWaiting', 0)}\n"
                f"⏹️ 已停止: {stat.get('numStopped', 0)}"
            )
            await self._reply(update, context, text, parse_mode="Markdown")
        except RpcError as e:
            logger.error(f"/stats 命令执行失败: {e} - {_get_user_info(update)}")
            await self._reply(update, context, f"❌ 获取统计失败: {e}")

    # === 下载任务监控和通知 ===

    async def _start_download_monitor(self, gid: str, chat_id: int) -> None:
        """启动下载任务监控"""
        if gid in self._download_monitors:
            return
        task = asyncio.create_task(self._monitor_download(gid, chat_id))
        self._download_monitors[gid] = task

    async def _monitor_download(self, gid: str, chat_id: int) -> None:
        """监控下载任务直到完成或失败"""
        from .app_ref import get_bot_instance

        try:
            rpc = self._get_rpc_client()
            for _ in range(17280):  # 最长 24 小时 (5秒 * 17280)
                try:
                    task = await rpc.get_status(gid)
                except RpcError:
                    break  # 任务可能已被删除

                if task.status == "complete":
                    if gid not in self._notified_gids:
                        self._notified_gids.add(gid)
                        await self._send_completion_notification(chat_id, task)
                    break
                elif task.status == "error":
                    if gid not in self._notified_gids:
                        self._notified_gids.add(gid)
                        await self._send_error_notification(chat_id, task)
                    break
                elif task.status == "removed":
                    break

                await asyncio.sleep(5)
        finally:
            self._download_monitors.pop(gid, None)

    async def _send_completion_notification(self, chat_id: int, task: DownloadTask) -> None:
        """发送下载完成通知"""
        from .app_ref import get_bot_instance

        _bot_instance = get_bot_instance()
        if _bot_instance is None:
            return
        safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
        text = f"✅ *下载完成*\n📄 {safe_name}\n📦 大小: {task.size_str}\n🆔 GID: `{task.gid}`"
        try:
            await _bot_instance.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            # 注意：自动上传已在 _auto_refresh_task 中通过 _coordinated_auto_upload 处理
            # 这里不再单独触发，避免重复上传
        except Exception as e:
            logger.warning(f"发送完成通知失败 (GID={task.gid}): {e}")

    async def _send_error_notification(self, chat_id: int, task: DownloadTask) -> None:
        """发送下载失败通知"""
        from .app_ref import get_bot_instance

        _bot_instance = get_bot_instance()
        if _bot_instance is None:
            return
        safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
        text = f"❌ *下载失败*\n📄 {safe_name}\n🆔 GID: `{task.gid}`\n⚠️ 原因: {task.error_message or '未知错误'}"
        try:
            await _bot_instance.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"发送失败通知失败 (GID={task.gid}): {e}")
