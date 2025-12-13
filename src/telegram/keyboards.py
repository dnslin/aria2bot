"""Telegram 键盘构建工具"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

# 状态 emoji 映射
STATUS_EMOJI = {
    "active": "⬇️",
    "waiting": "⏳",
    "paused": "⏸️",
    "complete": "✅",
    "error": "❌",
    "removed": "🗑️",
}


def build_list_type_keyboard(active_count: int, waiting_count: int, stopped_count: int) -> InlineKeyboardMarkup:
    """构建列表类型选择键盘"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"▶️ 活动 ({active_count})", callback_data="list:active:1"),
            InlineKeyboardButton(f"⏳ 等待 ({waiting_count})", callback_data="list:waiting:1"),
        ],
        [
            InlineKeyboardButton(f"✅ 已完成 ({stopped_count})", callback_data="list:stopped:1"),
            InlineKeyboardButton("📊 统计", callback_data="stats"),
        ],
    ])


def build_task_keyboard(gid: str, status: str) -> InlineKeyboardMarkup:
    """构建单个任务的操作按钮"""
    buttons = []

    if status == "active":
        buttons.append(InlineKeyboardButton("⏸ 暂停", callback_data=f"pause:{gid}"))
    elif status in ("paused", "waiting"):
        buttons.append(InlineKeyboardButton("▶️ 恢复", callback_data=f"resume:{gid}"))

    buttons.append(InlineKeyboardButton("🗑 删除", callback_data=f"delete:{gid}"))
    buttons.append(InlineKeyboardButton("📋 详情", callback_data=f"detail:{gid}"))

    return InlineKeyboardMarkup([buttons])


def build_task_list_keyboard(page: int, total_pages: int, list_type: str) -> InlineKeyboardMarkup | None:
    """构建任务列表的翻页按钮"""
    nav_buttons = []

    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"list:{list_type}:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"list:{list_type}:{page + 1}"))

    # 返回按钮
    back_button = [InlineKeyboardButton("🔙 返回列表", callback_data="list:menu")]

    rows = []
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(back_button)

    return InlineKeyboardMarkup(rows)


def build_delete_confirm_keyboard(gid: str) -> InlineKeyboardMarkup:
    """构建删除确认按钮（含是否删除文件选项）"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 仅删任务", callback_data=f"confirm_del:{gid}:0"),
            InlineKeyboardButton("🗑 删任务+文件", callback_data=f"confirm_del:{gid}:1"),
        ],
        [
            InlineKeyboardButton("❌ 取消", callback_data="cancel"),
        ],
    ])


def build_detail_keyboard(gid: str, status: str) -> InlineKeyboardMarkup:
    """构建详情页面的操作按钮"""
    buttons = []

    if status == "active":
        buttons.append(InlineKeyboardButton("⏸ 暂停", callback_data=f"pause:{gid}"))
    elif status in ("paused", "waiting"):
        buttons.append(InlineKeyboardButton("▶️ 恢复", callback_data=f"resume:{gid}"))

    buttons.append(InlineKeyboardButton("🗑 删除", callback_data=f"delete:{gid}"))

    return InlineKeyboardMarkup([
        buttons,
        [
            InlineKeyboardButton("🔄 刷新", callback_data=f"refresh:{gid}"),
            InlineKeyboardButton("🔙 返回列表", callback_data="list:menu"),
        ],
    ])


def build_after_add_keyboard(gid: str) -> InlineKeyboardMarkup:
    """构建添加任务后的操作按钮"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 查看详情", callback_data=f"detail:{gid}"),
            InlineKeyboardButton("📥 查看列表", callback_data="list:menu"),
        ],
    ])


def build_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """构建主菜单 Reply Keyboard"""
    keyboard = [
        [KeyboardButton("📥 下载列表"), KeyboardButton("📊 统计")],
        [KeyboardButton("▶️ 启动"), KeyboardButton("⏹ 停止")],
        [KeyboardButton("🔄 重启"), KeyboardButton("📋 状态")],
        [KeyboardButton("📜 日志"), KeyboardButton("❓ 帮助")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


# ==================== 云存储相关键盘 ====================


def build_cloud_menu_keyboard() -> InlineKeyboardMarkup:
    """构建云存储管理菜单"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 OneDrive 认证", callback_data="cloud:auth:onedrive")],
        [
            InlineKeyboardButton("📊 状态", callback_data="cloud:status"),
            InlineKeyboardButton("⚙️ 设置", callback_data="cloud:settings"),
        ],
        [InlineKeyboardButton("🚪 登出", callback_data="cloud:logout")],
    ])


def build_upload_choice_keyboard(gid: str) -> InlineKeyboardMarkup:
    """构建下载完成后的上传选择键盘"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☁️ 上传到 OneDrive", callback_data=f"upload:onedrive:{gid}")],
        [InlineKeyboardButton("🔙 返回列表", callback_data="list:menu")],
    ])


def build_cloud_settings_keyboard(auto_upload: bool, delete_after: bool) -> InlineKeyboardMarkup:
    """构建云存储设置键盘"""
    auto_text = "✅ 自动上传" if auto_upload else "❌ 自动上传"
    delete_text = "✅ 上传后删除" if delete_after else "❌ 上传后删除"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(auto_text, callback_data="cloud:toggle:auto_upload")],
        [InlineKeyboardButton(delete_text, callback_data="cloud:toggle:delete_after")],
        [InlineKeyboardButton("🔙 返回", callback_data="cloud:menu")],
    ])


def build_detail_keyboard_with_upload(gid: str, status: str, show_onedrive: bool = False, show_channel: bool = False) -> InlineKeyboardMarkup:
    """构建详情页面的操作按钮（含上传选项）"""
    buttons = []

    if status == "active":
        buttons.append(InlineKeyboardButton("⏸ 暂停", callback_data=f"pause:{gid}"))
    elif status in ("paused", "waiting"):
        buttons.append(InlineKeyboardButton("▶️ 恢复", callback_data=f"resume:{gid}"))

    buttons.append(InlineKeyboardButton("🗑 删除", callback_data=f"delete:{gid}"))

    rows = [buttons]

    # 任务完成时显示上传按钮
    if status == "complete":
        upload_buttons = []
        if show_onedrive:
            upload_buttons.append(InlineKeyboardButton("☁️ OneDrive", callback_data=f"upload:onedrive:{gid}"))
        if show_channel:
            upload_buttons.append(InlineKeyboardButton("📢 频道", callback_data=f"upload:telegram:{gid}"))
        if upload_buttons:
            rows.append(upload_buttons)

    rows.append([
        InlineKeyboardButton("🔄 刷新", callback_data=f"refresh:{gid}"),
        InlineKeyboardButton("🔙 返回列表", callback_data="list:menu"),
    ])

    return InlineKeyboardMarkup(rows)
