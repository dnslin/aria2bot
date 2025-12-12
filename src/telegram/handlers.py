"""Telegram bot command handlers."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from src.utils.logger import get_logger

from src.core import (
    Aria2Config,
    Aria2Error,
    NotInstalledError,
    ServiceError,
    DownloadError,
    ConfigError,
    is_aria2_installed,
    get_aria2_version,
    ARIA2_CONF,
)
from src.aria2 import Aria2Installer, Aria2ServiceManager

logger = get_logger("handlers")


def _get_user_info(update: Update) -> str:
    """获取用户信息用于日志"""
    user = update.effective_user
    if user:
        return f"用户ID={user.id}, 用户名={user.username or 'N/A'}"
    return "未知用户"


class Aria2BotAPI:
    def __init__(self, config: Aria2Config | None = None):
        self.config = config or Aria2Config()
        self.installer = Aria2Installer(self.config)
        self.service = Aria2ServiceManager()

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

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /help 命令 - {_get_user_info(update)}")
        commands = [
            "/install - 安装 aria2",
            "/uninstall - 卸载 aria2",
            "/start - 启动 aria2 服务",
            "/stop - 停止 aria2 服务",
            "/restart - 重启 aria2 服务",
            "/status - 查看 aria2 状态",
            "/logs - 查看最近日志",
            "/clear_logs - 清空日志",
            "/help - 显示此帮助",
        ]
        await self._reply(update, context, "可用命令：\n" + "\n".join(commands))


def build_handlers(api: Aria2BotAPI) -> list[CommandHandler]:
    """构建 CommandHandler 列表"""
    return [
        CommandHandler("install", api.install),
        CommandHandler("uninstall", api.uninstall),
        CommandHandler("start", api.start_service),
        CommandHandler("stop", api.stop_service),
        CommandHandler("restart", api.restart_service),
        CommandHandler("status", api.status),
        CommandHandler("logs", api.view_logs),
        CommandHandler("clear_logs", api.clear_logs),
        CommandHandler("help", api.help_command),
    ]
