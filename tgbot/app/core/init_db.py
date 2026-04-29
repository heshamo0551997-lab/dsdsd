import asyncio
import logging
import bcrypt as bcrypt_lib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text
from ..core.db import engine, AsyncSessionLocal
from ..models.all_models import Base, SystemSetting, Plan, AdminUser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def hash_password(password: str) -> str:
    return bcrypt_lib.hashpw(password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8')

MIGRATIONS = [
    # plans: add sort_no
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS sort_no INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()",
    # group_join_queue: add executed_at
    "ALTER TABLE group_join_queue ADD COLUMN IF NOT EXISTS executed_at TIMESTAMP",
    # protocol_accounts: add status_label and username if missing
    "ALTER TABLE protocol_accounts ADD COLUMN IF NOT EXISTS status_label VARCHAR(32) DEFAULT '未检测'",
    "ALTER TABLE protocol_accounts ADD COLUMN IF NOT EXISTS username VARCHAR(64) DEFAULT ''",
    # main_accounts: add fetch_groups columns if missing
    "ALTER TABLE main_accounts ADD COLUMN IF NOT EXISTS fetch_groups_requested SMALLINT DEFAULT 0",
    "ALTER TABLE main_accounts ADD COLUMN IF NOT EXISTS last_fetch_at TIMESTAMP",
    "ALTER TABLE main_accounts ADD COLUMN IF NOT EXISTS login_status VARCHAR(32) DEFAULT 'idle'",
    "ALTER TABLE main_accounts ADD COLUMN IF NOT EXISTS login_code_hash TEXT DEFAULT ''",
    "ALTER TABLE main_accounts ADD COLUMN IF NOT EXISTS login_error TEXT DEFAULT ''",
    # users: add private_status columns if missing
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS private_status SMALLINT DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_filter_status SMALLINT DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_limit_notif_at TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_chat_id BIGINT",
    # recharge_orders: add updated_at
    "ALTER TABLE recharge_orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()",
    # inbox_auto_replies: add button_url
    "ALTER TABLE inbox_auto_replies ADD COLUMN IF NOT EXISTS button_url TEXT DEFAULT ''",
    # bot_events: new table for activity tracking
    """CREATE TABLE IF NOT EXISTS bot_events (
        id SERIAL PRIMARY KEY,
        event_type VARCHAR(64) NOT NULL,
        user_id INTEGER,
        telegram_id BIGINT,
        username VARCHAR(128) DEFAULT '',
        nickname VARCHAR(128) DEFAULT '',
        detail TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW()
    )""",
]

async def run_migrations():
    async with engine.begin() as conn:
        for sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                logger.info(f"Migration OK: {sql[:60]}...")
            except Exception as e:
                logger.warning(f"Migration warning (may be OK): {e}")

async def init_db():
    async with engine.begin() as conn:
        logger.info("Creating all tables...")
        await conn.run_sync(Base.metadata.create_all)

    # Run column migrations for tables that existed before
    await run_migrations()

    async with AsyncSessionLocal() as session:
        # 1. Default Admin
        admin_res = await session.execute(select(AdminUser).where(AdminUser.username == "admin"))
        if not admin_res.scalar_one_or_none():
            logger.info("Creating default admin user...")
            session.add(AdminUser(
                username="admin",
                password_hash=hash_password("admin123"),
                nickname="Admin"
            ))

        # 2. Default Plans
        plans_res = await session.execute(select(Plan))
        existing = plans_res.scalars().all()
        if not existing:
            logger.info("Creating default plans...")
            session.add(Plan(name="Trial Member", price=0, keyword_limit=10, plan_group="Normal", duration_days=0, sort_no=1))
            session.add(Plan(name="Platinum Member", price=30, keyword_limit=50, plan_group="Normal", duration_days=30, sort_no=2))
            session.add(Plan(name="Diamond Member", price=100, keyword_limit=200, plan_group="Normal", duration_days=30, sort_no=3))

        # 3. Default Settings with descriptions
        settings_to_init = [
            ("site_name", "TG Monitor Pro", "string", "站点名称"),
            ("usdt_trc20_address", "", "string", "USDT-TRC20 收款地址"),
            ("usdt_payment_notice", "请使用 USDT-TRC20 支付\n付款后系统将自动激活", "text", "支付说明"),
            ("admin_telegram_id", "", "string", "管理员 Telegram ID（接收系统通知）"),
            ("customer_service_username", "@service", "string", "客服 Telegram Username"),
            ("daily_outbound_limit", "3", "number", "每个协议号每日最大私聊数量"),
            ("protocol_proxy_url", "", "string", "协议号默认代理 URL（例如 socks5://127.0.0.1:1080）"),
            ("frontend_notice", "", "text", "前台公告（显示在机器人欢迎消息中）"),
            ("expire_soon_days", "3", "number", "到期前几天发送续费提醒"),
            ("max_keywords_per_user", "50", "number", "每个用户最多关键词数量"),
        ]

        for key, val, stype, desc in settings_to_init:
            res = await session.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
            if not res.scalar_one_or_none():
                session.add(SystemSetting(setting_key=key, setting_value=val, setting_type=stype, description=desc))

        await session.commit()
        logger.info("Database initialization completed!")

if __name__ == "__main__":
    asyncio.run(init_db())
