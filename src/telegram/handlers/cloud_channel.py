"""Telegram 频道存储功能处理。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.utils.logger import get_logger
from src.core import RpcError

from .base import _get_user_info

logger = get_logger("handlers.cloud_channel")


class TelegramChannelHandlersMixin:
    """Telegram 频道存储功能 Mixin"""

    async def _trigger_channel_auto_upload(self, chat_id: int, gid: str, bot) -> None:
        """触发频道自动上传"""
        logger.info(f"触发频道自动上传 GID={gid}")

        client = self._get_telegram_channel_client(bot)
        if not client:
            logger.warning(f"频道上传跳过：频道未配置 GID={gid}")
            return

        rpc = self._get_rpc_client()
        try:
            task = await rpc.get_status(gid)
        except RpcError as e:
            logger.error(f"频道上传失败：获取任务信息失败 GID={gid}: {e}")
            return

        if task.status != "complete":
            return

        local_path = Path(task.dir) / task.name
        if not local_path.exists():
            logger.error(
                f"频道上传失败：本地文件不存在 GID={gid}, dir={task.dir}, name={task.name}, path={local_path}"
            )
            return

        # 检查文件大小
        file_size = local_path.stat().st_size
        if file_size > client.get_max_size():
            limit_mb = client.get_max_size_mb()
            await bot.send_message(
                chat_id=chat_id, text=f"⚠️ 文件 {task.name} 超过 {limit_mb}MB 限制，跳过频道上传"
            )
            return

        asyncio.create_task(
            self._do_channel_upload(client, local_path, task.name, chat_id, gid, bot)
        )

    async def _do_channel_upload(
        self,
        client,
        local_path,
        task_name: str,
        chat_id: int,
        gid: str,
        bot,
        skip_delete: bool = False,
    ) -> bool:
        """执行频道上传

        Args:
            skip_delete: 是否跳过删除（用于并行上传协调）

        Returns:
            上传是否成功
        """
        try:
            msg = await bot.send_message(chat_id=chat_id, text=f"📢 正在发送到频道: {task_name}")
        except Exception as e:
            logger.error(f"频道上传失败：发送消息失败 GID={gid}: {e}")
            return False

        try:
            success, result = await client.upload_file(local_path)
            if success:
                result_text = f"✅ 已发送到频道: {task_name}"
                # 只有不跳过删除且配置了删除时才删除
                if (
                    not skip_delete
                    and self._telegram_channel_config
                    and self._telegram_channel_config.delete_after_upload
                ):
                    _, delete_msg = await self._delete_local_file(local_path, gid)
                    result_text += f"\n{delete_msg}"
                await msg.edit_text(result_text)
                logger.info(f"频道上传成功 GID={gid}")
                return True
            else:
                await msg.edit_text(f"❌ 发送到频道失败: {task_name}\n原因: {result}")
                logger.error(f"频道上传失败 GID={gid}: {result}")
                return False
        except Exception as e:
            logger.error(f"频道上传异常 GID={gid}: {e}")
            try:
                await msg.edit_text(f"❌ 发送到频道失败: {task_name}\n错误: {e}")
            except Exception:
                pass
            return False

    async def handle_channel_id_input(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """处理频道ID输入，返回 True 表示已处理"""
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id or user_id not in self._pending_channel_input:
            return False

        # 清除等待状态
        del self._pending_channel_input[user_id]

        text = update.message.text.strip()
        if not text:
            await self._reply(update, context, "❌ 频道ID不能为空")
            return True

        # 验证格式
        if not (
            text.startswith("@") or text.startswith("-100") or text.lstrip("-").isdigit()
        ):
            await self._reply(
                update,
                context,
                "❌ 无效的频道ID格式\n\n"
                "请使用以下格式之一：\n"
                "• `@channel_name`\n"
                "• `-100xxxxxxxxxx`",
                parse_mode="Markdown",
            )
            return True

        # 更新配置
        if self._telegram_channel_config:
            self._telegram_channel_config.channel_id = text
            # 重新创建客户端
            self._recreate_telegram_channel_client(context.bot)
            # 保存配置
            self._save_cloud_config()
            await self._reply(
                update,
                context,
                f"✅ 频道ID已设置为: `{text}`\n\n" "请确保 Bot 已被添加为频道管理员",
                parse_mode="Markdown",
            )
        else:
            await self._reply(update, context, "❌ 频道配置未初始化")

        return True

    async def _upload_to_channel_manual(
        self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, gid: str
    ) -> None:
        """手动上传到频道"""
        import shutil

        client = self._get_telegram_channel_client(context.bot)
        if not client:
            await query.edit_message_text("❌ 频道存储未配置")
            return

        rpc = self._get_rpc_client()
        try:
            task = await rpc.get_status(gid)
        except RpcError as e:
            await query.edit_message_text(f"❌ 获取任务信息失败: {e}")
            return

        if task.status != "complete":
            await query.edit_message_text("❌ 任务未完成，无法上传")
            return

        local_path = Path(task.dir) / task.name
        if not local_path.exists():
            await query.edit_message_text("❌ 本地文件不存在")
            return

        # 检查文件大小
        file_size = local_path.stat().st_size
        if file_size > client.get_max_size():
            limit_mb = client.get_max_size_mb()
            await query.edit_message_text(f"❌ 文件超过 {limit_mb}MB 限制")
            return

        await query.edit_message_text(f"📢 正在发送到频道: {task.name}")
        success, result = await client.upload_file(local_path)
        if success:
            result_text = f"✅ 已发送到频道: {task.name}"
            if (
                self._telegram_channel_config
                and self._telegram_channel_config.delete_after_upload
            ):
                try:
                    if local_path.is_dir():
                        shutil.rmtree(local_path)
                    else:
                        local_path.unlink()
                    result_text += "\n🗑️ 本地文件已删除"
                except Exception as e:
                    result_text += f"\n⚠️ 删除本地文件失败: {e}"
            await query.edit_message_text(result_text)
        else:
            await query.edit_message_text(f"❌ 发送失败: {result}")
