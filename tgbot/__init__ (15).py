import logging
import asyncio
import os
import random
import json
import zipfile
import io
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, Message, LabeledPrice, PreCheckoutQuery,
    BufferedInputFile, InputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.future import select
from sqlalchemy import update, delete, func, and_, or_

from ..core.config import settings
from ..core.db import AsyncSessionLocal, get_db
from ..core.utils import format_money, format_date, format_status, resolve_template, translate_tg_error
from ..models.all_models import (
    User, SystemSetting, UserKeyword, UserFilter, 
    AutoReply, ProtocolAccount, RechargeOrder, Plan,
    ListenedUser, UserTargetChat, ProtocolProfileSetting, InboxAutoReply, PrivateChatLog,
    LeadSoftware, BotEvent
)

async def log_event(event_type: str, user: "User | None" = None, detail: str = "", telegram_id: int = None):
    try:
        async with AsyncSessionLocal() as db:
            ev = BotEvent(
                event_type=event_type,
                user_id=user.id if user else None,
                telegram_id=(user.telegram_id if user else telegram_id),
                username=(user.username or "") if user else "",
                nickname=(user.nickname or "") if user else "",
                detail=detail,
            )
            db.add(ev)
            await db.commit()
    except Exception:
        pass

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FSM States
class AddKeywordStates(StatesGroup):
    waiting_for_keyword = State()
    waiting_for_match_type = State()

class AddFilterStates(StatesGroup):
    waiting_for_filter = State()

class InboxReplyStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_image = State()

class ScriptStates(StatesGroup):
    waiting_for_script = State()

class BulkProfileStates(StatesGroup):
    waiting_for_zip = State()

class AddProtocolStates(StatesGroup):
    waiting_for_session = State()

class ImportTargetChatStates(StatesGroup):
    waiting_for_file = State()

# Router
router = Router()

# Helper Functions
async def get_system_setting(key: str) -> Optional[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
        setting = result.scalar_one_or_none()
        return setting.setting_value if setting else None

async def ensure_user(tg_user: types.User) -> User:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_user.id))
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(
                telegram_id=tg_user.id,
                username=tg_user.username or "",
                nickname=tg_user.full_name or "",
                status=1,
                listen_status=0,
                plan_name="体验会员",
                plan_keyword_limit=10,
                expire_at=datetime.now() + timedelta(hours=4)
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user

def get_main_menu_kb(user: User):
    builder = InlineKeyboardBuilder()
    
    # 1. Member recharge / history
    builder.row(
        InlineKeyboardButton(text="💰 会员充值", callback_data="menu_recharge"), 
        InlineKeyboardButton(text="📋 查看充值记录", callback_data="menu_recharge_history")
    )
    
    # 2. Listen Status
    builder.row(
        InlineKeyboardButton(text="✅ 开启监听" if user.listen_status else "📡 开启监听", callback_data="menu_listen_on"), 
        InlineKeyboardButton(text="🔴 关闭监听", callback_data="menu_listen_off")
    )
    
    # 3. Private Status
    builder.row(
        InlineKeyboardButton(text="💬 开启私聊" if user.private_status else "💬 开启私聊", callback_data="menu_private_on"), 
        InlineKeyboardButton(text="🔴 关闭私聊", callback_data="menu_private_off")
    )
    
    # 4. Keywords
    builder.row(
        InlineKeyboardButton(text="➕ 添加监听关键词", callback_data="menu_add_keyword"), 
        InlineKeyboardButton(text="🔍 查看监听关键词", callback_data="menu_view_keyword")
    )
    
    # 5. Filters
    builder.row(
        InlineKeyboardButton(text="🚫 添加过滤关键词", callback_data="menu_add_filter"), 
        InlineKeyboardButton(text="👁️ 查看过滤关键词", callback_data="menu_view_filter")
    )
    
    # 6. Protocol Accounts
    builder.row(
        InlineKeyboardButton(text="👤 添加私聊账号", callback_data="menu_add_protocol"), 
        InlineKeyboardButton(text="⚙️ 管理私聊账号", callback_data="menu_view_protocol"),
        InlineKeyboardButton(text="📜 私聊日志", callback_data="menu_chat_logs")
    )
    
    # 7. Bulk Profile / Auto Reply
    builder.row(
        InlineKeyboardButton(text="🖼️ 批量改协议号资料", callback_data="menu_batch_profile"),
        InlineKeyboardButton(text="📩 收到消息自动回复", callback_data="menu_inbox_reply")
    )
    
    # 8. Script Management
    builder.row(
        InlineKeyboardButton(text="📝 设置话术", callback_data="menu_set_script"),
        InlineKeyboardButton(text="🗑️ 清除话术", callback_data="menu_clear_script")
    )
    
    # 9. Privacy Filter
    builder.row(
        InlineKeyboardButton(text="🔒 点击开启过滤不能私聊的用户" if not user.privacy_filter_status else "🔓 点击关闭过滤不能私聊的用户", 
                             callback_data="menu_toggle_privacy_filter")
    )
    
    # 10. Listened Data
    builder.row(InlineKeyboardButton(text="📊 监听用户数据", callback_data="menu_listened_users"))
    
    # 11. Import Target Groups
    builder.row(InlineKeyboardButton(text="📥 批量导入目标群", callback_data="menu_add_target_chat"))
    
    # 12. Service / Others
    builder.row(
        InlineKeyboardButton(text="🎧 联系客服", url="https://t.me/zlhp8_bot"), # Replace with actual service link
        InlineKeyboardButton(text="🧳 其他引流软件", callback_data="menu_others")
    )
    
    # 13. Home
    builder.row(InlineKeyboardButton(text="🏠 【返回主菜单】", callback_data="menu_home"))
    
    return builder.as_markup()

@router.message(Command("start"))
async def cmd_start(message: Message):
    user = await ensure_user(message.from_user)
    await log_event("user_start", user, detail="打开机器人")
    text = (
        f"🤖 欢迎使用TG 监听系统商业版监听机器人！\n\n"
        f"👤 当前身份：正式会员\n"
        f"📅 有效期至：{format_date(user.expire_at)}\n"
        f"⏳ 剩余天数：{((user.expire_at - datetime.now()).days if user.expire_at else 0)} 天\n"
        f"💡 请输入 /push 绑定推送对话框"
    )
    kb = get_main_menu_kb(user)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(Command("push"))
async def cmd_push(message: Message):
    user = await ensure_user(message.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User).where(User.id == user.id).values(push_chat_id=message.chat.id)
        )
        await session.commit()
    await message.answer(f"✅ 推送对话框绑定成功！\nID: <code>{message.chat.id}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("menu_view_protocol"))
async def cb_view_protocol(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    
    # Parse pagination
    page = 1
    if ":" in callback.data:
        page = int(callback.data.split(":")[1])
    
    PAGE_SIZE = 5 # As seen in the screenshot, only about 5-10 accounts per page
    async with AsyncSessionLocal() as session:
        # Total count for pagination
        count_res = await session.execute(
            select(func.count(ProtocolAccount.id)).where(ProtocolAccount.user_id == user.id)
        )
        total_count = count_res.scalar()
        total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE if total_count > 0 else 1
        
        # Get accounts for current page
        res = await session.execute(
            select(ProtocolAccount)
            .where(ProtocolAccount.user_id == user.id)
            .order_by(ProtocolAccount.created_at.asc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        accs = res.scalars().all()
        
    text = f"⚙️ <b>私聊账号管理</b>\n\n当前已设置 {total_count} 个私聊小号：\n"
    for i, acc in enumerate(accs, (page - 1) * PAGE_SIZE + 1):
        username_link = f'<a href="https://t.me/{acc.username}">@{acc.username}</a>' if acc.username else "无用户名"
        status_icon = "✅" if acc.status == 1 else "⚠️"
        text += f"{i}. 📱 <code>{acc.phone}</code> | {username_link} | 回复{acc.inbound_count}人 | {status_icon} {acc.status_label}\n"
        
    builder = InlineKeyboardBuilder()
    
    # Pagination buttons
    nav_btns = []
    if page > 1:
        nav_btns.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"menu_view_protocol:{page-1}"))
    if page < total_pages:
        nav_btns.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"menu_view_protocol:{page+1}"))
    if nav_btns:
        builder.row(*nav_btns)
        
    # Action buttons from screenshot
    builder.row(
        InlineKeyboardButton(text="🗑️ 清除所有账号", callback_data="protocol_clear_all"),
        InlineKeyboardButton(text="🗑️ 清除异常账号", callback_data="protocol_clear_abnormal")
    )
    builder.row(InlineKeyboardButton(text="🔍 检测账号状态", callback_data="menu_check_protocol"))
    builder.row(InlineKeyboardButton(text="🏠 【返回主菜单】", callback_data="menu_home"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "protocol_clear_all")
async def cb_protocol_clear_all(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(ProtocolAccount).where(ProtocolAccount.user_id == user.id))
        await session.commit()
    await callback.answer("✅ 所有账号已清除")
    await cb_view_protocol(callback)

@router.callback_query(F.data == "protocol_clear_abnormal")
async def cb_protocol_clear_abnormal(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(ProtocolAccount)
            .where(ProtocolAccount.user_id == user.id, ProtocolAccount.status == 2)
        )
        await session.commit()
    await callback.answer("✅ 异常账号已清除")
    await cb_view_protocol(callback)

from telethon import TelegramClient
from telethon.sessions import StringSession
from ..core.utils import parse_proxy_url as _parse_proxy

@router.callback_query(F.data == "menu_check_protocol")
async def cb_check_protocol(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    await callback.answer("⏳ 正在检测账号状态，请稍后...", show_alert=False)
    
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(ProtocolAccount).where(ProtocolAccount.user_id == user.id))
        accs = res.scalars().all()
    
    if not accs:
        await callback.message.answer("❌ 您还没有添加任何私聊小号。")
        return

    status_msg = await callback.message.answer(f"🔍 正在检测 {len(accs)} 个账号状态...")
    
    results = []
    normal_count = 0
    abnormal_count = 0
    
    for acc in accs:
        is_normal = False
        error_msg = ""
        try:
            proxy = _parse_proxy(acc.proxy_url or "")
            client = TelegramClient(
                StringSession(acc.session_data), 12345, "hash",
                connection_retries=1, timeout=10,
                proxy=proxy
            )
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                acc.username = me.username or ""
                acc.status_label = "正常"
                acc.status = 1 # Ensure status is 1 for active accounts
                is_normal = True
                normal_count += 1
            else:
                acc.status_label = "失效"
                acc.status = 2
                abnormal_count += 1
            await client.disconnect()
        except Exception as e:
            acc.status_label = "异常"
            acc.status = 2
            error_msg = translate_tg_error(str(e)) # Use localized translation
            abnormal_count += 1
            
        # Update database status
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ProtocolAccount)
                .where(ProtocolAccount.id == acc.id)
                .values(status_label=acc.status_label, username=acc.username, status=acc.status)
            )
            await session.commit()
            
        # Format line with stats as requested by customer
        username_display = f'<a href="https://t.me/{acc.username}">@{acc.username}</a>' if acc.username else "无用户名"
        status_icon = "✅ 正常" if is_normal else f"⚠️ {acc.status_label} ({error_msg[:20]})"
        # ADDED: Outbound and Inbound stats here
        results.append(f"• <code>{acc.phone}</code> | {username_display} | <b>私聊{acc.outbound_count}</b> | <b>回复{acc.inbound_count}</b> : {status_icon}")

    final_text = (
        f"🔍 <b>协议号状态检测 — 完成</b>\n\n"
        f"✅ <b>正常:</b> {normal_count} 个\n"
        f"⚠️ <b>异常:</b> {abnormal_count} 个\n\n"
        f"<b>详细结果:</b>\n" + "\n".join(results)
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔍 重新检测", callback_data="menu_check_protocol"))
    builder.row(InlineKeyboardButton(text="⚙️ 返回账号管理", callback_data="menu_view_protocol"),
               InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    
    await status_msg.edit_text(final_text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "menu_home")
async def cb_home(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    text = (
        f"🤖 欢迎使用TG 监听系统商业版监听机器人！\n\n"
        f"👤 当前身份：正式会员\n"
        f"📅 有效期至：{format_date(user.expire_at)}\n"
        f"⏳ 剩余天数：{((user.expire_at - datetime.now()).days if user.expire_at else 0)} 天\n"
        f"💡 请输入 /push 绑定推送对话框"
    )
    kb = get_main_menu_kb(user)
    try: await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except: await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

# --- Keyword Handlers ---

@router.callback_query(F.data == "menu_add_keyword")
async def cb_add_keyword(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>添加监听关键词</b>\n\n"
        "请发送您要监听的关键词（每行一个，可同时添加多个）：",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ 取消", callback_data="menu_home")
        ).as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AddKeywordStates.waiting_for_keyword)

@router.message(AddKeywordStates.waiting_for_keyword)
async def handle_add_keyword(message: Message, state: FSMContext):
    keywords = [k.strip() for k in message.text.split("\n") if k.strip()]
    if not keywords:
        await message.answer("⚠️ 请输入有效关键词")
        return
    # Save keywords to state, then ask match type
    await state.update_data(pending_keywords=keywords)
    await state.set_state(AddKeywordStates.waiting_for_match_type)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎯 精准匹配", callback_data="kw_match_exact"),
        InlineKeyboardButton(text="🔍 模糊匹配", callback_data="kw_match_fuzzy"),
    )
    builder.row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_home"))
    preview = "、".join(keywords[:3]) + ("..." if len(keywords) > 3 else "")
    await message.answer(
        f"📝 关键词：<b>{preview}</b>\n\n"
        f"请选择匹配方式：\n\n"
        f"🎯 <b>精准匹配</b> — 消息内容与关键词完全一致才触发\n"
        f"🔍 <b>模糊匹配</b> — 消息中包含关键词即触发",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.in_({"kw_match_exact", "kw_match_fuzzy"}))
async def handle_match_type_choice(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    keywords = data.get("pending_keywords", [])
    match_type = "exact" if callback.data == "kw_match_exact" else "contains"
    match_label = "精准匹配" if match_type == "exact" else "模糊匹配"

    if not keywords:
        await callback.answer("❌ 未找到待添加关键词", show_alert=True)
        await state.clear()
        return

    user = await ensure_user(callback.from_user)
    added = 0
    async with AsyncSessionLocal() as session:
        # Check keyword limit
        count_res = await session.execute(
            select(func.count()).select_from(UserKeyword).where(UserKeyword.user_id == user.id)
        )
        current_count = count_res.scalar() or 0
        limit = user.plan_keyword_limit if hasattr(user, 'plan_keyword_limit') and user.plan_keyword_limit else 20
        for kw in keywords:
            if current_count >= limit:
                break
            res = await session.execute(
                select(UserKeyword).where(UserKeyword.user_id == user.id, UserKeyword.keyword == kw)
            )
            if not res.scalar_one_or_none():
                session.add(UserKeyword(user_id=user.id, keyword=kw, match_type=match_type, status=1))
                added += 1
                current_count += 1
        await session.commit()

    await state.clear()
    await log_event("keyword_add", user, detail=f"添加关键词({match_label}): {', '.join(keywords[:5])}")
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ 继续添加", callback_data="menu_add_keyword"))
    builder.row(InlineKeyboardButton(text="🔍 查看关键词", callback_data="menu_view_keyword"))
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(
        f"✅ 成功添加 <b>{added}</b> 个关键词\n"
        f"匹配方式：{'🎯 精准匹配' if match_type == 'exact' else '🔍 模糊匹配'}",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_view_keyword")
async def cb_view_keyword(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(UserKeyword).where(UserKeyword.user_id == user.id))
        keywords = res.scalars().all()

    text = "🔍 <b>当前监听关键词：</b>\n\n"
    if not keywords:
        text += "暂无关键词"
    else:
        for i, kw in enumerate(keywords, 1):
            if kw.match_type == "exact":
                tag = "🎯精准"
            else:
                tag = "🔍模糊"
            text += f"{i}. {kw.keyword}  <code>[{tag}]</code>\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🗑️ 清空所有关键词", callback_data="keyword_clear_all"))
    builder.row(InlineKeyboardButton(text="🏠 【返回主菜单】", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "keyword_clear_all")
async def cb_keyword_clear(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(UserKeyword).where(UserKeyword.user_id == user.id))
        await session.commit()
    await callback.answer("✅ 关键词已清空")
    await cb_view_keyword(callback)

# --- Filter Handlers ---

@router.callback_query(F.data == "menu_add_filter")
async def cb_add_filter(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🚫 <b>添加过滤关键词</b>\n\n"
        "包含此类词的消息将被忽略（每行一个）。",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_home")).as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(AddFilterStates.waiting_for_filter)

@router.message(AddFilterStates.waiting_for_filter)
async def handle_add_filter(message: Message, state: FSMContext):
    user = await ensure_user(message.from_user)
    filters = [f.strip() for f in message.text.split("\n") if f.strip()]
    
    async with AsyncSessionLocal() as session:
        for fl in filters:
            res = await session.execute(select(UserFilter).where(UserFilter.user_id == user.id, UserFilter.keyword == fl))
            if not res.scalar_one_or_none():
                session.add(UserFilter(user_id=user.id, keyword=fl))
        await session.commit()
    
    await message.answer(f"✅ 成功添加 {len(filters)} 个过滤词！")
    await state.clear()
    await cmd_start(message)

@router.callback_query(F.data == "menu_view_filter")
async def cb_view_filter(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(UserFilter).where(UserFilter.user_id == user.id))
        filters = res.scalars().all()
    
    text = "👁️ <b>当前过滤关键词：</b>\n\n"
    if not filters:
        text += "暂无过滤词"
    else:
        for i, fl in enumerate(filters, 1):
            text += f"{i}. {fl.keyword}\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🗑️ 清空所有过滤词", callback_data="filter_clear_all"))
    builder.row(InlineKeyboardButton(text="🏠 【返回主菜单】", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "filter_clear_all")
async def cb_filter_clear(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(UserFilter).where(UserFilter.user_id == user.id))
        await session.commit()
    await callback.answer("✅ 过滤词已清空")
    await cb_view_filter(callback)

# --- Auto Reply Handlers ---

@router.callback_query(F.data == "menu_inbox_reply")
async def cb_inbox_reply(callback: CallbackQuery, state: FSMContext):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(InboxAutoReply).where(InboxAutoReply.user_id == user.id))
        iar = res.scalar_one_or_none()
        if not iar:
            iar = InboxAutoReply(user_id=user.id)
            session.add(iar)
            await session.commit()
            await session.refresh(iar)
            
    text = (
        "📩 <b>收到消息自动回复设置</b>\n\n"
        f"当前状态：{'🟢 已开启' if iar.is_enabled else '🔴 已关闭'}\n"
        f"内容类型：{iar.reply_type}\n"
        f"文字内容：{iar.reply_content[:50] or '未设置'}\n"
        f"按钮文字：{iar.button_text or '未设置'}\n\n"
        "请选择要修改的项："
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ 开启" if not iar.is_enabled else "🔴 关闭", callback_data="inbox_toggle"),
        InlineKeyboardButton(text="📝 修改文字", callback_data="inbox_set_text")
    )
    builder.row(
        InlineKeyboardButton(text="🖼️ 上传图片", callback_data="inbox_set_image"),
        InlineKeyboardButton(text="🔗 修改按钮", callback_data="inbox_set_button")
    )
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "inbox_toggle")
async def cb_inbox_toggle(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(InboxAutoReply).where(InboxAutoReply.user_id == user.id))
        iar = res.scalar_one()
        iar.is_enabled = 1 if not iar.is_enabled else 0
        await session.commit()
    await callback.answer("✅ 状态已更新")
    await cb_inbox_reply(callback, None)

@router.callback_query(F.data == "inbox_set_text")
async def cb_inbox_text(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("💡 请发送自动回复的文字内容：", 
                                     reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_inbox_reply")).as_markup())
    await state.set_state(InboxReplyStates.waiting_for_content)

@router.message(InboxReplyStates.waiting_for_content)
async def handle_inbox_text(message: Message, state: FSMContext):
    user = await ensure_user(message.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(update(InboxAutoReply).where(InboxAutoReply.user_id == user.id).values(reply_content=message.text, reply_type="text"))
        await session.commit()
    await message.answer("✅ 自动回复文字已保存！")
    await state.clear()
    await cmd_start(message)

@router.callback_query(F.data == "inbox_set_image")
async def cb_inbox_image(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("💡 请发送一张图片作为自动回复：", 
                                     reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_inbox_reply")).as_markup())
    await state.set_state(InboxReplyStates.waiting_for_image)

@router.message(InboxReplyStates.waiting_for_image, F.photo)
async def handle_inbox_image(message: Message, state: FSMContext):
    user = await ensure_user(message.from_user)
    photo = message.photo[-1]
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(InboxAutoReply)
            .where(InboxAutoReply.user_id == user.id)
            .values(image_file_id=photo.file_id, reply_type="image")
        )
        await session.commit()
    await message.answer("✅ 自动回复图片已保存！")
    await state.clear()
    await cmd_start(message)

# --- Script Handlers ---

@router.callback_query(F.data == "menu_set_script")
async def cb_set_script(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 <b>设置私聊话术</b>\n\n"
        "请输入您想要在私聊时发送的话术内容。\n"
        "支持变量：\n"
        "• <code>{随机数字}</code> - 7位随机数\n"
        "• <code>{随机表情}</code> - 随机 Emoji\n"
        "• <code>@username</code> - 自动转为可点击链接",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_home")).as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(ScriptStates.waiting_for_script)

@router.message(ScriptStates.waiting_for_script)
async def handle_set_script(message: Message, state: FSMContext):
    user = await ensure_user(message.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(AutoReply).where(AutoReply.user_id == user.id))
        ar = res.scalar_one_or_none()
        if not ar:
            ar = AutoReply(user_id=user.id, keyword="DEFAULT")
            session.add(ar)
        ar.reply_content = message.text
        ar.reply_type = "text"
        await session.commit()
    await message.answer(f"✅ 话术设置成功！\n\n内容：\n{message.text}")
    await state.clear()
    await cmd_start(message)

@router.callback_query(F.data == "menu_clear_script")
async def cb_clear_script(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AutoReply).where(AutoReply.user_id == user.id))
        await session.commit()
    await callback.answer("✅ 话术已清除")
    await cb_home(callback)

@router.callback_query(F.data == "menu_batch_profile")
async def cb_batch_profile(callback: CallbackQuery, state: FSMContext):
    text = (
        "🖼️ <b>批量修改协议号资料</b>\n\n"
        "第一步：请上传一个包含头像图片的 <code>.zip</code> 压缩包。\n"
        "系统将随机为您的账号分配头像。"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(BulkProfileStates.waiting_for_zip)

@router.message(BulkProfileStates.waiting_for_zip, F.document)
async def handle_profile_zip(message: Message, state: FSMContext, bot: Bot):
    if not message.document.file_name.endswith(".zip"):
        await message.answer("❌ 请上传 <code>.zip</code> 文件。")
        return
    
    user = await ensure_user(message.from_user)
    
    # Save zip file_id to database for background processing
    async with AsyncSessionLocal() as session:
        # Check if settings exist
        res = await session.execute(select(ProtocolProfileSetting).where(ProtocolProfileSetting.user_id == user.id))
        setting = res.scalar_one_or_none()
        if not setting:
            setting = ProtocolProfileSetting(user_id=user.id)
            session.add(setting)
        
        setting.photo_zip_file_id = message.document.file_id
        setting.apply_requested = 1
        await session.commit()

    await message.answer("⏳ <b>已收到头像压缩包！</b>\n\n系统已进入排队处理状态，将自动解压并随机为您的协议号更新头像。完成后您可以在管理页面看到变化。")
    await state.clear()
    await cmd_start(message)

# --- Other Essential Handlers ---

@router.callback_query(F.data == "menu_recharge")
async def cb_recharge(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Plan).where(Plan.status == 1))
        plans = res.scalars().all()
    
    text = "💰 <b>会员充值中心</b>\n\n请选择您要购买的套餐："
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.row(InlineKeyboardButton(text=f"{plan.name} - {plan.price} USDT", callback_data=f"buy_plan:{plan.id}"))
    
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data.startswith("buy_plan:"))
async def cb_buy_plan(callback: CallbackQuery):
    plan_id = int(callback.data.split(":")[1])
    user = await ensure_user(callback.from_user)
    
    async with AsyncSessionLocal() as session:
        plan = await session.get(Plan, plan_id)
        if not plan: return
        
        # Check for system setting for USDT address
        res_addr = await session.execute(select(SystemSetting).where(SystemSetting.setting_key == "usdt_trc20_address"))
        addr = res_addr.scalar_one_or_none()
        usdt_address = addr.setting_value if addr else "未设置"
        
        # Generate unique order amount (small random decimals for identification)
        unique_amount = float(plan.price) + (random.randint(1, 999) / 10000.0)
        order_no = f"ORD-{uuid.uuid4().hex[:12].upper()}"
        
        new_order = RechargeOrder(
            user_id=user.id,
            order_no=order_no,
            plan_id=plan.id,
            plan_name=plan.name,
            amount=unique_amount,
            status="pending",
            pay_address=usdt_address,
            expire_at=datetime.now() + timedelta(minutes=15)
        )
        session.add(new_order)
        await session.commit()
    await log_event("order_created", user, detail=f"充值订单: {order_no} | {plan.name} | {unique_amount} USDT")
        
    text = (
        f"📋 <b>订单已生成</b>\n\n"
        f"订单编号：<code>{order_no}</code>\n"
        f"所选套餐：{plan.name}\n"
        f"需支付金额：<code>{unique_amount}</code> USDT (TRC20)\n\n"
        f"⚠️ <b>请务必支付精确金额（包括小数部分），否则系统无法自动识别。</b>\n\n"
        f"收款地址：\n<code>{usdt_address}</code>\n\n"
        f"支付后请等待约 5-10 分钟，系统确认后会自动激活。"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "menu_add_protocol")
async def cb_add_protocol(callback: CallbackQuery, state: FSMContext):
    text = (
        "👤 <b>添加私聊协议号</b>\n\n"
        "请上传 <code>.session</code> 文件或包含多个 session 文件的 <code>.zip</code> 压缩包。\n"
        "这些账号将用于执行自动私聊和回复任务。"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(AddProtocolStates.waiting_for_session)

@router.message(AddProtocolStates.waiting_for_session, F.document)
async def handle_add_protocol(message: Message, state: FSMContext, bot: Bot):
    user = await ensure_user(message.from_user)
    
    file_id = message.document.file_id
    file_name = message.document.file_name
    
    if not (file_name.endswith(".session") or file_name.endswith(".zip")):
        await message.answer("❌ 只支持 <code>.session</code> 或 <code>.zip</code> 文件。")
        return

    status_msg = await message.answer("⏳ 正在处理账号，请稍候...")
    
    file = await bot.get_file(file_id)
    content = await bot.download_file(file.file_path)
    
    imported = 0
    async with AsyncSessionLocal() as session:
        if file_name.endswith(".session"):
            session_data = content.read().decode('utf-8', errors='ignore')
            # Try to extract phone from filename if it looks like one
            phone = file_name.replace(".session", "")
            session.add(ProtocolAccount(user_id=user.id, session_data=session_data, phone=phone))
            imported = 1
        else:
            with zipfile.ZipFile(io.BytesIO(content.read())) as z:
                for name in z.namelist():
                    if name.endswith(".session"):
                        with z.open(name) as f:
                            session_data = f.read().decode('utf-8', errors='ignore')
                            phone = name.split("/")[-1].replace(".session", "")
                            session.add(ProtocolAccount(user_id=user.id, session_data=session_data, phone=phone))
                            imported += 1
        await session.commit()

    await status_msg.edit_text(f"✅ 成功导入 {imported} 个账号！")
    await state.clear()
    await cmd_start(message)

# --- Listen / Private Status Handlers ---

@router.callback_query(F.data == "menu_listen_on")
async def cb_listen_on(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(update(User).where(User.id == user.id).values(listen_status=1))
        await session.commit()
    await log_event("listen_on", user, detail="开启监听")
    await callback.answer("✅ 已开启监听")
    await cb_home(callback)

@router.callback_query(F.data == "menu_listen_off")
async def cb_listen_off(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(update(User).where(User.id == user.id).values(listen_status=0))
        await session.commit()
    await log_event("listen_off", user, detail="关闭监听")
    await callback.answer("🔴 已关闭监听")
    await cb_home(callback)

@router.callback_query(F.data == "menu_private_on")
async def cb_private_on(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(update(User).where(User.id == user.id).values(private_status=1))
        await session.commit()
    await log_event("private_on", user, detail="开启私聊")
    await callback.answer("✅ 已开启私聊功能")
    await cb_home(callback)

@router.callback_query(F.data == "menu_private_off")
async def cb_private_off(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        await session.execute(update(User).where(User.id == user.id).values(private_status=0))
        await session.commit()
    await log_event("private_off", user, detail="关闭私聊")
    await callback.answer("🔴 已关闭私聊功能")
    await cb_home(callback)

@router.callback_query(F.data == "menu_toggle_privacy_filter")
async def cb_toggle_privacy_filter(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    new_val = 0 if user.privacy_filter_status else 1
    async with AsyncSessionLocal() as session:
        await session.execute(update(User).where(User.id == user.id).values(privacy_filter_status=new_val))
        await session.commit()
    msg = "✅ 已开启私聊过滤（自动跳过无法私聊的用户）" if new_val else "🔓 已关闭私聊过滤"
    await callback.answer(msg, show_alert=True)
    await cb_home(callback)

# --- Recharge History ---

@router.callback_query(F.data == "menu_recharge_history")
async def cb_recharge_history(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(RechargeOrder)
            .where(RechargeOrder.user_id == user.id)
            .order_by(RechargeOrder.id.desc())
            .limit(10)
        )
        orders = res.scalars().all()

    text = "📋 <b>充值记录（最近10条）</b>\n\n"
    if not orders:
        text += "暂无充值记录"
    else:
        for o in orders:
            status_icon = {"paid": "✅", "pending": "⏳", "expired": "❌"}.get(o.status, "❓")
            created = o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else "-"
            text += f"{status_icon} <code>{o.order_no}</code>\n   套餐：{o.plan_name} | 金额：{o.amount} USDT | {created}\n\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- Listened Users Data ---

@router.callback_query(F.data == "menu_listened_users")
async def cb_listened_users(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        total_res = await session.execute(
            select(func.count(ListenedUser.id)).where(ListenedUser.user_id == user.id)
        )
        total = total_res.scalar()
        res = await session.execute(
            select(ListenedUser)
            .where(ListenedUser.user_id == user.id)
            .order_by(ListenedUser.id.desc())
            .limit(15)
        )
        users_list = res.scalars().all()

    text = f"📊 <b>监听用户数据</b>\n\n共命中 <b>{total}</b> 个用户\n\n"
    if not users_list:
        text += "暂无监听用户记录"
    else:
        for lu in users_list:
            ulink = f'<a href="https://t.me/{lu.sender_username}">@{lu.sender_username}</a>' if lu.sender_username else f"<code>{lu.sender_id}</code>"
            kw = lu.keyword or "-"
            group = lu.group_title or "-"
            text += f"• {ulink} | 关键词：{kw} | 群：{group}\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- Chat Logs ---

@router.callback_query(F.data == "menu_chat_logs")
async def cb_chat_logs(callback: CallbackQuery):
    user = await ensure_user(callback.from_user)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(PrivateChatLog)
            .where(PrivateChatLog.user_id == user.id)
            .order_by(PrivateChatLog.id.desc())
            .limit(20)
        )
        logs = res.scalars().all()

    text = "📜 <b>私聊日志（最近20条）</b>\n\n"
    if not logs:
        text += "暂无私聊日志"
    else:
        for log in logs:
            direction = "➡️ 发送" if log.log_type == "outbound" else "⬅️ 收到"
            ts = log.created_at.strftime("%m-%d %H:%M") if log.created_at else ""
            target = f"@{log.sender_username}" if log.sender_username else str(log.sender_id or "-")
            content_preview = (log.message_text or "")[:40]
            text += f"{direction} → {target}\n   {content_preview} <i>{ts}</i>\n\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- Others / Lead Software ---

@router.callback_query(F.data == "menu_others")
async def cb_others(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(LeadSoftware).where(LeadSoftware.status == 1).order_by(LeadSoftware.sort_no.asc())
        )
        softwares = res.scalars().all()

    cs = await get_system_setting("customer_service_username") or "@service"
    text = "🧳 <b>其他引流软件</b>\n\n"
    builder = InlineKeyboardBuilder()

    if softwares:
        for sw in softwares:
            text += f"• <b>{sw.title}</b>\n  {sw.description or ''}\n\n"
            if sw.url:
                builder.row(InlineKeyboardButton(text=f"🔗 {sw.title}", url=sw.url))
    else:
        text += "暂无其他软件推荐，请联系客服了解更多服务。"

    builder.row(InlineKeyboardButton(text="🎧 联系客服", url=f"https://t.me/{cs.lstrip('@')}"))
    builder.row(InlineKeyboardButton(text="🏠 返回主菜单", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- Inbox Button Setting ---

class InboxButtonStates(StatesGroup):
    waiting_for_button = State()

@router.callback_query(F.data == "inbox_set_button")
async def cb_inbox_button(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔗 <b>设置自动回复按钮</b>\n\n"
        "请发送按钮信息，格式：\n"
        "<code>按钮文字 | https://链接地址</code>\n\n"
        "例如：<code>点击加入 | https://t.me/yourgroup</code>",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ 取消", callback_data="menu_inbox_reply")
        ).as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(InboxButtonStates.waiting_for_button)

@router.message(InboxButtonStates.waiting_for_button)
async def handle_inbox_button(message: Message, state: FSMContext):
    user = await ensure_user(message.from_user)
    parts = message.text.split("|", 1)
    if len(parts) != 2:
        await message.answer("❌ 格式错误，请使用：<code>按钮文字 | 链接</code>", parse_mode="HTML")
        return
    btn_text = parts[0].strip()
    btn_url = parts[1].strip()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(InboxAutoReply)
            .where(InboxAutoReply.user_id == user.id)
            .values(button_text=btn_text, button_url=btn_url)
        )
        await session.commit()
    await message.answer(f"✅ 按钮设置成功！\n文字：{btn_text}\n链接：{btn_url}")
    await state.clear()
    await cmd_start(message)

# --- Final Main Loop and States ---


@router.callback_query(F.data == "menu_add_target_chat")
async def cb_add_target_chat(callback: CallbackQuery, state: FSMContext):
    user = await ensure_user(callback.from_user)
    text = (
        "📥 <b>批量导入目标群</b>\n\n"
        "📌 <b>功能说明：</b>目标群是您希望接收监听命中通知的群组。当系统监测到关键词命中时，通知将发送到这些群组。\n"
        "⚠️ <b>前提：</b>机器人必须已加入该群，且该群必须为公开群（有用户名）。\n\n"
        "请发送一个 <code>.txt</code> 文件，每行一个群组用户名。\n\n"
        "格式（带 @ 或不带均可）：\n"
        "<code>@FlyIDHub124</code>\n"
        "<code>@TGAccountStore</code>\n"
        "<code>GroupUsername123</code>"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ 取消", callback_data="menu_home"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(ImportTargetChatStates.waiting_for_file)

@router.message(ImportTargetChatStates.waiting_for_file, F.document)
async def handle_target_chat_file(message: Message, state: FSMContext, bot: Bot):
    if not message.document.file_name.endswith(".txt"):
        await message.answer("❌ 请发送 <code>.txt</code> 格式的文件。")
        return

    user = await ensure_user(message.from_user)
    status_msg = await message.answer("⏳ 正在解析目标群，请稍候...")
    
    file_id = message.document.file_id
    file = await bot.get_file(file_id)
    file_content = await bot.download_file(file.file_path)
    
    content = file_content.read().decode("utf-8", errors="ignore")
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    
    success_count = 0
    duplicate_count = 0
    fail_list = []
    
    async with AsyncSessionLocal() as session:
        for line in lines:
            username = line.replace("@", "").strip()
            if not username: continue
            
            # Check if already exists
            existing = await session.execute(
                select(UserTargetChat).where(UserTargetChat.user_id == user.id, UserTargetChat.chat_username == username)
            )
            if existing.scalar_one_or_none():
                duplicate_count += 1
                continue
            
            try:
                # Try to get chat info via bot to verify it exists and bot is a member
                chat = await bot.get_chat(f"@{username}")
                
                new_chat = UserTargetChat(
                    user_id=user.id,
                    chat_id=str(chat.id),
                    chat_title=chat.title or username,
                    chat_username=username
                )
                session.add(new_chat)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to resolve target chat @{username}: {e}")
                fail_list.append(f"@{username}")
        
        await session.commit()
    
    result_text = (
        "✅ <b>导入完成</b>\n\n"
        f"✅ <b>成功添加：</b> {success_count} 个\n"
        f"⏩ <b>已存在跳过：</b> {duplicate_count} 个\n"
    )
    
    if fail_list:
        result_text += f"❌ <b>无法解析（不存在或私有群）：</b>\n"
        result_text += "\n".join([f"• <code>{f}</code>" for f in fail_list[:20]])
        if len(fail_list) > 20:
            result_text += f"\n... 以及其他 {len(fail_list) - 20} 个"
            
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 【返回主菜单】", callback_data="menu_home"))
    await status_msg.edit_text(result_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.clear()

async def main():
    bot = Bot(token=settings.BOT_TOKEN or "", parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
