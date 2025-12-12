"""Telegram bot command handlers."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from src.utils.logger import get_logger

from src.core import (
    Aria2Config,
    Aria2Error,
    NotInstalledError,
    ServiceError,
    DownloadError,
    ConfigError,
    RpcError,
    is_aria2_installed,
    get_aria2_version,
    generate_rpc_secret,
    ARIA2_CONF,
)
from src.aria2 import Aria2Installer, Aria2ServiceManager
from src.aria2.rpc import Aria2RpcClient, DownloadTask, _format_size
from src.telegram.keyboards import (
    STATUS_EMOJI,
    build_list_type_keyboard,
    build_task_keyboard,
    build_task_list_keyboard,
    build_delete_confirm_keyboard,
    build_detail_keyboard,
    build_after_add_keyboard,
)

logger = get_logger("handlers")


def _get_user_info(update: Update) -> str:
    """获取用户信息用于日志"""
    user = update.effective_user
    if user:
        return f"用户ID={user.id}, 用户名={user.username or 'N/A'}"
    return "未知用户"


import asyncio

class Aria2BotAPI:
    def __init__(self, config: Aria2Config | None = None):
        self.config = config or Aria2Config()
        self.installer = Aria2Installer(self.config)
        self.service = Aria2ServiceManager()
        self._rpc: Aria2RpcClient | None = None
        self._auto_refresh_tasks: dict[str, asyncio.Task] = {}  # chat_id:msg_id -> task

    def _get_rpc_client(self) -> Aria2RpcClient:
        """获取或创建 RPC 客户端"""
        if self._rpc is None:
            secret = self._get_rpc_secret()
            port = self._get_rpc_port() or 6800
            self._rpc = Aria2RpcClient(port=port, secret=secret)
        return self._rpc

    async def _reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
        if update.effective_message:
            return await update.effective_message.reply_text(text, **kwargs)
        if update.effective_chat:
            return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, **kwargs)
        return None

    def _get_rpc_secret(self) -> str:
        if self.config.rpc_secret:
            return self.config.rpc_secret
        if ARIA2_CONF.exists():
            try:
                for line in ARIA2_CONF.read_text(encoding="utf-8", errors="ignore").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("rpc-secret="):
                        secret = stripped.split("=", 1)[1].strip()
                        if secret:
                            self.config.rpc_secret = secret
                            return secret
            except OSError:
                return ""
        return ""

    def _get_rpc_port(self) -> int | None:
        if ARIA2_CONF.exists():
            try:
                for line in ARIA2_CONF.read_text(encoding="utf-8", errors="ignore").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("rpc-listen-port="):
                        port_str = stripped.split("=", 1)[1].strip()
                        if port_str.isdigit():
                            return int(port_str)
            except OSError:
                return None
        return self.config.rpc_port

    async def install(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /install 命令 - {_get_user_info(update)}")
        if is_aria2_installed():
            await self._reply(update, context, "aria2 已安装，无需重复安装。如需重新安装，请先运行 /uninstall")
            return
        await self._reply(update, context, "正在安装 aria2，处理中，请稍候...")
        try:
            result = await self.installer.install()
            version = get_aria2_version() or result.get("version") or "未知"
            rpc_secret = self._get_rpc_secret() or "未设置"
            rpc_port = self._get_rpc_port() or self.config.rpc_port
            await self._reply(
                update,
                context,
                "\n".join(
                    [
                        "安装完成 ✅",
                        f"版本：{version}",
                        f"二进制：{result.get('binary')}",
                        f"配置目录：{result.get('config_dir')}",
                        f"配置文件：{result.get('config')}",
                        f"RPC 端口：{rpc_port}",
                        f"RPC 密钥：{rpc_secret}",
                    ]
                ),
            )
            logger.info(f"/install 命令执行成功 - {_get_user_info(update)}")
        except (DownloadError, ConfigError, Aria2Error) as exc:
            logger.error(f"/install 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"安装失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/install 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"安装失败，发生未知错误：{exc}")

    async def uninstall(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /uninstall 命令 - {_get_user_info(update)}")
        if not is_aria2_installed():
            await self._reply(update, context, "aria2 未安装，无需卸载")
            return
        await self._reply(update, context, "正在卸载 aria2，处理中，请稍候...")
        try:
            try:
                self.service.stop()
            except ServiceError:
                pass
            self.installer.uninstall()
            await self._reply(update, context, "卸载完成 ✅")
            logger.info(f"/uninstall 命令执行成功 - {_get_user_info(update)}")
        except Aria2Error as exc:
            logger.error(f"/uninstall 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"卸载失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/uninstall 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"卸载失败，发生未知错误：{exc}")

    async def start_service(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /start 命令 - {_get_user_info(update)}")
        try:
            if not is_aria2_installed():
                logger.info(f"/start 命令: aria2 未安装 - {_get_user_info(update)}")
                await self._reply(update, context, "aria2 未安装，请先运行 /install")
                return
            self.service.start()
            await self._reply(update, context, "aria2 服务已启动 ✅")
            logger.info(f"/start 命令执行成功 - {_get_user_info(update)}")
        except NotInstalledError:
            logger.info(f"/start 命令: aria2 未安装 - {_get_user_info(update)}")
            await self._reply(update, context, "aria2 未安装，请先运行 /install")
        except ServiceError as exc:
            logger.error(f"/start 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"启动失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/start 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"启动失败，发生未知错误：{exc}")

    async def stop_service(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /stop 命令 - {_get_user_info(update)}")
        try:
            self.service.stop()
            await self._reply(update, context, "aria2 服务已停止 ✅")
            logger.info(f"/stop 命令执行成功 - {_get_user_info(update)}")
        except ServiceError as exc:
            logger.error(f"/stop 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"停止失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/stop 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"停止失败，发生未知错误：{exc}")

    async def restart_service(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /restart 命令 - {_get_user_info(update)}")
        try:
            self.service.restart()
            await self._reply(update, context, "aria2 服务已重启 ✅")
            logger.info(f"/restart 命令执行成功 - {_get_user_info(update)}")
        except ServiceError as exc:
            logger.error(f"/restart 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"重启失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/restart 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"重启失败，发生未知错误：{exc}")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /status 命令 - {_get_user_info(update)}")
        try:
            info = self.service.status()
            version = get_aria2_version() or "未知"
            rpc_secret = self._get_rpc_secret() or "未设置"
            rpc_port = self._get_rpc_port() or self.config.rpc_port or "未知"
        except ServiceError as exc:
            logger.error(f"/status 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"获取状态失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/status 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"获取状态失败，发生未知错误：{exc}")
            return

        text = (
            "*Aria2 状态*\n"
            f"- 安装状态：{'已安装 ✅' if info.get('installed') or is_aria2_installed() else '未安装 ❌'}\n"
            f"- 运行状态：{'运行中 ✅' if info.get('running') else '未运行 ❌'}\n"
            f"- PID：`{info.get('pid') or 'N/A'}`\n"
            f"- 版本：`{version}`\n"
            f"- RPC 端口：`{rpc_port}`\n"
            f"- RPC 密钥：`{rpc_secret}`"
        )
        await self._reply(update, context, text, parse_mode="Markdown")
        logger.info(f"/status 命令执行成功 - {_get_user_info(update)}")

    async def view_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /logs 命令 - {_get_user_info(update)}")
        try:
            logs = self.service.view_log(lines=30)
        except ServiceError as exc:
            logger.error(f"/logs 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"读取日志失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/logs 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"读取日志失败，发生未知错误：{exc}")
            return

        if not logs.strip():
            await self._reply(update, context, "暂无日志内容。")
            logger.info(f"/logs 命令执行成功(无日志) - {_get_user_info(update)}")
            return

        await self._reply(update, context, f"最近 30 行日志：\n{logs}")
        logger.info(f"/logs 命令执行成功 - {_get_user_info(update)}")

    async def clear_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /clear_logs 命令 - {_get_user_info(update)}")
        try:
            self.service.clear_log()
            await self._reply(update, context, "日志已清空 ✅")
            logger.info(f"/clear_logs 命令执行成功 - {_get_user_info(update)}")
        except ServiceError as exc:
            logger.error(f"/clear_logs 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"清空日志失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/clear_logs 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"清空日志失败，发生未知错误：{exc}")

    async def set_secret(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """设置自定义 RPC 密钥"""
        logger.info(f"收到 /set_secret 命令 - {_get_user_info(update)}")
        if not context.args or len(context.args) != 1:
            await self._reply(update, context, "用法: /set_secret <密钥>\n密钥长度需为 16 位")
            return
        new_secret = context.args[0]
        if len(new_secret) != 16:
            await self._reply(update, context, "密钥长度需为 16 位")
            return
        try:
            self.service.update_rpc_secret(new_secret)
            self.config.rpc_secret = new_secret
            self.service.restart()
            await self._reply(update, context, f"RPC 密钥已更新并重启服务 ✅\n新密钥: `{new_secret}`", parse_mode="Markdown")
            logger.info(f"/set_secret 命令执行成功 - {_get_user_info(update)}")
        except ConfigError as exc:
            logger.error(f"/set_secret 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"设置密钥失败：{exc}")
        except ServiceError as exc:
            logger.error(f"/set_secret 命令执行失败(重启服务): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"密钥已更新但重启服务失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/set_secret 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"设置密钥失败，发生未知错误：{exc}")

    async def reset_secret(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """重新生成随机 RPC 密钥"""
        logger.info(f"收到 /reset_secret 命令 - {_get_user_info(update)}")
        try:
            new_secret = generate_rpc_secret()
            self.service.update_rpc_secret(new_secret)
            self.config.rpc_secret = new_secret
            self.service.restart()
            await self._reply(update, context, f"RPC 密钥已重新生成并重启服务 ✅\n新密钥: `{new_secret}`", parse_mode="Markdown")
            logger.info(f"/reset_secret 命令执行成功 - {_get_user_info(update)}")
        except ConfigError as exc:
            logger.error(f"/reset_secret 命令执行失败: {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"重置密钥失败：{exc}")
        except ServiceError as exc:
            logger.error(f"/reset_secret 命令执行失败(重启服务): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"密钥已更新但重启服务失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"/reset_secret 命令执行失败(未知错误): {exc} - {_get_user_info(update)}")
            await self._reply(update, context, f"重置密钥失败，发生未知错误：{exc}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /help 命令 - {_get_user_info(update)}")
        commands = [
            "*服务管理*",
            "/install - 安装 aria2",
            "/uninstall - 卸载 aria2",
            "/start - 启动 aria2 服务",
            "/stop - 停止 aria2 服务",
            "/restart - 重启 aria2 服务",
            "/status - 查看 aria2 状态",
            "/logs - 查看最近日志",
            "/clear\\_logs - 清空日志",
            "/set\\_secret <密钥> - 设置 RPC 密钥",
            "/reset\\_secret - 重新生成 RPC 密钥",
            "",
            "*下载管理*",
            "/add <URL> - 添加下载任务",
            "/list - 查看下载列表",
            "/stats - 全局下载统计",
            "",
            "/help - 显示此帮助",
        ]
        await self._reply(update, context, "可用命令：\n" + "\n".join(commands), parse_mode="Markdown")

    # === 下载管理命令 ===

    async def add_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/add <url> - 添加下载任务"""
        logger.info(f"收到 /add 命令 - {_get_user_info(update)}")
        if not context.args:
            await self._reply(update, context, "用法: /add <URL>\n支持 HTTP/HTTPS/磁力链接")
            return

        url = context.args[0]
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
        except RpcError as e:
            logger.error(f"种子任务添加失败: {e} - {_get_user_info(update)}")
            await self._reply(update, context, f"❌ 添加种子失败: {e}")

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

    # === Callback Query 处理 ===

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        action = parts[0]

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

        except RpcError as e:
            await query.edit_message_text(f"❌ 操作失败: {e}")

    async def _handle_list_callback(self, query, rpc: Aria2RpcClient, parts: list) -> None:
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

    async def _send_task_list(self, query, tasks: list[DownloadTask], page: int, list_type: str, title: str) -> None:
        """发送任务列表"""
        page_size = 5
        total_pages = max(1, (len(tasks) + page_size - 1) // page_size)
        start = (page - 1) * page_size
        page_tasks = tasks[start:start + page_size]

        if not tasks:
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
                row.append({"text": f"⏸ {t.gid[:6]}", "callback_data": f"pause:{t.gid}"})
            elif t.status in ("paused", "waiting"):
                row.append({"text": f"▶️ {t.gid[:6]}", "callback_data": f"resume:{t.gid}"})
            row.append({"text": f"🗑 {t.gid[:6]}", "callback_data": f"delete:{t.gid}"})
            row.append({"text": f"📋 {t.gid[:6]}", "callback_data": f"detail:{t.gid}"})
            task_buttons.append(row)

        # 构建完整键盘
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard_rows = []
        for row in task_buttons:
            keyboard_rows.append([InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row])

        # 添加翻页按钮
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"list:{list_type}:{page - 1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"list:{list_type}:{page + 1}"))
        if nav_buttons:
            keyboard_rows.append(nav_buttons)

        keyboard_rows.append([InlineKeyboardButton("🔙 返回列表", callback_data="list:menu")])

        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard_rows))

    async def _handle_pause_callback(self, query, rpc: Aria2RpcClient, gid: str) -> None:
        """处理暂停回调，然后返回详情页继续刷新"""
        await rpc.pause(gid)
        await self._handle_detail_callback(query, rpc, gid)

    async def _handle_resume_callback(self, query, rpc: Aria2RpcClient, gid: str) -> None:
        """处理恢复回调，然后返回详情页继续刷新"""
        await rpc.unpause(gid)
        await self._handle_detail_callback(query, rpc, gid)

    async def _handle_delete_callback(self, query, gid: str) -> None:
        """处理删除确认回调"""
        keyboard = build_delete_confirm_keyboard(gid)
        await query.edit_message_text(f"⚠️ 确认删除任务？\n🆔 GID: `{gid}`",
                                      parse_mode="Markdown", reply_markup=keyboard)

    async def _handle_confirm_delete_callback(self, query, rpc: Aria2RpcClient, gid: str, delete_file: str) -> None:
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

        # 如果需要删除文件
        file_deleted = False
        if delete_file == "1" and task:
            file_deleted = rpc.delete_files(task)

        msg = f"🗑️ 任务已删除\n🆔 GID: `{gid}`"
        if delete_file == "1":
            msg += f"\n📁 文件: {'已删除' if file_deleted else '删除失败或不存在'}"

        await query.edit_message_text(msg, parse_mode="Markdown")

    def _stop_auto_refresh(self, key: str) -> None:
        """停止自动刷新任务"""
        if key in self._auto_refresh_tasks:
            self._auto_refresh_tasks[key].cancel()
            del self._auto_refresh_tasks[key]

    async def _handle_detail_callback(self, query, rpc: Aria2RpcClient, gid: str) -> None:
        """处理详情回调，启动自动刷新"""
        chat_id = query.message.chat_id
        msg_id = query.message.message_id
        key = f"{chat_id}:{msg_id}"

        # 停止该消息之前的刷新任务
        self._stop_auto_refresh(key)

        # 启动新的自动刷新任务
        task = asyncio.create_task(self._auto_refresh_detail(query.message, rpc, gid, key))
        self._auto_refresh_tasks[key] = task

    async def _auto_refresh_detail(self, message, rpc: Aria2RpcClient, gid: str, key: str) -> None:
        """自动刷新详情页面"""
        try:
            last_text = ""
            for _ in range(60):  # 最多刷新 2 分钟
                try:
                    task = await rpc.get_status(gid)
                except RpcError:
                    break

                emoji = STATUS_EMOJI.get(task.status, "❓")
                safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
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

                keyboard = build_detail_keyboard(gid, task.status)

                # 只有内容变化时才更新
                if text != last_text:
                    try:
                        await message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
                        last_text = text
                    except Exception:
                        break

                # 任务完成或出错时停止刷新
                if task.status in ("complete", "error", "removed"):
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
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回列表", callback_data="list:menu")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)


def build_handlers(api: Aria2BotAPI) -> list:
    """构建 Handler 列表"""
    return [
        # 服务管理命令
        CommandHandler("install", api.install),
        CommandHandler("uninstall", api.uninstall),
        CommandHandler("start", api.start_service),
        CommandHandler("stop", api.stop_service),
        CommandHandler("restart", api.restart_service),
        CommandHandler("status", api.status),
        CommandHandler("logs", api.view_logs),
        CommandHandler("clear_logs", api.clear_logs),
        CommandHandler("set_secret", api.set_secret),
        CommandHandler("reset_secret", api.reset_secret),
        CommandHandler("help", api.help_command),
        # 下载管理命令
        CommandHandler("add", api.add_download),
        CommandHandler("list", api.list_downloads),
        CommandHandler("stats", api.global_stats),
        # 种子文件处理
        MessageHandler(filters.Document.FileExtension("torrent"), api.handle_torrent),
        # Callback Query 处理
        CallbackQueryHandler(api.handle_callback),
    ]
