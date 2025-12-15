"""服务管理命令处理。"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from src.utils.logger import get_logger
from src.core import (
    Aria2Error,
    NotInstalledError,
    ServiceError,
    DownloadError,
    ConfigError,
    is_aria2_installed,
    get_aria2_version,
    generate_rpc_secret,
)
from src.telegram.keyboards import build_main_reply_keyboard

from .base import _get_user_info

logger = get_logger("handlers.service")


class ServiceHandlersMixin:
    """服务管理命令 Mixin"""

    async def install(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info(f"收到 /install 命令 - {_get_user_info(update)}")
        if is_aria2_installed():
            await self._reply(
                update, context, "aria2 已安装，无需重复安装。如需重新安装，请先运行 /uninstall"
            )
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
            await self._reply(
                update,
                context,
                f"RPC 密钥已更新并重启服务 ✅\n新密钥: `{new_secret[:4]}****{new_secret[-4:]}`",
                parse_mode="Markdown",
            )
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
            await self._reply(
                update,
                context,
                f"RPC 密钥已重新生成并重启服务 ✅\n新密钥: `{new_secret[:4]}****{new_secret[-4:]}`",
                parse_mode="Markdown",
            )
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
            update,
            context,
            "📋 *快捷菜单*\n\n使用下方按钮快速操作，或输入命令：\n/add <URL> - 添加下载任务",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
