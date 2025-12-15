"""Telegram bot command handlers."""
from __future__ import annotations

from urllib.parse import urlparse

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
    DOWNLOAD_DIR,
)
from src.core.config import OneDriveConfig, TelegramChannelConfig
from src.cloud.base import UploadProgress, UploadStatus
from src.aria2 import Aria2Installer, Aria2ServiceManager
from src.aria2.rpc import Aria2RpcClient, DownloadTask, _format_size
from src.telegram.keyboards import (
    STATUS_EMOJI,
    build_list_type_keyboard,
    build_task_list_keyboard,
    build_delete_confirm_keyboard,
    build_after_add_keyboard,
    build_main_reply_keyboard,
    build_cloud_menu_keyboard,
    build_cloud_settings_keyboard,
    build_detail_keyboard_with_upload,
)

# Reply Keyboard 按钮文本到命令的映射
BUTTON_COMMANDS = {
    "📥 下载列表": "list",
    "📊 统计": "stats",
    "▶️ 启动": "start",
    "⏹ 停止": "stop",
    "🔄 重启": "restart",
    "📋 状态": "status",
    "📜 日志": "logs",
    "❓ 帮助": "help",
}

logger = get_logger("handlers")


def _get_user_info(update: Update) -> str:
    """获取用户信息用于日志"""
    user = update.effective_user
    if user:
        return f"用户ID={user.id}, 用户名={user.username or 'N/A'}"
    return "未知用户"


def _validate_download_url(url: str) -> tuple[bool, str]:
    """验证下载 URL 的有效性，防止恶意输入"""
    # 检查 URL 长度
    if len(url) > 2048:
        return False, "URL 过长（最大 2048 字符）"

    # 磁力链接直接通过
    if url.startswith("magnet:"):
        return True, ""

    # 验证 HTTP/HTTPS URL
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"不支持的协议: {parsed.scheme or '无'}，仅支持 HTTP/HTTPS/磁力链接"
        if not parsed.netloc:
            return False, "无效的 URL 格式"
        return True, ""
    except Exception:
        return False, "URL 解析失败"


import asyncio
from functools import wraps

class Aria2BotAPI:
    def __init__(self, config: Aria2Config | None = None, allowed_users: set[int] | None = None,
                 onedrive_config: OneDriveConfig | None = None,
                 telegram_channel_config: TelegramChannelConfig | None = None,
                 api_base_url: str = ""):
        self.config = config or Aria2Config()
        self.allowed_users = allowed_users or set()
        self.installer = Aria2Installer(self.config)
        self.service = Aria2ServiceManager()
        self._rpc: Aria2RpcClient | None = None
        self._auto_refresh_tasks: dict[str, asyncio.Task] = {}  # chat_id:msg_id -> task
        self._auto_uploaded_gids: set[str] = set()  # 已自动上传的任务GID，防止重复上传
        self._download_monitors: dict[str, asyncio.Task] = {}  # gid -> 监控任务
        self._notified_gids: set[str] = set()  # 已通知的 GID，防止重复通知
        # 云存储相关
        self._onedrive_config = onedrive_config
        self._onedrive = None
        self._pending_auth: dict[int, dict] = {}  # user_id -> flow
        # Telegram 频道存储
        self._telegram_channel_config = telegram_channel_config
        self._telegram_channel = None
        self._api_base_url = api_base_url
        self._channel_uploaded_gids: set[str] = set()  # 已上传到频道的 GID

    async def _check_permission(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """检查用户权限，返回 True 表示有权限"""
        # 未配置白名单时拒绝所有用户
        if not self.allowed_users:
            logger.warning(f"未配置 ALLOWED_USERS，拒绝访问 - {_get_user_info(update)}")
            await self._reply(update, context, "⚠️ Bot 未配置允许的用户，请联系管理员")
            return False
        user_id = update.effective_user.id if update.effective_user else None
        if user_id and user_id in self.allowed_users:
            return True
        logger.warning(f"未授权访问 - {_get_user_info(update)}")
        await self._reply(update, context, "🚫 您没有权限使用此 Bot")
        return False

    def _get_rpc_client(self) -> Aria2RpcClient:
        """获取或创建 RPC 客户端"""
        if self._rpc is None:
            secret = self._get_rpc_secret()
            port = self._get_rpc_port() or 6800
            self._rpc = Aria2RpcClient(port=port, secret=secret)
        return self._rpc

    def _get_onedrive_client(self):
        """获取或创建 OneDrive 客户端"""
        if self._onedrive is None and self._onedrive_config and self._onedrive_config.enabled:
            from src.cloud.onedrive import OneDriveClient
            self._onedrive = OneDriveClient(self._onedrive_config)
        return self._onedrive

    def _get_telegram_channel_client(self, bot):
        """获取或创建 Telegram 频道客户端"""
        if self._telegram_channel is None and self._telegram_channel_config and self._telegram_channel_config.enabled:
            from src.cloud.telegram_channel import TelegramChannelClient
            is_local_api = bool(self._api_base_url)
            self._telegram_channel = TelegramChannelClient(self._telegram_channel_config, bot, is_local_api)
        return self._telegram_channel

    async def _reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
        if update.effective_message:
            return await update.effective_message.reply_text(text, **kwargs)
        if update.effective_chat:
            return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, **kwargs)
        return None

    async def _delayed_delete_messages(self, messages: list, delay: int = 5) -> None:
        """延迟删除多条消息"""
        try:
            await asyncio.sleep(delay)
            for msg in messages:
                try:
                    await msg.delete()
                except Exception as e:
                    logger.warning(f"删除消息失败: {e}")
            logger.debug("已删除敏感认证消息")
        except Exception as e:
            logger.warning(f"延迟删除任务失败: {e}")

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
                        f"RPC 密钥：{rpc_secret[:4]}****{rpc_secret[-4:] if len(rpc_secret) > 8 else '****'}",
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
            f"- RPC 密钥：`{rpc_secret[:4]}****{rpc_secret[-4:] if len(rpc_secret) > 8 else '****'}`"
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
            await self._reply(update, context, f"RPC 密钥已更新并重启服务 ✅\n新密钥: `{new_secret[:4]}****{new_secret[-4:]}`", parse_mode="Markdown")
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
            await self._reply(update, context, f"RPC 密钥已重新生成并重启服务 ✅\n新密钥: `{new_secret[:4]}****{new_secret[-4:]}`", parse_mode="Markdown")
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
            "*云存储*",
            "/cloud - 云存储管理菜单",
            "",
            "/menu - 显示快捷菜单",
            "/help - 显示此帮助",
        ]
        await self._reply(update, context, "可用命令：\n" + "\n".join(commands), parse_mode="Markdown")

    async def menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /menu 命令，显示 Reply Keyboard 主菜单"""
        logger.info(f"收到 /menu 命令 - {_get_user_info(update)}")
        keyboard = build_main_reply_keyboard()
        await self._reply(
            update, context,
            "📋 *快捷菜单*\n\n使用下方按钮快速操作，或输入命令：\n/add <URL> - 添加下载任务",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    # === 云存储命令 ===

    async def cloud_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """云存储管理菜单"""
        logger.info(f"收到 /cloud 命令 - {_get_user_info(update)}")
        if not self._onedrive_config or not self._onedrive_config.enabled:
            await self._reply(update, context, "❌ 云存储功能未启用，请在配置中设置 ONEDRIVE_ENABLED=true")
            return
        keyboard = build_cloud_menu_keyboard()
        await self._reply(update, context, "☁️ *云存储管理*", parse_mode="Markdown", reply_markup=keyboard)

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
            update, context,
            f"🔐 *OneDrive 认证*\n\n"
            f"1\\. 点击下方链接登录 Microsoft 账户\n"
            f"2\\. 授权后会跳转到一个空白页面\n"
            f"3\\. 复制该页面的完整 URL 发送给我\n\n"
            f"[点击认证]({url})",
            parse_mode="Markdown"
        )
        self._pending_auth[user_id] = {"flow": flow, "message": auth_message}

    async def handle_auth_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        delete_after = self._onedrive_config.delete_after_upload if self._onedrive_config else False
        remote_path = self._onedrive_config.remote_path if self._onedrive_config else "/aria2bot"

        text = (
            "☁️ *OneDrive 状态*\n\n"
            f"🔐 认证状态: {'✅ 已认证' if is_auth else '❌ 未认证'}\n"
            f"📤 自动上传: {'✅ 开启' if auto_upload else '❌ 关闭'}\n"
            f"🗑️ 上传后删除: {'✅ 开启' if delete_after else '❌ 关闭'}\n"
            f"📁 远程路径: `{remote_path}`"
        )
        await self._reply(update, context, text, parse_mode="Markdown")

    async def upload_to_cloud(self, update: Update, context: ContextTypes.DEFAULT_TYPE, gid: str) -> None:
        """上传文件到云存储（启动后台任务，不阻塞其他命令）"""
        from pathlib import Path

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
        asyncio.create_task(self._do_upload_to_cloud(
            client, local_path, remote_path, task.name, msg, gid, _get_user_info(update)
        ))

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
            success = await client.upload_file(local_path, remote_path, progress_callback=sync_progress_callback)

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
        from pathlib import Path

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
        asyncio.create_task(self._do_auto_upload(
            client, local_path, remote_path, task.name, chat_id, gid
        ))

    async def _do_auto_upload(
        self, client, local_path, remote_path: str, task_name: str, chat_id: int, gid: str
    ) -> None:
        """后台执行自动上传任务"""
        import shutil
        from .app import _bot_instance  # 获取全局 bot 实例

        if _bot_instance is None:
            logger.error(f"自动上传失败：无法获取 bot 实例 GID={gid}")
            return

        # 发送上传开始通知
        try:
            msg = await _bot_instance.send_message(
                chat_id=chat_id,
                text=f"☁️ 自动上传开始: {task_name}\n⏳ 请稍候..."
            )
        except Exception as e:
            logger.error(f"自动上传失败：发送消息失败 GID={gid}: {e}")
            return

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
            success = await client.upload_file(local_path, remote_path, progress_callback=sync_progress_callback)

            if success:
                result_text = f"✅ 自动上传成功: {task_name}"
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
                logger.info(f"自动上传成功 GID={gid}")
            else:
                await msg.edit_text(f"❌ 自动上传失败: {task_name}")
                logger.error(f"自动上传失败 GID={gid}")
        except Exception as e:
            logger.error(f"自动上传异常 GID={gid}: {e}")
            try:
                await msg.edit_text(f"❌ 自动上传失败: {task_name}\n错误: {e}")
            except Exception:
                pass

    async def _trigger_channel_auto_upload(self, chat_id: int, gid: str, bot) -> None:
        """触发频道自动上传"""
        from pathlib import Path

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
            logger.error(f"频道上传失败：本地文件不存在 GID={gid}, dir={task.dir}, name={task.name}, path={local_path}")
            return

        # 检查文件大小
        file_size = local_path.stat().st_size
        if file_size > client.get_max_size():
            limit_mb = client.get_max_size_mb()
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ 文件 {task.name} 超过 {limit_mb}MB 限制，跳过频道上传"
            )
            return

        asyncio.create_task(self._do_channel_upload(client, local_path, task.name, chat_id, gid, bot))

    async def _do_channel_upload(self, client, local_path, task_name: str, chat_id: int, gid: str, bot) -> None:
        """执行频道上传"""
        import shutil

        try:
            msg = await bot.send_message(chat_id=chat_id, text=f"📢 正在发送到频道: {task_name}")
        except Exception as e:
            logger.error(f"频道上传失败：发送消息失败 GID={gid}: {e}")
            return

        try:
            success, result = await client.upload_file(local_path)
            if success:
                result_text = f"✅ 已发送到频道: {task_name}"
                if self._telegram_channel_config and self._telegram_channel_config.delete_after_upload:
                    try:
                        if local_path.is_dir():
                            shutil.rmtree(local_path)
                        else:
                            local_path.unlink()
                        result_text += "\n🗑️ 本地文件已删除"
                    except Exception as e:
                        result_text += f"\n⚠️ 删除本地文件失败: {e}"
                await msg.edit_text(result_text)
                logger.info(f"频道上传成功 GID={gid}")
            else:
                await msg.edit_text(f"❌ 发送到频道失败: {task_name}\n原因: {result}")
                logger.error(f"频道上传失败 GID={gid}: {result}")
        except Exception as e:
            logger.error(f"频道上传异常 GID={gid}: {e}")
            try:
                await msg.edit_text(f"❌ 发送到频道失败: {task_name}\n错误: {e}")
            except Exception:
                pass

    async def handle_button_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    # === 下载管理命令 ===

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
        if not parts:
            await query.edit_message_text("❌ 无效操作")
            return
        action = parts[0]

        # 安全检查：验证回调数据格式，防止索引越界
        required_parts = {
            "pause": 2, "resume": 2, "delete": 2, "detail": 2, "refresh": 2,
            "confirm_del": 3, "cancel_del": 3,
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

                # 检查是否显示上传按钮
                show_onedrive = (
                    task.status == "complete" and
                    self._onedrive_config and
                    self._onedrive_config.enabled
                )
                show_channel = (
                    task.status == "complete" and
                    self._telegram_channel_config and
                    self._telegram_channel_config.enabled
                )
                keyboard = build_detail_keyboard_with_upload(gid, task.status, show_onedrive, show_channel)

                # 只有内容变化时才更新
                if text != last_text:
                    try:
                        await message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
                        last_text = text
                    except Exception as e:
                        logger.warning(f"编辑消息失败 (GID={gid}): {e}")
                        break

                # 任务完成或出错时停止刷新
                if task.status in ("complete", "error", "removed"):
                    # 任务完成时检查是否需要自动上传
                    if (task.status == "complete" and
                        gid not in self._auto_uploaded_gids and
                        self._onedrive_config and
                        self._onedrive_config.enabled and
                        self._onedrive_config.auto_upload):
                        self._auto_uploaded_gids.add(gid)
                        asyncio.create_task(self._trigger_auto_upload(message.chat_id, gid))
                    break

                await asyncio.sleep(2)
        finally:
            self._auto_refresh_tasks.pop(key, None)

    # === 下载任务监控和通知 ===

    async def _start_download_monitor(self, gid: str, chat_id: int) -> None:
        """启动下载任务监控"""
        if gid in self._download_monitors:
            return
        task = asyncio.create_task(self._monitor_download(gid, chat_id))
        self._download_monitors[gid] = task

    async def _monitor_download(self, gid: str, chat_id: int) -> None:
        """监控下载任务直到完成或失败"""
        from .app import _bot_instance
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
        from .app import _bot_instance
        if _bot_instance is None:
            return
        safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
        text = f"✅ *下载完成*\n📄 {safe_name}\n📦 大小: {task.size_str}\n🆔 GID: `{task.gid}`"
        try:
            await _bot_instance.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            # 触发频道自动上传
            if (self._telegram_channel_config and
                self._telegram_channel_config.enabled and
                self._telegram_channel_config.auto_upload and
                task.gid not in self._channel_uploaded_gids):
                self._channel_uploaded_gids.add(task.gid)
                asyncio.create_task(self._trigger_channel_auto_upload(chat_id, task.gid, _bot_instance))
        except Exception as e:
            logger.warning(f"发送完成通知失败 (GID={task.gid}): {e}")

    async def _send_error_notification(self, chat_id: int, task: DownloadTask) -> None:
        """发送下载失败通知"""
        from .app import _bot_instance
        if _bot_instance is None:
            return
        safe_name = task.name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
        text = f"❌ *下载失败*\n📄 {safe_name}\n🆔 GID: `{task.gid}`\n⚠️ 原因: {task.error_message or '未知错误'}"
        try:
            await _bot_instance.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"发送失败通知失败 (GID={task.gid}): {e}")

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

    # === 云存储回调处理 ===

    async def _handle_cloud_callback(self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list) -> None:
        """处理云存储相关回调"""
        if len(parts) < 2:
            await query.edit_message_text("❌ 无效操作")
            return

        sub_action = parts[1]

        if sub_action == "auth":
            # 认证请求
            await self.cloud_auth(update, context)
        elif sub_action == "status":
            # 状态查询
            client = self._get_onedrive_client()
            if not client:
                await query.edit_message_text("❌ OneDrive 未配置")
                return
            is_auth = await client.is_authenticated()
            auto_upload = self._onedrive_config.auto_upload if self._onedrive_config else False
            delete_after = self._onedrive_config.delete_after_upload if self._onedrive_config else False
            remote_path = self._onedrive_config.remote_path if self._onedrive_config else "/aria2bot"
            text = (
                "☁️ *OneDrive 状态*\n\n"
                f"🔐 认证状态: {'✅ 已认证' if is_auth else '❌ 未认证'}\n"
                f"📤 自动上传: {'✅ 开启' if auto_upload else '❌ 关闭'}\n"
                f"🗑️ 上传后删除: {'✅ 开启' if delete_after else '❌ 关闭'}\n"
                f"📁 远程路径: `{remote_path}`"
            )
            keyboard = build_cloud_menu_keyboard()
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        elif sub_action == "settings":
            # 设置页面
            auto_upload = self._onedrive_config.auto_upload if self._onedrive_config else False
            delete_after = self._onedrive_config.delete_after_upload if self._onedrive_config else False
            keyboard = build_cloud_settings_keyboard(auto_upload, delete_after)
            await query.edit_message_text("⚙️ *云存储设置*\n\n点击切换设置：", parse_mode="Markdown", reply_markup=keyboard)
        elif sub_action == "logout":
            # 登出
            client = self._get_onedrive_client()
            if client and await client.logout():
                await query.edit_message_text("✅ 已登出 OneDrive")
            else:
                await query.edit_message_text("❌ 登出失败")
        elif sub_action == "menu":
            # 返回菜单
            keyboard = build_cloud_menu_keyboard()
            await query.edit_message_text("☁️ *云存储管理*", parse_mode="Markdown", reply_markup=keyboard)
        elif sub_action == "toggle":
            # 切换设置（注意：运行时修改配置，重启后会重置）
            if len(parts) < 3:
                return
            setting = parts[2]
            if self._onedrive_config:
                if setting == "auto_upload":
                    self._onedrive_config.auto_upload = not self._onedrive_config.auto_upload
                elif setting == "delete_after":
                    self._onedrive_config.delete_after_upload = not self._onedrive_config.delete_after_upload
            auto_upload = self._onedrive_config.auto_upload if self._onedrive_config else False
            delete_after = self._onedrive_config.delete_after_upload if self._onedrive_config else False
            keyboard = build_cloud_settings_keyboard(auto_upload, delete_after)
            await query.edit_message_text("⚙️ *云存储设置*\n\n点击切换设置：", parse_mode="Markdown", reply_markup=keyboard)

    async def _handle_upload_callback(self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list) -> None:
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

    async def _upload_to_channel_manual(self, query, update: Update, context: ContextTypes.DEFAULT_TYPE, gid: str) -> None:
        """手动上传到频道"""
        import shutil
        from pathlib import Path

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
            if self._telegram_channel_config and self._telegram_channel_config.delete_after_upload:
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


def build_handlers(api: Aria2BotAPI) -> list:
    """构建 Handler 列表"""

    def wrap_with_permission(handler_func):
        """包装处理函数，添加权限检查"""
        @wraps(handler_func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await api._check_permission(update, context):
                return
            return await handler_func(update, context)
        return wrapped

    # 构建按钮文本过滤器
    button_pattern = "^(" + "|".join(BUTTON_COMMANDS.keys()).replace("▶️", "▶️").replace("⏹", "⏹") + ")$"

    return [
        # 服务管理命令
        CommandHandler("install", wrap_with_permission(api.install)),
        CommandHandler("uninstall", wrap_with_permission(api.uninstall)),
        CommandHandler("start", wrap_with_permission(api.start_service)),
        CommandHandler("stop", wrap_with_permission(api.stop_service)),
        CommandHandler("restart", wrap_with_permission(api.restart_service)),
        CommandHandler("status", wrap_with_permission(api.status)),
        CommandHandler("logs", wrap_with_permission(api.view_logs)),
        CommandHandler("clear_logs", wrap_with_permission(api.clear_logs)),
        CommandHandler("set_secret", wrap_with_permission(api.set_secret)),
        CommandHandler("reset_secret", wrap_with_permission(api.reset_secret)),
        CommandHandler("help", wrap_with_permission(api.help_command)),
        CommandHandler("menu", wrap_with_permission(api.menu_command)),
        # 下载管理命令
        CommandHandler("add", wrap_with_permission(api.add_download)),
        CommandHandler("list", wrap_with_permission(api.list_downloads)),
        CommandHandler("stats", wrap_with_permission(api.global_stats)),
        # 云存储命令
        CommandHandler("cloud", wrap_with_permission(api.cloud_command)),
        # Reply Keyboard 按钮文本处理
        MessageHandler(filters.TEXT & filters.Regex(button_pattern), wrap_with_permission(api.handle_button_text)),
        # OneDrive 认证回调 URL 处理
        MessageHandler(filters.TEXT & filters.Regex(r"^https://login\.microsoftonline\.com"), wrap_with_permission(api.handle_auth_callback)),
        # 种子文件处理
        MessageHandler(filters.Document.FileExtension("torrent"), wrap_with_permission(api.handle_torrent)),
        # Callback Query 处理
        CallbackQueryHandler(wrap_with_permission(api.handle_callback)),
    ]
