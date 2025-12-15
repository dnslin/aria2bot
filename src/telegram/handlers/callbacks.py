"""回调处理。"""
from __future__ import annotations

import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.utils.logger import get_logger
from src.core import RpcError
from src.aria2.rpc import Aria2RpcClient, DownloadTask, _format_size
from src.telegram.keyboards import (
    STATUS_EMOJI,
    build_list_type_keyboard,
    build_delete_confirm_keyboard,
    build_cloud_settings_keyboard,
    build_detail_keyboard_with_upload,
    build_onedrive_menu_keyboard,
    build_telegram_channel_menu_keyboard,
    build_telegram_channel_settings_keyboard,
    build_cloud_menu_keyboard,
)

from .base import BUTTON_COMMANDS, _get_user_info

logger = get_logger("handlers.callbacks")


class CallbackHandlersMixin:
    """回调处理 Mixin"""

    async def handle_text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """处理文本消息（包括频道ID输入和按钮点击）"""
        # 先检查是否是频道ID输入
        if await self.handle_channel_id_input(update, context):
            return

        # 然后检查是否是按钮点击
        await self.handle_button_text(update, context)

    async def handle_button_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """处理 Reply Keyboard 按钮点击"""
        text = update.message.text
        if text not in BUTTON_COMMANDS:
            return

        cmd = BUTTON_COMMANDS[text]
        handler_map = {
            "list": self.list_downloads,
            "stats": self.global_stats,
            "start": self.start_service,
            "stop": self.stop_service,
            "restart": self.restart_service,
            "status": self.status,
            "logs": self.view_logs,
            "help": self.help_command,
        }
        if cmd in handler_map:
            await handler_map[cmd](update, context)

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """处理 Inline Keyboard 回调"""
        query = update.callback_query

        try:
            await query.answer()
        except Exception as e:
            logger.warning(f"回调应答失败 (可忽略): {e}")

        data = query.data
        if not data:
            return

        parts = data.split(":")
        if not parts:
            await query.edit_message_text("❌ 无效操作")
            return
        action = parts[0]

        # 安全检查：验证回调数据格式，防止索引越界
        required_parts = {
            "pause": 2,
            "resume": 2,
            "delete": 2,
            "detail": 2,
            "refresh": 2,
            "confirm_del": 3,
            "cancel_del": 3,
        }
        if action in required_parts and len(parts) < required_parts[action]:
            await query.edit_message_text("❌ 无效操作")
            return

        # 点击非详情相关按钮时，停止该消息的自动刷新
        if action not in ("detail", "refresh", "pause", "resume"):
            key = f"{query.message.chat_id}:{query.message.message_id}"
            self._stop_auto_refresh(key)

        try:
            rpc = self._get_rpc_client()

            if action == "list":
                await self._handle_list_callback(query, rpc, parts)
            elif action == "pause":
                await self._handle_pause_callback(query, rpc, parts[1])
            elif action == "resume":
                await self._handle_resume_callback(query, rpc, parts[1])
            elif action == "delete":
                await self._handle_delete_callback(query, parts[1])
            elif action == "confirm_del":
                await self._handle_confirm_delete_callback(query, rpc, parts[1], parts[2])
            elif action == "detail":
                await self._handle_detail_callback(query, rpc, parts[1])
            elif action == "refresh":
                await self._handle_detail_callback(query, rpc, parts[1])
            elif action == "stats":
                await self._handle_stats_callback(query, rpc)
            elif action == "cancel":
                await query.edit_message_text("❌ 操作已取消")
            # 云存储相关回调
            elif action == "cloud":
                await self._handle_cloud_callback(query, update, context, parts)
            elif action == "upload":
                await self._handle_upload_callback(query, update, context, parts)

        except RpcError as e:
            await query.edit_message_text(f"❌ 操作失败: {e}")

    async def _handle_list_callback(
        self, query, rpc: Aria2RpcClient, parts: list
    ) -> None:
        """处理列表相关回调"""
        if parts[1] == "menu":
            stat = await rpc.get_global_stat()
            keyboard = build_list_type_keyboard(
                int(stat.get("numActive", 0)),
                int(stat.get("numWaiting", 0)),
                int(stat.get("numStopped", 0)),
            )
            await query.edit_message_text("📥 选择查看类型：", reply_markup=keyboard)
            return

        list_type = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 1

        if list_type == "active":
            tasks = await rpc.get_active()
            title = "▶️ 活动任务"
        elif list_type == "waiting":
            tasks = await rpc.get_waiting()
            title = "⏳ 等待任务"
        else:  # stopped
            tasks = await rpc.get_stopped()
            title = "✅ 已完成/错误"

        await self._send_task_list(query, tasks, page, list_type, title)

    async def _send_task_list(
        self, query, tasks: list[DownloadTask], page: int, list_type: str, title: str
    ) -> None:
        """发送任务列表"""
        page_size = 5
        total_pages = max(1, (len(tasks) + page_size - 1) // page_size)
        start = (page - 1) * page_size
        page_tasks = tasks[start : start + page_size]

        if not tasks:
            from src.telegram.keyboards import build_task_list_keyboard

            keyboard = build_task_list_keyboard(1, 1, list_type)
            await query.edit_message_text(f"{title}\n\n📭 暂无任务", reply_markup=keyboard)
            return

        lines = [f"{title} ({page}/{total_pages})\n"]
        for t in page_tasks:
            emoji = STATUS_EMOJI.get(t.status, "❓")
            lines.append(f"{emoji} {t.name}")
            lines.append(f"   {t.progress_bar} {t.progress:.1f}%")
            lines.append(f"   {t.size_str} | {t.speed_str}")
            # 添加操作按钮提示
            if t.status == "active":
                lines.append(f"   ⏸ /pause\\_{t.gid[:8]}")
            elif t.status in ("paused", "waiting"):
                lines.append(f"   ▶️ /resume\\_{t.gid[:8]}")
            lines.append(f"   📋 详情: 点击下方按钮\n")

        # 为每个任务添加操作按钮
        task_buttons = []
        for t in page_tasks:
            row = []
            if t.status == "active":
                row.append(
                    {"text": f"⏸ {t.gid[:6]}", "callback_data": f"pause:{t.gid}"}
                )
            elif t.status in ("paused", "waiting"):
                row.append(
                    {"text": f"▶️ {t.gid[:6]}", "callback_data": f"resume:{t.gid}"}
                )
            row.append({"text": f"🗑 {t.gid[:6]}", "callback_data": f"delete:{t.gid}"})
            row.append({"text": f"📋 {t.gid[:6]}", "callback_data": f"detail:{t.gid}"})
            task_buttons.append(row)

        # 构建完整键盘
        keyboard_rows = []
        for row in task_buttons:
            keyboard_rows.append(
                [
                    InlineKeyboardButton(b["text"], callback_data=b["callback_data"])
                    for b in row
                ]
            )

        # 添加翻页按钮
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    "⬅️ 上一页", callback_data=f"list:{list_type}:{page - 1}"
                )
            )
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    "➡️ 下一页", callback_data=f"list:{list_type}:{page + 1}"
                )
            )
        if nav_buttons:
            keyboard_rows.append(nav_buttons)

        keyboard_rows.append(
            [InlineKeyboardButton("🔙 返回列表", callback_data="list:menu")]
        )

        await query.edit_message_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard_rows)
        )

    async def _handle_pause_callback(
        self, query, rpc: Aria2RpcClient, gid: str
    ) -> None:
        """处理暂停回调，然后返回详情页继续刷新"""
        await rpc.pause(gid)
        await self._handle_detail_callback(query, rpc, gid)

    async def _handle_resume_callback(
        self, query, rpc: Aria2RpcClient, gid: str
    ) -> None:
        """处理恢复回调，然后返回详情页继续刷新"""
        await rpc.unpause(gid)
        await self._handle_detail_callback(query, rpc, gid)

    async def _handle_delete_callback(self, query, gid: str) -> None:
        """处理删除确认回调"""
        keyboard = build_delete_confirm_keyboard(gid)
        await query.edit_message_text(
            f"⚠️ 确认删除任务？\n🆔 GID: `{gid}`",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    async def _handle_confirm_delete_callback(
        self, query, rpc: Aria2RpcClient, gid: str, delete_file: str
    ) -> None:
        """处理确认删除回调"""
        task = None
        try:
            task = await rpc.get_status(gid)
        except RpcError:
            pass

        # 尝试删除任务
        try:
            await rpc.remove(gid)
        except RpcError:
            try:
                await rpc.force_remove(gid)
            except RpcError:
                pass
        try:
            await rpc.remove_download_result(gid)
        except RpcError:
            pass

        # 如果需要删除文件（使用 asyncio.to_thread 避免阻塞事件循环）
        file_deleted = False
        if delete_file == "1" and task:
            file_deleted = await asyncio.to_thread(rpc.delete_files, task)

        msg = f"🗑️ 任务已删除\n🆔 GID: `{gid}`"
        if delete_file == "1":
            msg += f"\n📁 文件: {'已删除' if file_deleted else '删除失败或不存在'}"

        await query.edit_message_text(msg, parse_mode="Markdown")

    def _stop_auto_refresh(self, key: str) -> None:
        """停止自动刷新任务并等待清理"""
        if key in self._auto_refresh_tasks:
            task = self._auto_refresh_tasks.pop(key)
            task.cancel()
            # 注意：这里不等待任务完成，因为是同步方法
            # 任务会在 finally 块中自行清理

    async def _handle_detail_callback(
        self, query, rpc: Aria2RpcClient, gid: str
    ) -> None:
        """处理详情回调，启动自动刷新"""
        chat_id = query.message.chat_id
        msg_id = query.message.message_id
        key = f"{chat_id}:{msg_id}"

        # 停止该消息之前的刷新任务
        self._stop_auto_refresh(key)

        # 启动新的自动刷新任务
        task = asyncio.create_task(
            self._auto_refresh_detail(query.message, rpc, gid, key)
        )
        self._auto_refresh_tasks[key] = task

    async def _auto_refresh_detail(
        self, message, rpc: Aria2RpcClient, gid: str, key: str
    ) -> None:
        """自动刷新详情页面"""
        from .app_ref import get_bot_instance

        try:
            last_text = ""
            for _ in range(60):  # 最多刷新 2 分钟
                try:
                    task = await rpc.get_status(gid)
                except RpcError:
                    break

                emoji = STATUS_EMOJI.get(task.status, "❓")
                safe_name = (
                    task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                )
                text = (
                    f"📋 *任务详情*\n"
                    f"📄 文件: {safe_name}\n"
                    f"🆔 GID: `{task.gid}`\n"
                    f"📊 状态: {emoji} {task.status}\n"
                    f"📈 进度: {task.progress_bar} {task.progress:.1f}%\n"
                    f"📦 大小: {task.size_str}\n"
                    f"⬇️ 下载: {task.speed_str}\n"
                    f"⬆️ 上传: {_format_size(task.upload_speed)}/s"
                )
                if task.error_message:
                    text += f"\n❌ 错误: {task.error_message}"

                # 检查是否显示上传按钮
                show_onedrive = (
                    task.status == "complete"
                    and self._onedrive_config
                    and self._onedrive_config.enabled
                )
                show_channel = (
                    task.status == "complete"
                    and self._telegram_channel_config
                    and self._telegram_channel_config.enabled
                )
                keyboard = build_detail_keyboard_with_upload(
                    gid, task.status, show_onedrive, show_channel
                )

                # 只有内容变化时才更新
                if text != last_text:
                    try:
                        await message.edit_text(
                            text, parse_mode="Markdown", reply_markup=keyboard
                        )
                        last_text = text
                    except Exception as e:
                        logger.warning(f"编辑消息失败 (GID={gid}): {e}")
                        break

                # 任务完成或出错时停止刷新
                if task.status in ("complete", "error", "removed"):
                    # 任务完成时检查是否需要自动上传（使用协调上传）
                    if task.status == "complete" and gid not in self._auto_uploaded_gids:
                        _bot_instance = get_bot_instance()
                        need_onedrive = (
                            self._onedrive_config
                            and self._onedrive_config.enabled
                            and self._onedrive_config.auto_upload
                        )
                        need_telegram = (
                            self._telegram_channel_config
                            and self._telegram_channel_config.enabled
                            and self._telegram_channel_config.auto_upload
                        )
                        if need_onedrive or need_telegram:
                            self._auto_uploaded_gids.add(gid)
                            self._channel_uploaded_gids.add(gid)
                            asyncio.create_task(
                                self._coordinated_auto_upload(
                                    message.chat_id, gid, task, _bot_instance
                                )
                            )
                    break

                await asyncio.sleep(2)
        finally:
            self._auto_refresh_tasks.pop(key, None)

    async def _handle_stats_callback(self, query, rpc: Aria2RpcClient) -> None:
        """处理统计回调"""
        stat = await rpc.get_global_stat()
        text = (
            "📊 *全局统计*\n"
            f"⬇️ 下载速度: {_format_size(int(stat.get('downloadSpeed', 0)))}/s\n"
            f"⬆️ 上传速度: {_format_size(int(stat.get('uploadSpeed', 0)))}/s\n"
            f"▶️ 活动任务: {stat.get('numActive', 0)}\n"
            f"⏳ 等待任务: {stat.get('numWaiting', 0)}\n"
            f"⏹️ 已停止: {stat.get('numStopped', 0)}"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 返回列表", callback_data="list:menu")]]
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

    # === 云存储回调处理 ===

    async def _handle_cloud_callback(
        self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list
    ) -> None:
        """处理云存储相关回调"""
        if len(parts) < 2:
            await query.edit_message_text("❌ 无效操作")
            return

        sub_action = parts[1]

        # 主菜单
        if sub_action == "menu":
            keyboard = build_cloud_menu_keyboard()
            await query.edit_message_text(
                "☁️ *云存储管理*\n\n选择要配置的云存储：",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

        # OneDrive 相关
        elif sub_action == "onedrive":
            await self._handle_onedrive_callback(
                query, update, context, parts[2:] if len(parts) > 2 else []
            )

        # Telegram 频道相关
        elif sub_action == "telegram":
            await self._handle_telegram_channel_callback(
                query, update, context, parts[2:] if len(parts) > 2 else []
            )

        # 兼容旧的回调格式
        elif sub_action == "auth":
            await self.cloud_auth(update, context)
        elif sub_action == "status":
            await self._handle_onedrive_callback(query, update, context, ["status"])
        elif sub_action == "settings":
            await self._handle_onedrive_callback(query, update, context, ["settings"])
        elif sub_action == "logout":
            await self._handle_onedrive_callback(query, update, context, ["logout"])
        elif sub_action == "toggle":
            await self._handle_onedrive_callback(
                query, update, context, ["toggle"] + parts[2:]
            )

    async def _handle_onedrive_callback(
        self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list
    ) -> None:
        """处理 OneDrive 相关回调"""
        action = parts[0] if parts else "menu"

        if action == "menu":
            keyboard = build_onedrive_menu_keyboard()
            await query.edit_message_text(
                "☁️ *OneDrive 设置*", parse_mode="Markdown", reply_markup=keyboard
            )

        elif action == "auth":
            await self.cloud_auth(update, context)

        elif action == "status":
            client = self._get_onedrive_client()
            if not client:
                await query.edit_message_text("❌ OneDrive 未配置")
                return
            is_auth = await client.is_authenticated()
            auto_upload = (
                self._onedrive_config.auto_upload if self._onedrive_config else False
            )
            delete_after = (
                self._onedrive_config.delete_after_upload
                if self._onedrive_config
                else False
            )
            remote_path = (
                self._onedrive_config.remote_path
                if self._onedrive_config
                else "/aria2bot"
            )
            text = (
                "☁️ *OneDrive 状态*\n\n"
                f"🔐 认证状态: {'✅ 已认证' if is_auth else '❌ 未认证'}\n"
                f"📤 自动上传: {'✅ 开启' if auto_upload else '❌ 关闭'}\n"
                f"🗑️ 上传后删除: {'✅ 开启' if delete_after else '❌ 关闭'}\n"
                f"📁 远程路径: `{remote_path}`"
            )
            keyboard = build_onedrive_menu_keyboard()
            await query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )

        elif action == "settings":
            auto_upload = (
                self._onedrive_config.auto_upload if self._onedrive_config else False
            )
            delete_after = (
                self._onedrive_config.delete_after_upload
                if self._onedrive_config
                else False
            )
            keyboard = build_cloud_settings_keyboard(auto_upload, delete_after)
            await query.edit_message_text(
                "⚙️ *OneDrive 设置*\n\n点击切换设置：",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

        elif action == "logout":
            client = self._get_onedrive_client()
            if client and await client.logout():
                await query.edit_message_text("✅ 已登出 OneDrive")
            else:
                await query.edit_message_text("❌ 登出失败")

        elif action == "toggle":
            if len(parts) < 2:
                return
            setting = parts[1]
            if self._onedrive_config:
                if setting == "auto_upload":
                    self._onedrive_config.auto_upload = not self._onedrive_config.auto_upload
                elif setting == "delete_after":
                    self._onedrive_config.delete_after_upload = (
                        not self._onedrive_config.delete_after_upload
                    )
                # 保存配置
                self._save_cloud_config()
            auto_upload = (
                self._onedrive_config.auto_upload if self._onedrive_config else False
            )
            delete_after = (
                self._onedrive_config.delete_after_upload
                if self._onedrive_config
                else False
            )
            keyboard = build_cloud_settings_keyboard(auto_upload, delete_after)
            await query.edit_message_text(
                "⚙️ *OneDrive 设置*\n\n点击切换设置：",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

    async def _handle_telegram_channel_callback(
        self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list
    ) -> None:
        """处理 Telegram 频道相关回调"""
        action = parts[0] if parts else "menu"

        if action == "menu":
            enabled = (
                self._telegram_channel_config.enabled
                if self._telegram_channel_config
                else False
            )
            channel_id = (
                self._telegram_channel_config.channel_id
                if self._telegram_channel_config
                else ""
            )
            keyboard = build_telegram_channel_menu_keyboard(enabled, channel_id)
            await query.edit_message_text(
                "📢 *Telegram 频道设置*", parse_mode="Markdown", reply_markup=keyboard
            )

        elif action == "info":
            # 显示频道信息
            if not self._telegram_channel_config:
                await query.answer("频道未配置")
                return
            channel_id = self._telegram_channel_config.channel_id
            if channel_id:
                await query.answer(f"当前频道: {channel_id}")
            else:
                await query.answer("频道ID未设置，请在设置中配置")

        elif action == "settings":
            auto_upload = (
                self._telegram_channel_config.auto_upload
                if self._telegram_channel_config
                else False
            )
            delete_after = (
                self._telegram_channel_config.delete_after_upload
                if self._telegram_channel_config
                else False
            )
            channel_id = (
                self._telegram_channel_config.channel_id
                if self._telegram_channel_config
                else ""
            )
            keyboard = build_telegram_channel_settings_keyboard(
                auto_upload, delete_after, channel_id
            )
            await query.edit_message_text(
                "⚙️ *Telegram 频道设置*\n\n点击切换设置：",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

        elif action == "toggle":
            if len(parts) < 2:
                return
            setting = parts[1]
            if self._telegram_channel_config:
                if setting == "enabled":
                    self._telegram_channel_config.enabled = (
                        not self._telegram_channel_config.enabled
                    )
                    # 重新创建客户端
                    self._recreate_telegram_channel_client(context.bot)
                elif setting == "auto_upload":
                    self._telegram_channel_config.auto_upload = (
                        not self._telegram_channel_config.auto_upload
                    )
                elif setting == "delete_after":
                    self._telegram_channel_config.delete_after_upload = (
                        not self._telegram_channel_config.delete_after_upload
                    )
                # 保存配置
                self._save_cloud_config()

            # 根据来源返回不同页面
            if setting == "enabled":
                enabled = (
                    self._telegram_channel_config.enabled
                    if self._telegram_channel_config
                    else False
                )
                channel_id = (
                    self._telegram_channel_config.channel_id
                    if self._telegram_channel_config
                    else ""
                )
                keyboard = build_telegram_channel_menu_keyboard(enabled, channel_id)
                await query.edit_message_text(
                    "📢 *Telegram 频道设置*",
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            else:
                auto_upload = (
                    self._telegram_channel_config.auto_upload
                    if self._telegram_channel_config
                    else False
                )
                delete_after = (
                    self._telegram_channel_config.delete_after_upload
                    if self._telegram_channel_config
                    else False
                )
                channel_id = (
                    self._telegram_channel_config.channel_id
                    if self._telegram_channel_config
                    else ""
                )
                keyboard = build_telegram_channel_settings_keyboard(
                    auto_upload, delete_after, channel_id
                )
                await query.edit_message_text(
                    "⚙️ *Telegram 频道设置*\n\n点击切换设置：",
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

        elif action == "set_channel":
            # 提示用户输入频道ID
            user_id = update.effective_user.id if update.effective_user else None
            if user_id:
                self._pending_channel_input = {user_id: True}
            await query.edit_message_text(
                "📝 *设置频道ID*\n\n"
                "请发送频道ID或频道用户名：\n"
                "• 频道ID格式: `-100xxxxxxxxxx`\n"
                "• 用户名格式: `@channel_name`\n\n"
                "注意：Bot 必须是频道管理员才能发送消息",
                parse_mode="Markdown",
            )

    async def _handle_upload_callback(
        self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list
    ) -> None:
        """处理上传回调"""
        if len(parts) < 3:
            await query.edit_message_text("❌ 无效操作")
            return

        provider = parts[1]  # onedrive / telegram
        gid = parts[2]

        if provider == "onedrive":
            await self.upload_to_cloud(update, context, gid)
        elif provider == "telegram":
            await self._upload_to_channel_manual(query, update, context, gid)
