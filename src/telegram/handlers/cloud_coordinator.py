"""多云存储协调功能。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from src.utils.logger import get_logger
from src.core import DOWNLOAD_DIR

logger = get_logger("handlers.cloud_coordinator")


class CloudCoordinatorMixin:
    """多云存储协调 Mixin"""

    async def _coordinated_auto_upload(self, chat_id: int, gid: str, task, bot) -> None:
        """协调多云存储并行上传

        当 OneDrive 和 Telegram 频道都启用自动上传且都启用删除时，
        并行执行上传，全部成功后才删除本地文件。
        """
        local_path = Path(task.dir) / task.name
        if not local_path.exists():
            logger.error(f"协调上传失败：本地文件不存在 GID={gid}")
            return

        # 检测哪些云存储需要上传
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

        # 检测是否需要协调删除（两个都启用删除）
        onedrive_delete = need_onedrive and self._onedrive_config.delete_after_upload
        telegram_delete = need_telegram and self._telegram_channel_config.delete_after_upload
        need_coordinated_delete = onedrive_delete and telegram_delete

        if need_coordinated_delete:
            # 并行执行，跳过各自的删除，最后统一删除
            logger.info(f"启动协调并行上传 GID={gid}")
            await self._parallel_upload_with_coordinated_delete(
                chat_id, gid, local_path, task.name, bot
            )
        else:
            # 独立执行（保持现有逻辑）
            if need_onedrive and gid not in self._auto_uploaded_gids:
                self._auto_uploaded_gids.add(gid)
                asyncio.create_task(self._trigger_auto_upload(chat_id, gid))

            if need_telegram and gid not in self._channel_uploaded_gids:
                self._channel_uploaded_gids.add(gid)
                asyncio.create_task(self._trigger_channel_auto_upload(chat_id, gid, bot))

    async def _parallel_upload_with_coordinated_delete(
        self, chat_id: int, gid: str, local_path, task_name: str, bot
    ) -> None:
        """并行上传到多个云存储，全部成功后才删除文件"""
        from .app_ref import get_bot_instance

        # 准备 OneDrive 上传参数
        onedrive_client = self._get_onedrive_client()
        onedrive_authenticated = onedrive_client and await onedrive_client.is_authenticated()

        # 计算 OneDrive 远程路径
        try:
            download_dir = DOWNLOAD_DIR.resolve()
            relative_path = local_path.resolve().relative_to(download_dir)
            remote_path = f"{self._onedrive_config.remote_path}/{relative_path.parent}"
        except ValueError:
            remote_path = self._onedrive_config.remote_path

        # 准备 Telegram 频道客户端
        telegram_client = self._get_telegram_channel_client(bot)

        # 检查文件大小是否超过 Telegram 限制
        telegram_size_ok = True
        if telegram_client:
            file_size = local_path.stat().st_size
            if file_size > telegram_client.get_max_size():
                telegram_size_ok = False
                limit_mb = telegram_client.get_max_size_mb()
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ 文件 {task_name} 超过 {limit_mb}MB 限制，跳过频道上传",
                )

        # 构建上传任务列表
        tasks = []
        task_names = []

        if onedrive_authenticated:
            tasks.append(
                self._do_auto_upload(
                    onedrive_client,
                    local_path,
                    remote_path,
                    task_name,
                    chat_id,
                    gid,
                    skip_delete=True,
                )
            )
            task_names.append("onedrive")

        if telegram_client and telegram_size_ok:
            tasks.append(
                self._do_channel_upload(
                    telegram_client, local_path, task_name, chat_id, gid, bot, skip_delete=True
                )
            )
            task_names.append("telegram")

        if not tasks:
            logger.warning(f"协调上传跳过：没有可用的上传目标 GID={gid}")
            return

        # 并行执行上传
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 分析结果
        all_success = True
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"协调上传异常 ({task_names[i]}) GID={gid}: {result}")
                all_success = False
            elif result is not True:
                all_success = False

        # 只有全部成功才删除
        _bot_instance = get_bot_instance()
        if all_success and len(tasks) > 0:
            _, delete_msg = await self._delete_local_file(local_path, gid)
            if _bot_instance:
                await _bot_instance.send_message(
                    chat_id=chat_id, text=f"📦 所有上传完成: {task_name}\n{delete_msg}"
                )
        elif not all_success:
            if _bot_instance:
                await _bot_instance.send_message(
                    chat_id=chat_id, text=f"⚠️ 部分上传失败，保留本地文件: {task_name}"
                )
