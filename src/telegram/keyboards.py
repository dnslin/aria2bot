"""Telegram 键盘构建工具"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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
