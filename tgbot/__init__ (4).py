import asyncio
import logging
import os
import random
import re
import io
from telethon import TelegramClient, events, types, functions
from telethon.sessions import StringSession
from datetime import datetime, timedelta, time
from sqlalchemy.future import select
from sqlalchemy import update, func, distinct, and_
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..core.config import settings
from ..core.db import AsyncSessionLocal
from ..core.utils import resolve_template, parse_proxy_url
from ..models.all_models import (
    MainAccount, User, UserKeyword, UserFilter, 
    MonitoredGroup, AutoReply, SystemSetting, ListenedUser,
    GroupJoinQueue, UserTargetChat, PrivateChatLog, ProtocolAccount, InboxAutoReply,
    ProtocolProfileSetting
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RANDOM_EMOJIS = ['😊','😍','🥰','😎','🤩','🎉','🔥','💯','✅','👍','💪','🌟','🚀','💰','🎁','❤️','👑','💎','🤝','😄','🎯','🌈','⚡','🍀','🏆']

class MonitoringService:
    def __init__(self):
        self.active_clients = {}  # account_id (MainAccount) -> client
        self.protocol_clients = {} # account_id (ProtocolAccount) -> client
        self.running_tasks = {}    # account_id -> task
        self.bot = None
        self._login_sessions = {}  # account_id -> {"client": client, "phone_code_hash": hash}
        self.daily_reset_task = None

    async def get_protocol_client(self, acc: ProtocolAccount, ma: MainAccount):
        """Get or create an active Telethon client for a Protocol Account."""
        if acc.id in self.protocol_clients:
            client = self.protocol_clients[acc.id]
            if client.is_connected():
                return client
            else:
                try: await client.connect()
                except: pass
                if client.is_connected(): return client
        
        # Create new client
        proxy = parse_proxy_url(acc.proxy_url or "")
        client = TelegramClient(
            StringSession(acc.session_data), 
            int(ma.api_id), ma.api_hash,
            connection_retries=3, 
            timeout=15,
            parse_mode='html',
            proxy=proxy
        )
        try:
            await client.connect()
            if await client.is_user_authorized():
                self.protocol_clients[acc.id] = client
                return client
        except:
            pass
        return None

    async def check_daily_reset(self):
        """Background task to reset account counts daily at midnight (UTC+8)."""
        while True:
            # China Standard Time (UTC+8)
            now = datetime.utcnow() + timedelta(hours=8)
            next_reset = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
            
            # Calculate sleep seconds in UTC
            now_utc = datetime.utcnow()
            reset_utc = next_reset - timedelta(hours=8)
            sleep_seconds = (reset_utc - now_utc).total_seconds()
            
            if sleep_seconds < 0: sleep_seconds = 1
            
            logger.info(f"Daily stats reset scheduled in {sleep_seconds} seconds (at China Midnight)")
            await asyncio.sleep(sleep_seconds)
            
            async with AsyncSessionLocal() as session:
                try:
                    await session.execute(
                        update(ProtocolAccount)
                        .values(outbound_count=0, inbound_count=0)
                    )
                    # Also clear limit notifications
                    await session.execute(
                        update(User)
                        .values(last_limit_notif_at=None)
                    )
                    await session.commit()
                    logger.info("Daily statistics and notification limits reset successfully")
                except Exception as e:
                    logger.error(f"Failed to reset daily stats: {e}")
            
            await asyncio.sleep(60) # Avoid multiple resets if sleep was too short

    async def get_bot(self):
        if not self.bot:
            token = settings.BOT_TOKEN
            if not token:
                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(SystemSetting).where(SystemSetting.setting_key == "bot_token"))
                    token_setting = res.scalar_one_or_none()
                    token = token_setting.setting_value if token_setting else None
            if token:
                self.bot = Bot(token=token, parse_mode="HTML")
        return self.bot

    async def send_protocol_message(self, client, peer, text, msg_type="text", file_id=None, buttons=None):
        """Helper to send message via protocol account, handling bot file IDs and buttons."""
        try:
            # Handle Buttons for Protocol Accounts (Regular accounts can't send Inline Keyboards)
            # Workaround: Append buttons as links at the end of the text
            if buttons:
                button_text = ""
                for b in buttons:
                    if hasattr(b, 'text') and hasattr(b, 'url'):
                        button_text += f"\n\n🔗 <a href='{b.url}'>{b.text}</a>"
                text += button_text

            if msg_type == "image" and file_id:
                bot = await self.get_bot()
                if bot:
                    # Download file from bot to memory
                    file = await bot.get_file(file_id)
                    file_data = io.BytesIO()
                    await bot.download_file(file.file_path, file_data)
                    file_data.seek(0)
                    # Send to user via protocol account (Telethon supports HTML if formatted correctly)
                    await client.send_file(peer, file_data, caption=text, parse_mode='html')
                    return True
            
            await client.send_message(peer, text, parse_mode='html')
            return True
        except Exception as e:
            logger.error(f"Failed to send protocol message: {e}")
            return False

    async def handle_new_message(self, event, account_id, client):
        # NEW: Ignore stickers to avoid junk logs and unnecessary auto-replies
        if event.message.sticker:
            return

        if not event.is_group:
            # --- Handle PRIVATE Messages (INBOUND logic) ---
            if event.is_private:
                sender = await event.get_sender()
                if not sender or sender.bot: return
                
                async with AsyncSessionLocal() as session:
                    # Find account and owner
                    acc_res = await session.execute(select(ProtocolAccount).where(ProtocolAccount.id == account_id))
                    acc = acc_res.scalar_one_or_none()
                    if not acc: return
                    
                    # 1. Check for Inbox Auto-Reply settings
                    iar_res = await session.execute(select(InboxAutoReply).where(InboxAutoReply.user_id == acc.user_id))
                    iar = iar_res.scalar_one_or_none()
                    
                    if iar and iar.is_enabled:
                        # 2. Perform Auto-Reply
                        reply_text = resolve_template(iar.reply_content, RANDOM_EMOJIS)
                        
                        # Create a simple button list if button text is set
                        iar_buttons = []
                        if iar.button_text:
                            # Use a default URL or parse it if stored
                            iar_buttons.append(type('obj', (object,), {'text': iar.button_text, 'url': settings.BACKEND_URL}))

                        success = await self.send_protocol_message(
                            client, sender.id, reply_text, 
                            iar.reply_type, iar.image_file_id,
                            buttons=iar_buttons
                        )
                        
                        if success:
                            # 3. Log the Inbound Interaction (Message from customer)
                            new_log = PrivateChatLog(
                                user_id=acc.user_id,
                                protocol_account_id=acc.id,
                                sender_id=sender.id,
                                sender_username=sender.username or "",
                                sender_name=f"{sender.first_name or ''} {sender.last_name or ''}".strip(),
                                message_text=event.message.message or "",
                                log_type="inbound"
                            )
                            session.add(new_log)

                            # NEW: Log the Outbound Reply (Message from our account)
                            outbound_reply_log = PrivateChatLog(
                                user_id=acc.user_id,
                                protocol_account_id=acc.id,
                                sender_id=sender.id,
                                sender_username=sender.username or "",
                                sender_name=f"{sender.first_name or ''} {sender.last_name or ''}".strip(),
                                message_text=reply_text,
                                log_type="outbound"
                            )
                            session.add(outbound_reply_log)
                            
                            # 4. Update INBOUND Statistics (Count UNIQUE users)
                            unique_in_check = await session.execute(
                                select(PrivateChatLog).where(
                                    PrivateChatLog.protocol_account_id == acc.id,
                                    PrivateChatLog.sender_id == sender.id,
                                    PrivateChatLog.log_type == "inbound",
                                    PrivateChatLog.id != new_log.id
                                )
                            )
                            if not unique_in_check.scalar_one_or_none():
                                acc.inbound_count += 1 
                                
                            await session.commit()
                            logger.info(f"Inbound auto-reply sent to {sender.id} for account {acc.phone}")
            return
            
        # --- Handle GROUP Messages (OUTBOUND logic) ---
        text = event.message.message or ""
        if not text: return
            
        async with AsyncSessionLocal() as session:
            # Check if this protocol account has reached its daily limit (e.g., 3 users)
            # We'll get the limit from a system setting or default to 3
            res_limit = await session.execute(select(SystemSetting).where(SystemSetting.setting_key == "daily_outbound_limit"))
            limit_setting = res_limit.scalar_one_or_none()
            DAILY_LIMIT = int(limit_setting.setting_value) if limit_setting else 3

            acc_res = await session.execute(select(ProtocolAccount).where(ProtocolAccount.id == account_id))
            acc = acc_res.scalar_one_or_none()
            if not acc: return

            if acc.outbound_count >= DAILY_LIMIT:
                # Account reached limit, skip outbound for this account
                return

            users_res = await session.execute(
                select(User).where(User.status == 1, User.listen_status == 1, 
                                   (User.expire_at == None) | (User.expire_at > func.now()))
            )
            users = users_res.scalars().all()
            
            for user in users:
                # 1. Check if this user has any protocol accounts that haven't reached the limit
                accs_res = await session.execute(
                    select(ProtocolAccount).where(
                        ProtocolAccount.user_id == user.id,
                        ProtocolAccount.status == 1
                    )
                )
                all_accs = accs_res.scalars().all()
                
                # Check limit setting
                res_limit = await session.execute(select(SystemSetting).where(SystemSetting.setting_key == "daily_outbound_limit"))
                limit_setting = res_limit.scalar_one_or_none()
                DAILY_LIMIT = int(limit_setting.setting_value) if limit_setting else 3
                
                available_accs = [a for a in all_accs if a.outbound_count < DAILY_LIMIT]
                
                if not available_accs and all_accs:
                     # Cooldown: Notify at most once every 4 hours
                     now = datetime.now()
                     if not user.last_limit_notif_at or (now - user.last_limit_notif_at) > timedelta(hours=4):
                         bot = await self.get_bot()
                         if bot:
                             warning_text = (
                                 f"⚠️ <b>协议号提醒</b>\n\n"
                                 f"您的所有协议号已用完，共 {len(all_accs)} 个协议号已达到私聊上限（每个最多私聊 {DAILY_LIMIT} 个用户）。\n\n"
                                 f"请前往机器人补充新的协议号后继续使用自动回复功能。"
                             )
                             try: 
                                 await bot.send_message(user.telegram_id, warning_text)
                                 user.last_limit_notif_at = now
                                 await session.commit()
                             except: pass
                     continue
                
                # Check filters
                filters_res = await session.execute(select(UserFilter).where(UserFilter.user_id == user.id))
                filters = [f.keyword.lower() for f in filters_res.scalars().all()]
                if any(f in text.lower() for f in filters): continue
                
                # Check keywords
                keywords_res = await session.execute(select(UserKeyword).where(UserKeyword.user_id == user.id, UserKeyword.status == 1))
                keywords = keywords_res.scalars().all()
                
                for kw_obj in keywords:
                    kw = kw_obj.keyword.lower()
                    if (kw_obj.match_type == "exact" and kw == text.lower().strip()) or (kw_obj.match_type != "exact" and kw in text.lower()):
                        sender = await event.get_sender()
                        if not sender or not sender.username: continue

                        # Check for duplicate hits (last hour)
                        one_hour_ago = datetime.now() - timedelta(hours=1)
                        dup_check = await session.execute(
                            select(ListenedUser).where(
                                ListenedUser.user_id == user.id,
                                ListenedUser.sender_id == sender.id,
                                ListenedUser.created_at > one_hour_ago
                            )
                        )
                        if dup_check.scalar_one_or_none(): continue

                        # 1. Log the Outbound Initiation
                        chat = await event.get_chat()
                        new_hit = ListenedUser(
                            user_id=user.id,
                            sender_id=sender.id,
                            sender_username=sender.username,
                            sender_name=f"{sender.first_name or ''} {sender.last_name or ''}".strip(),
                            group_id=chat.id,
                            group_title=chat.title,
                            keyword=kw_obj.keyword,
                            message_text=text,
                            protocol_account_id=account_id 
                        )
                        session.add(new_hit)
                        
                        # 2. Update OUTBOUND Statistics (Count UNIQUE users)
                        acc_res = await session.execute(select(ProtocolAccount).where(ProtocolAccount.id == account_id))
                        acc = acc_res.scalar_one()
                        
                        unique_out_check = await session.execute(
                            select(ListenedUser).where(
                                ListenedUser.protocol_account_id == account_id,
                                ListenedUser.sender_id == sender.id,
                                ListenedUser.id != new_hit.id
                            )
                        )
                        if not unique_out_check.scalar_one_or_none():
                            acc.outbound_count += 1

                        # 3. Send Notification via Bot
                        bot = await self.get_bot()
                        if bot:
                            # FIX: Wrap username in <a> tag for clickability, per customer request
                            username_display = f'<a href="https://t.me/{sender.username}">@{sender.username}</a>'
                            notif_text = (
                                f"🎯 <b>发现匹配关键词：</b> {kw_obj.keyword}\n\n"
                                f"👥 <b>来源群组：</b> {chat.title}\n"
                                f"👤 <b>发布者：</b> {new_hit.sender_name} ({username_display})\n"
                                f"💬 <b>内容：</b>\n{text[:500]}"
                            )
                            kb = InlineKeyboardBuilder()
                            msg_link = f"https://t.me/c/{str(chat.id).replace('-100', '')}/{event.message.id}"
                            if hasattr(chat, 'username') and chat.username:
                                msg_link = f"https://t.me/{chat.username}/{event.message.id}"
                            kb.row(InlineKeyboardButton(text="↗️ 查看消息", url=msg_link))
                            kb.row(InlineKeyboardButton(text="💬 私聊", url=f"https://t.me/{sender.username}"),
                                   InlineKeyboardButton(text="🚫 屏蔽此人", callback_data=f"blk:{user.id}:{sender.id}"))
                            try: await bot.send_message(user.telegram_id, notif_text, parse_mode="HTML", reply_markup=kb.as_markup())
                            except: pass
                        
                        # 4. Pick an available Protocol Account for this user to send the PM
                        available_acc = None
                        if available_accs:
                            available_acc = random.choice(available_accs)
                        
                        if available_acc:
                            reply_text = resolve_template(ar.reply_content, RANDOM_EMOJIS)
                            
                            ar_buttons = []
                            if ar.button_text and ar.button_url:
                                ar_buttons.append(type('obj', (object,), {'text': ar.button_text, 'url': ar.button_url}))

                            # Get MainAccount for api_id/api_hash
                            ma_res = await session.execute(select(MainAccount).where(MainAccount.id == account_id))
                            ma = ma_res.scalar_one()

                            # Get active or new client for the Protocol Account
                            temp_client = await self.get_protocol_client(available_acc, ma)
                            
                            if temp_client:
                                try:
                                    success = await self.send_protocol_message(
                                        temp_client, sender.id, reply_text,
                                        ar.reply_type, ar.image_file_id,
                                        buttons=ar_buttons
                                    )
                                    
                                    if success:
                                        # Update the CORRECT account's stats
                                        available_acc.outbound_count += 1
                                        
                                        # Log the outbound message
                                        outbound_log = PrivateChatLog(
                                            user_id=user.id,
                                            protocol_account_id=available_acc.id,
                                            sender_id=sender.id,
                                            sender_username=sender.username or "",
                                            sender_name=f"{sender.first_name or ''} {sender.last_name or ''}".strip(),
                                            message_text=reply_text,
                                            log_type="outbound"
                                        )
                                        session.add(outbound_log)
                                except Exception as e:
                                    logger.error(f"Protocol account {available_acc.phone} failed to send PM: {e}")
                                    available_acc.status_label = "异常"
                                    await session.commit()
                        
                        await session.commit()
                        break 

    async def process_join_queue(self, client, account_id):
        async with AsyncSessionLocal() as session:
            # 1. Fetch ALL pending tasks for this account at once
            res = await session.execute(
                select(GroupJoinQueue).where(
                    GroupJoinQueue.main_account_id == account_id, 
                    GroupJoinQueue.status == "pending"
                ).order_by(GroupJoinQueue.created_at.asc())
            )
            tasks = res.scalars().all()
            
            if not tasks:
                return

            logger.info(f"Account {account_id} starting join queue for {len(tasks)} groups")
            
            for task in tasks:
                # Re-fetch task status to ensure it hasn't been cancelled or processed
                await session.refresh(task)
                if task.status != "pending":
                    continue

                try:
                    # 2. Parse delay with larger range to avoid detection
                    delay = random.randint(task.min_delay_s, task.max_delay_s)
                    logger.info(f"Account {account_id} waiting {delay}s before joining {task.group_link}")
                    await asyncio.sleep(delay)
                    
                    # 3. Clean and Join
                    link = task.group_link.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip()
                    if "/" in link: link = link.split("/")[-1]
                    
                    try:
                        await client(functions.channels.JoinChannelRequest(channel=link))
                        task.status = "completed"
                        task.error_msg = None
                        logger.info(f"Account {account_id} joined {task.group_link} successfully")
                    except Exception as e:
                        error_str = str(e)
                        task.error_msg = error_str
                        
                        if "FLOOD_WAIT" in error_str:
                            # Robust flood wait handling
                            wait_match = re.search(r'(\d+)', error_str)
                            wait_time = int(wait_match.group(1)) if wait_match else 300
                            logger.warning(f"Account {account_id} hit flood wait for {wait_time}s. Pausing queue.")
                            task.status = "pending" # Keep pending to retry
                            await session.commit()
                            await asyncio.sleep(wait_time + 30) # Wait the required time plus safety buffer
                            continue # Move to next retry in the same loop or next run
                        elif "INVITE_HASH_EXPIRED" in error_str or "USERNAME_INVALID" in error_str:
                            task.status = "failed"
                        else:
                            # For other errors, we might want to retry later
                            task.status = "failed"
                            logger.error(f"Account {account_id} failed to join {task.group_link}: {e}")
                    
                    await session.commit()
                except Exception as e:
                    logger.error(f"Critical error in join queue for task {task.id}: {e}")
                    await session.rollback()
                    await asyncio.sleep(10) # Safety pause before next task

    async def sync_groups(self, client, account_id):
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(MainAccount).where(MainAccount.id == account_id))
            acc = res.scalar_one()
            if acc.fetch_groups_requested != 1:
                return
            logger.info(f"[SyncGroups] Starting sync for account {account_id}")
            added = 0
            try:
                async for dialog in client.iter_dialogs():
                    if dialog.is_group or dialog.is_channel:
                        gid = int(dialog.id)  # BigInteger — keep as int
                        try:
                            existing = await session.execute(
                                select(MonitoredGroup).where(
                                    MonitoredGroup.main_account_id == account_id,
                                    MonitoredGroup.telegram_group_id == gid
                                )
                            )
                            if not existing.scalar_one_or_none():
                                username = ""
                                try:
                                    username = dialog.entity.username or ""
                                except Exception:
                                    pass
                                session.add(MonitoredGroup(
                                    main_account_id=account_id,
                                    telegram_group_id=gid,
                                    group_title=dialog.title or "Unknown",
                                    group_username=username,
                                    status=1
                                ))
                                added += 1
                        except Exception as row_err:
                            logger.warning(f"[SyncGroups] Skipping dialog {gid}: {row_err}")
                            await session.rollback()
                            continue
                acc.fetch_groups_requested = 0
                acc.last_fetch_at = func.now()
                await session.commit()
                logger.info(f"[SyncGroups] Done for account {account_id}: added {added} groups")
            except Exception as e:
                logger.error(f"[SyncGroups] Error for account {account_id}: {e}")
                try:
                    await session.rollback()
                    await session.execute(
                        update(MainAccount).where(MainAccount.id == account_id).values(
                            fetch_groups_requested=0
                        )
                    )
                    await session.commit()
                except Exception:
                    pass

    async def start_account(self, account: MainAccount):
        account_id = account.id
        phone = account.phone
        session_str = account.session_name or ""
        api_id = int(account.api_id)
        api_hash = account.api_hash

        logger.info(f"[Listener] Connecting account {account_id} ({phone}) ...")
        client = TelegramClient(
            StringSession(session_str),
            api_id, api_hash,
            connection_retries=5,
            auto_reconnect=True,
        )
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
            if not authorized:
                logger.error(f"[Listener] Account {account_id} session invalid/expired — marking error")
                async with AsyncSessionLocal() as sess:
                    await sess.execute(
                        update(MainAccount).where(MainAccount.id == account_id).values(
                            login_status="error", run_status=0,
                            login_error="Session expired, please re-login"
                        )
                    )
                    await sess.commit()
                return

            logger.info(f"[Listener] Account {account_id} connected and authorized ✅")
            self.active_clients[account_id] = client

            @client.on(events.NewMessage)
            async def handler(event):
                await self.handle_new_message(event, account_id, client)

            while True:
                await self.process_join_queue(client, account_id)
                await self.sync_groups(client, account_id)
                await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"[Listener] Account {account_id} failed: {e}", exc_info=True)
        finally:
            if account_id in self.active_clients:
                del self.active_clients[account_id]
            if account_id in self.running_tasks:
                del self.running_tasks[account_id]
            try:
                await client.disconnect()
            except Exception:
                pass
            logger.info(f"[Listener] Account {account_id} task ended")

    async def process_profile_updates(self):
        """Background task to process pending profile updates (Bulk Profile Change)."""
        while True:
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(ProtocolProfileSetting).where(ProtocolProfileSetting.apply_requested == 1)
                )
                settings_list = res.scalars().all()
                
                for setting in settings_list:
                    user_id = setting.user_id
                    bot = await self.get_bot()
                    if not bot: continue
                    
                    try:
                        # 1. Download ZIP from Bot
                        file = await bot.get_file(setting.photo_zip_file_id)
                        zip_data = io.BytesIO()
                        await bot.download_file(file.file_path, zip_data)
                        zip_data.seek(0)
                        
                        # 2. Extract images
                        images = []
                        with zipfile.ZipFile(zip_data) as z:
                            for name in z.namelist():
                                if name.lower().endswith(('.png', '.jpg', '.jpeg')):
                                    images.append(z.read(name))
                        
                        if not images:
                            logger.warning(f"No images found in ZIP for user {user_id}")
                            setting.apply_requested = 0
                            await session.commit()
                            continue
                            
                        # 3. Get user's accounts
                        acc_res = await session.execute(
                            select(ProtocolAccount).where(ProtocolAccount.user_id == user_id, ProtocolAccount.status == 1)
                        )
                        accounts = acc_res.scalars().all()
                        
                        # Get a MainAccount for API ID/Hash
                        ma_res = await session.execute(select(MainAccount).limit(1))
                        ma = ma_res.scalar_one_or_none()
                        if not ma: continue
                        
                        for acc in accounts:
                            client = await self.get_protocol_client(acc, ma)
                            if client:
                                try:
                                    # Update Photo
                                    img_data = random.choice(images)
                                    await client(functions.photos.UploadProfilePhotoRequest(
                                        file=await client.upload_file(img_data)
                                    ))
                                    
                                    # Update Name/Bio if set
                                    if setting.display_name or setting.bio:
                                        await client(functions.account.UpdateProfileRequest(
                                            first_name=setting.display_name or acc.phone,
                                            about=setting.bio or ""
                                        ))
                                    
                                    logger.info(f"Updated profile for account {acc.phone}")
                                except Exception as e:
                                    logger.error(f"Failed to update profile for {acc.phone}: {e}")
                        
                        setting.apply_requested = 0
                        await session.commit()
                        
                        # Notify user
                        try: await bot.send_message(user_id, "✅ <b>批量资料修改完成！</b>\n\n您的所有账号头像及资料已成功更新。", parse_mode="HTML")
                        except: pass
                        
                    except Exception as e:
                        logger.error(f"Error processing profile ZIP for user {user_id}: {e}")
                        setting.apply_requested = 0
                        await session.commit()
            
            await asyncio.sleep(60)

    async def handle_login_requests(self):
        """Poll DB for accounts needing OTP login and process them."""
        from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
        try:
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(MainAccount).where(MainAccount.login_status.in_(["request_code", "submit_code", "submit_password"]))
                )
                accounts = res.scalars().all()

            for acc in accounts:
                aid = acc.id
                try:
                    if acc.login_status == "request_code":
                        # Create a fresh client and send OTP
                        client = TelegramClient(
                            StringSession(""),
                            int(acc.api_id),
                            acc.api_hash,
                        )
                        await client.connect()
                        result = await client.send_code_request(acc.phone)
                        self._login_sessions[aid] = {
                            "client": client,
                            "phone_code_hash": result.phone_code_hash,
                        }
                        async with AsyncSessionLocal() as session:
                            await session.execute(
                                update(MainAccount).where(MainAccount.id == aid).values(
                                    login_status="code_sent", login_error="", updated_at=func.now()
                                )
                            )
                            await session.commit()
                        logger.info(f"[Login] OTP sent for account {aid} ({acc.phone})")

                    elif acc.login_status == "submit_code":
                        sess = self._login_sessions.get(aid)
                        if not sess:
                            # Session lost (restart?) — ask for resend
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        login_status="error",
                                        login_error="Session expired, please click 发送验证码 again",
                                        updated_at=func.now()
                                    )
                                )
                                await session.commit()
                            continue
                        client = sess["client"]
                        phone_code_hash = sess["phone_code_hash"]
                        code = acc.login_code_hash  # stored by API
                        try:
                            await client.sign_in(acc.phone, code, phone_code_hash=phone_code_hash)
                            session_str = client.session.save()
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        session_name=session_str,
                                        login_status="logged_in",
                                        run_status=1,
                                        login_error="",
                                        updated_at=func.now()
                                    )
                                )
                                await session.commit()
                            del self._login_sessions[aid]
                            logger.info(f"[Login] Account {aid} ({acc.phone}) logged in successfully")
                        except SessionPasswordNeededError:
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        login_status="need_2fa", updated_at=func.now()
                                    )
                                )
                                await session.commit()
                        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        login_status="error",
                                        login_error=str(e),
                                        updated_at=func.now()
                                    )
                                )
                                await session.commit()

                    elif acc.login_status == "submit_password":
                        sess = self._login_sessions.get(aid)
                        if not sess:
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        login_status="error",
                                        login_error="Session expired, please restart login",
                                        updated_at=func.now()
                                    )
                                )
                                await session.commit()
                            continue
                        client = sess["client"]
                        password = acc.login_code_hash
                        try:
                            await client.sign_in(password=password)
                            session_str = client.session.save()
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        session_name=session_str,
                                        login_status="logged_in",
                                        run_status=1,
                                        login_error="",
                                        updated_at=func.now()
                                    )
                                )
                                await session.commit()
                            del self._login_sessions[aid]
                            logger.info(f"[Login] Account {aid} logged in with 2FA")
                        except Exception as e:
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(MainAccount).where(MainAccount.id == aid).values(
                                        login_status="error",
                                        login_error=str(e),
                                        updated_at=func.now()
                                    )
                                )
                                await session.commit()
                except Exception as e:
                    logger.error(f"[Login] Error processing account {aid}: {e}")
                    try:
                        async with AsyncSessionLocal() as session:
                            await session.execute(
                                update(MainAccount).where(MainAccount.id == aid).values(
                                    login_status="error",
                                    login_error=str(e),
                                    updated_at=func.now()
                                )
                            )
                            await session.commit()
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[Login] handle_login_requests error: {e}")

    async def run(self):
        # Start background tasks
        if not self.daily_reset_task:
            self.daily_reset_task = asyncio.create_task(self.check_daily_reset())
        
        asyncio.create_task(self.process_profile_updates())

        while True:
            # Handle OTP login requests
            await self.handle_login_requests()

            async with AsyncSessionLocal() as session:
                res = await session.execute(select(MainAccount).where(MainAccount.run_status == 1))
                accounts = res.scalars().all()
                for acc in accounts:
                    if acc.id not in self.active_clients and acc.id not in self.running_tasks:
                        task = asyncio.create_task(self.start_account(acc))
                        self.running_tasks[acc.id] = task
                    elif acc.run_status == 0 and acc.id in self.active_clients:
                        client = self.active_clients[acc.id]
                        await client.disconnect()
                        del self.active_clients[acc.id]
                        if acc.id in self.running_tasks:
                            self.running_tasks[acc.id].cancel()
                            del self.running_tasks[acc.id]
            await asyncio.sleep(5)

if __name__ == "__main__":
    service = MonitoringService()
    asyncio.run(service.run())
