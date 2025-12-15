"""OneDrive 云存储功能处理。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.utils.logger import get_logger
from src.core import RpcError, DOWNLOAD_DIR
from src.cloud.base import UploadProgress, UploadStatus
from src.telegram.keyboards import build_cloud_menu_keyboard

from .base import _get_user_info

logger = get_logger("handlers.cloud_onedrive")


class OneDriveHandlersMixin:
    """OneDrive 云存储功能 Mixin"""

    async def cloud_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """云存储管理菜单"""
        logger.info(f"收到 /cloud 命令 - {_get_user_info(update)}")
        if not self._onedrive_config or not self._onedrive_config.enabled:
            await self._reply(
                update, context, "❌ 云存储功能未启用，请在配置中设置 ONEDRIVE_ENABLED=true"
            )
            return
        keyboard = build_cloud_menu_keyboard()
        await self._reply(
            update, context, "☁️ *云存储管理*", parse_mode="Markdown", reply_markup=keyboard
        )

    async def cloud_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """开始 OneDrive 认证"""
        logger.info(f"收到云存储认证请求 - {_get_user_info(update)}")
        client = self._get_onedrive_client()
        if not client:
            await self._reply(update, context, "❌ OneDrive 未配置")
            return

        if await client.is_authenticated():
            await self._reply(update, context, "✅ OneDrive 已认证")
            return

        url, flow = await client.get_auth_url()
        user_id = update.effective_user.id

        auth_message = await self._reply(
            update,
            context,
            f"🔐 *OneDrive 认证*\n\n"
            f"1\\. 点击下方链接登录 Microsoft 账户\n"
            f"2\\. 授权后会跳转到一个空白页面\n"
            f"3\\. 复制该页面的完整 URL 发送给我\n\n"
            f"[点击认证]({url})",
            parse_mode="Markdown",
        )
        self._pending_auth[user_id] = {"flow": flow, "message": auth_message}

    async def handle_auth_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """处理用户发送的认证回调 URL"""
        text = update.message.text
        if not text or not text.startswith("https://login.microsoftonline.com"):
            return

        user_id = update.effective_user.id
        if user_id not in self._pending_auth:
            return

        client = self._get_onedrive_client()
        if not client:
            return

        user_message = update.message  # 保存用户消息引用
        pending = self._pending_auth[user_id]
        flow = pending["flow"]
        auth_message = pending.get("message")  # 认证指引消息

        if await client.authenticate_with_code(text, flow=flow):
            del self._pending_auth[user_id]
            reply_message = await self._reply(update, context, "✅ OneDrive 认证成功！")
            logger.info(f"OneDrive 认证成功 - {_get_user_info(update)}")
        else:
            # 认证失败时清理认证信息
            del self._pending_auth[user_id]
            await client.logout()  # 删除可能存在的旧 token
            reply_message = await self._reply(update, context, "❌ 认证失败，请重试")
            logger.error(f"OneDrive 认证失败 - {_get_user_info(update)}")

        # 延迟 5 秒后删除敏感消息（包括认证指引消息）
        messages_to_delete = [msg for msg in [user_message, reply_message, auth_message] if msg]
        if messages_to_delete:
            asyncio.create_task(self._delayed_delete_messages(messages_to_delete))

    async def cloud_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """登出云存储"""
        logger.info(f"收到云存储登出请求 - {_get_user_info(update)}")
        client = self._get_onedrive_client()
        if not client:
            await self._reply(update, context, "❌ OneDrive 未配置")
            return

        if await client.logout():
            await self._reply(update, context, "✅ 已登出 OneDrive")
        else:
            await self._reply(update, context, "❌ 登出失败")

    async def cloud_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """查看云存储状态"""
        logger.info(f"收到云存储状态查询 - {_get_user_info(update)}")
        client = self._get_onedrive_client()
        if not client:
            await self._reply(update, context, "❌ OneDrive 未配置")
            return

        is_auth = await client.is_authenticated()
        auto_upload = self._onedrive_config.auto_upload if self._onedrive_config else False
        delete_after = (
            self._onedrive_config.delete_after_upload if self._onedrive_config else False
        )
        remote_path = self._onedrive_config.remote_path if self._onedrive_config else "/aria2bot"

        text = (
            "☁️ *OneDrive 状态*\n\n"
            f"🔐 认证状态: {'✅ 已认证' if is_auth else '❌ 未认证'}\n"
            f"📤 自动上传: {'✅ 开启' if auto_upload else '❌ 关闭'}\n"
            f"🗑️ 上传后删除: {'✅ 开启' if delete_after else '❌ 关闭'}\n"
            f"📁 远程路径: `{remote_path}`"
        )
        await self._reply(update, context, text, parse_mode="Markdown")

    async def upload_to_cloud(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, gid: str
    ) -> None:
        """上传文件到云存储（启动后台任务，不阻塞其他命令）"""
        logger.info(f"收到上传请求 GID={gid} - {_get_user_info(update)}")
        client = self._get_onedrive_client()
        if not client or not await client.is_authenticated():
            await self._reply(update, context, "❌ OneDrive 未认证，请先使用 /cloud 进行认证")
            return

        rpc = self._get_rpc_client()
        try:
            task = await rpc.get_status(gid)
        except RpcError as e:
            await self._reply(update, context, f"❌ 获取任务信息失败: {e}")
            return

        if task.status != "complete":
            await self._reply(update, context, "❌ 任务未完成，无法上传")
            return

        local_path = Path(task.dir) / task.name
        if not local_path.exists():
            await self._reply(update, context, "❌ 本地文件不存在")
            return

        # 计算远程路径（保持目录结构）
        try:
            download_dir = DOWNLOAD_DIR.resolve()
            relative_path = local_path.resolve().relative_to(download_dir)
            remote_path = f"{self._onedrive_config.remote_path}/{relative_path.parent}"
        except ValueError:
            remote_path = self._onedrive_config.remote_path

        msg = await self._reply(update, context, f"☁️ 正在上传: {task.name}\n⏳ 请稍候...")

        # 启动后台上传任务，不阻塞其他命令
        asyncio.create_task(
            self._do_upload_to_cloud(
                client, local_path, remote_path, task.name, msg, gid, _get_user_info(update)
            )
        )

    async def _do_upload_to_cloud(
        self, client, local_path, remote_path: str, task_name: str, msg, gid: str, user_info: str
    ) -> None:
        """后台执行上传任务"""
        import shutil

        loop = asyncio.get_running_loop()

        # 进度回调函数
        async def update_progress(progress: UploadProgress):
            """更新上传进度消息"""
            if progress.status == UploadStatus.UPLOADING and progress.total_size > 0:
                percent = progress.progress
                uploaded_mb = progress.uploaded_size / (1024 * 1024)
                total_mb = progress.total_size / (1024 * 1024)
                progress_text = (
                    f"☁️ 正在上传: {task_name}\n"
                    f"📤 {percent:.1f}% ({uploaded_mb:.1f}MB / {total_mb:.1f}MB)"
                )
                try:
                    await msg.edit_text(progress_text)
                except Exception:
                    pass  # 忽略消息更新失败（如内容未变化）

        def sync_progress_callback(progress: UploadProgress):
            """同步回调，将异步更新调度到事件循环"""
            if progress.status == UploadStatus.UPLOADING:
                asyncio.run_coroutine_threadsafe(update_progress(progress), loop)

        try:
            success = await client.upload_file(
                local_path, remote_path, progress_callback=sync_progress_callback
            )

            if success:
                result_text = f"✅ 上传成功: {task_name}"
                if self._onedrive_config and self._onedrive_config.delete_after_upload:
                    try:
                        if local_path.is_dir():
                            shutil.rmtree(local_path)
                        else:
                            local_path.unlink()
                        result_text += "\n🗑️ 本地文件已删除"
                    except Exception as e:
                        result_text += f"\n⚠️ 删除本地文件失败: {e}"
                await msg.edit_text(result_text)
                logger.info(f"上传成功 GID={gid} - {user_info}")
            else:
                await msg.edit_text(f"❌ 上传失败: {task_name}")
                logger.error(f"上传失败 GID={gid} - {user_info}")
        except Exception as e:
            logger.error(f"上传异常 GID={gid}: {e} - {user_info}")
            try:
                await msg.edit_text(f"❌ 上传失败: {task_name}\n错误: {e}")
            except Exception:
                pass

    async def _trigger_auto_upload(self, chat_id: int, gid: str) -> None:
        """自动上传触发（下载完成后自动调用）"""
        logger.info(f"触发自动上传 GID={gid}")

        client = self._get_onedrive_client()
        if not client or not await client.is_authenticated():
            logger.warning(f"自动上传跳过：OneDrive 未认证 GID={gid}")
            return

        rpc = self._get_rpc_client()
        try:
            task = await rpc.get_status(gid)
        except RpcError as e:
            logger.error(f"自动上传失败：获取任务信息失败 GID={gid}: {e}")
            return

        if task.status != "complete":
            logger.warning(f"自动上传跳过：任务未完成 GID={gid}")
            return

        local_path = Path(task.dir) / task.name
        if not local_path.exists():
            logger.error(f"自动上传失败：本地文件不存在 GID={gid}")
            return

        # 计算远程路径
        try:
            download_dir = DOWNLOAD_DIR.resolve()
            relative_path = local_path.resolve().relative_to(download_dir)
            remote_path = f"{self._onedrive_config.remote_path}/{relative_path.parent}"
        except ValueError:
            remote_path = self._onedrive_config.remote_path

        # 启动后台上传任务
        asyncio.create_task(
            self._do_auto_upload(client, local_path, remote_path, task.name, chat_id, gid)
        )

    async def _do_auto_upload(
        self,
        client,
        local_path,
        remote_path: str,
        task_name: str,
        chat_id: int,
        gid: str,
        skip_delete: bool = False,
    ) -> bool:
        """后台执行自动上传任务

        Args:
            skip_delete: 是否跳过删除（用于并行上传协调）

        Returns:
            上传是否成功
        """
        from .app_ref import get_bot_instance

        _bot_instance = get_bot_instance()
        if _bot_instance is None:
            logger.error(f"自动上传失败：无法获取 bot 实例 GID={gid}")
            return False

        # 发送上传开始通知
        try:
            msg = await _bot_instance.send_message(
                chat_id=chat_id, text=f"☁️ 自动上传开始: {task_name}\n⏳ 请稍候..."
            )
        except Exception as e:
            logger.error(f"自动上传失败：发送消息失败 GID={gid}: {e}")
            return False

        loop = asyncio.get_running_loop()

        # 进度回调函数
        async def update_progress(progress):
            if progress.status == UploadStatus.UPLOADING and progress.total_size > 0:
                percent = progress.progress
                uploaded_mb = progress.uploaded_size / (1024 * 1024)
                total_mb = progress.total_size / (1024 * 1024)
                progress_text = (
                    f"☁️ 自动上传: {task_name}\n"
                    f"📤 {percent:.1f}% ({uploaded_mb:.1f}MB / {total_mb:.1f}MB)"
                )
                try:
                    await msg.edit_text(progress_text)
                except Exception:
                    pass

        def sync_progress_callback(progress):
            if progress.status == UploadStatus.UPLOADING:
                asyncio.run_coroutine_threadsafe(update_progress(progress), loop)

        try:
            success = await client.upload_file(
                local_path, remote_path, progress_callback=sync_progress_callback
            )

            if success:
                result_text = f"✅ 自动上传成功: {task_name}"
                # 只有不跳过删除且配置了删除时才删除
                if (
                    not skip_delete
                    and self._onedrive_config
                    and self._onedrive_config.delete_after_upload
                ):
                    _, delete_msg = await self._delete_local_file(local_path, gid)
                    result_text += f"\n{delete_msg}"
                await msg.edit_text(result_text)
                logger.info(f"自动上传成功 GID={gid}")
                return True
            else:
                await msg.edit_text(f"❌ 自动上传失败: {task_name}")
                logger.error(f"自动上传失败 GID={gid}")
                return False
        except Exception as e:
            logger.error(f"自动上传异常 GID={gid}: {e}")
            try:
                await msg.edit_text(f"❌ 自动上传失败: {task_name}\n错误: {e}")
            except Exception:
                pass
            return False
