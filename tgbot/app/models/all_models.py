from sqlalchemy import Column, Integer, BigInteger, String, Text, Numeric, SmallInteger, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.sql import func
from datetime import datetime

class Base(DeclarativeBase):
    pass

class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    nickname = Column(String(64), default="系统管理员")
    status = Column(SmallInteger, default=1)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(128), default="")
    nickname = Column(String(128), default="")
    balance = Column(Numeric(18, 6), default=0)
    status = Column(SmallInteger, default=1)
    listen_status = Column(SmallInteger, default=0)
    plan_id = Column(Integer, nullable=True)
    plan_name = Column(String(255), default="")
    plan_keyword_limit = Column(Integer, default=0)
    expire_at = Column(DateTime, nullable=True)
    notif_expire_soon = Column(SmallInteger, default=0)
    notif_expired = Column(SmallInteger, default=0)
    last_limit_notif_at = Column(DateTime, nullable=True) # Added for cooldown
    push_chat_id = Column(BigInteger, nullable=True) # ID of chat for notifications
    private_status = Column(SmallInteger, default=0) # Status of private chat feature
    privacy_filter_status = Column(SmallInteger, default=0) # Status of non-private user filter
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    auto_replies = relationship("AutoReply", back_populates="user", cascade="all, delete-orphan")
    keywords = relationship("UserKeyword", back_populates="user", cascade="all, delete-orphan")
    filters = relationship("UserFilter", back_populates="user", cascade="all, delete-orphan")
    protocol_accounts = relationship("ProtocolAccount", back_populates="user", cascade="all, delete-orphan")
    target_chats = relationship("UserTargetChat", back_populates="user", cascade="all, delete-orphan")

class MainAccount(Base):
    __tablename__ = "main_accounts"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    phone = Column(String(64), default="")
    session_name = Column(Text, nullable=False, default="")
    api_id = Column(String(64), default="")
    api_hash = Column(String(128), default="")
    proxy_url = Column(Text, default="")
    remark = Column(String(255), default="")
    online_status = Column(SmallInteger, default=0)
    run_status = Column(SmallInteger, default=0)
    fetch_groups_requested = Column(SmallInteger, default=0)
    last_fetch_at = Column(DateTime, nullable=True)
    login_status = Column(String(32), default="idle")
    login_code_hash = Column(Text, default="")
    login_error = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ProtocolAccount(Base):
    __tablename__ = "protocol_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    phone = Column(String(64), default="")
    session_data = Column(Text, nullable=False)
    proxy_url = Column(Text, default="")
    status = Column(SmallInteger, default=1)
    # FIX: Splitting stats as per customer request
    outbound_count = Column(Integer, default=0) # Number of UNIQUE users we initiated chat with
    inbound_count = Column(Integer, default=0)  # Number of UNIQUE users who messaged us first (and we replied)
    inbox_offset = Column(BigInteger, default=0)
    username = Column(String(64), default="")
    status_label = Column(String(32), default="未检测")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="protocol_accounts")

class ProtocolProfileSetting(Base):
    __tablename__ = "protocol_profile_settings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    display_name = Column(Text, default="")
    usernames_txt = Column(Text, default="")
    bio = Column(Text, default="")
    photo_zip_file_id = Column(Text, default="")
    photo_file_id = Column(Text, default="")
    apply_requested = Column(SmallInteger, default=0)
    retry_phones = Column(Text, default="[]") 
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class InboxAutoReply(Base):
    __tablename__ = "inbox_auto_replies"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    is_enabled = Column(SmallInteger, default=0)
    reply_content = Column(Text, default="")
    reply_type = Column(String(20), default="text")
    image_file_id = Column(Text, default="")
    button_text = Column(Text, default="")
    button_url = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class AutoReply(Base):
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    keyword = Column(String(255), nullable=False)
    reply_content = Column(Text, default="")
    reply_type = Column(String(20), default="text")
    image_file_id = Column(Text, default="")
    button_text = Column(Text, default="")
    button_url = Column(Text, default="")
    tested_ok = Column(SmallInteger, default=0)
    tested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    user = relationship("User", back_populates="auto_replies")

class UserKeyword(Base):
    __tablename__ = "user_keywords"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    keyword = Column(String(255), nullable=False)
    status = Column(SmallInteger, default=1)
    match_type = Column(String(20), default="fuzzy")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    user = relationship("User", back_populates="keywords")

class UserFilter(Base):
    __tablename__ = "user_filters"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    keyword = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    user = relationship("User", back_populates="filters")

class UserTargetChat(Base):
    __tablename__ = "user_target_chats"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    chat_id = Column(String(64), nullable=False)
    chat_title = Column(String(255), default="")
    chat_username = Column(String(255), default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="target_chats")

class RechargeOrder(Base):
    __tablename__ = "recharge_orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    order_no = Column(String(64), unique=True, nullable=False)
    plan_id = Column(Integer)
    plan_name = Column(String(255))
    amount = Column(Numeric(18, 6))
    pay_address = Column(String(255))
    from_address = Column(String(255))
    tx_hash = Column(String(255))
    status = Column(String(32), default="pending")
    expire_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ListenedUser(Base):
    __tablename__ = "listened_users"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    sender_id = Column(BigInteger)
    sender_username = Column(String(255))
    sender_name = Column(String(255))
    group_id = Column(BigInteger)
    group_title = Column(String(255))
    keyword = Column(String(255))
    message_text = Column(Text)
    # Track which protocol account made the outbound contact
    protocol_account_id = Column(Integer, ForeignKey("protocol_accounts.id", ondelete="SET NULL"))
    created_at = Column(DateTime, server_default=func.now())

class PrivateChatLog(Base):
    __tablename__ = "private_chat_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    protocol_account_id = Column(Integer, ForeignKey("protocol_accounts.id", ondelete="CASCADE"))
    sender_id = Column(BigInteger)
    sender_username = Column(String(255))
    sender_name = Column(String(255))
    message_text = Column(Text)
    # Track if this message was an INBOUND initiation from customer
    log_type = Column(String(20), default="inbound") # inbound / outbound
    created_at = Column(DateTime, server_default=func.now())

class MonitoredGroup(Base):
    __tablename__ = "monitored_groups"
    id = Column(Integer, primary_key=True)
    main_account_id = Column(Integer, ForeignKey("main_accounts.id", ondelete="CASCADE"))
    telegram_group_id = Column(BigInteger, nullable=False)
    group_title = Column(String(255), default="")
    group_username = Column(String(255), default="")
    is_blocked = Column(SmallInteger, default=0)
    is_kicked = Column(SmallInteger, default=0)
    status = Column(SmallInteger, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class GroupJoinQueue(Base):
    __tablename__ = "group_join_queue"
    id = Column(Integer, primary_key=True)
    main_account_id = Column(Integer, ForeignKey("main_accounts.id", ondelete="CASCADE"))
    group_link = Column(String(255), nullable=False)
    status = Column(String(32), default="pending")
    error_msg = Column(Text)
    min_delay_s = Column(Integer, default=300)
    max_delay_s = Column(Integer, default=600)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class SystemSetting(Base):
    __tablename__ = "system_settings"
    setting_key = Column(String(128), primary_key=True)
    setting_value = Column(Text)
    setting_type = Column(String(32), default="string")
    description = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    price = Column(Numeric(18, 2), default=0)
    keyword_limit = Column(Integer, default=0)
    plan_group = Column(String(64), default="Normal")
    duration_days = Column(Integer, default=30)
    sort_no = Column(Integer, default=0)
    status = Column(SmallInteger, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class RechargeCard(Base):
    __tablename__ = "recharge_cards"
    id = Column(Integer, primary_key=True)
    card_code = Column(String(128), unique=True, nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), default="unused")
    used_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class LeadSoftware(Base):
    __tablename__ = "lead_softwares"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    url = Column(Text, default="")
    description = Column(Text, default="")
    sort_no = Column(Integer, default=0)
    status = Column(SmallInteger, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ProtoCheckRequest(Base):
    __tablename__ = "proto_check_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class BotEvent(Base):
    __tablename__ = "bot_events"
    id = Column(Integer, primary_key=True)
    event_type = Column(String(64), nullable=False)
    user_id = Column(Integer, nullable=True)
    telegram_id = Column(BigInteger, nullable=True)
    username = Column(String(128), default="")
    nickname = Column(String(128), default="")
    detail = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
