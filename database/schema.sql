-- ================================================================
--  TG Monitor Pro - Complete Database Schema
--  PostgreSQL 12+
--  Generated automatically from SQLAlchemy models
-- ================================================================

-- Users (Bot subscribers)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    nickname VARCHAR(255),
    phone VARCHAR(50),
    balance NUMERIC(20,6) DEFAULT 0,
    is_vip SMALLINT DEFAULT 0,
    vip_expire_at TIMESTAMP,
    plan_id INTEGER DEFAULT 0,
    plan_name VARCHAR(100),
    listen_status VARCHAR(20) DEFAULT 'off',
    private_status VARCHAR(20) DEFAULT 'off',
    privacy_filter_status VARCHAR(20) DEFAULT 'off',
    push_chat_id BIGINT,
    last_limit_notif_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

-- Admin Users
CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO admin_users (username, password_hash) VALUES
('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewPGZ3eRh0w7J9Eq')
ON CONFLICT (username) DO NOTHING;
-- Default password: admin123

-- Plans
CREATE TABLE IF NOT EXISTS plans (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    price NUMERIC(20,6) DEFAULT 0,
    duration_days INTEGER DEFAULT 30,
    keyword_limit INTEGER DEFAULT 10,
    listen_group_limit INTEGER DEFAULT 5,
    description TEXT,
    is_active SMALLINT DEFAULT 1,
    sort_no INTEGER DEFAULT 0,
    updated_at TIMESTAMP
);

-- Recharge Cards
CREATE TABLE IF NOT EXISTS recharge_cards (
    id SERIAL PRIMARY KEY,
    card_code VARCHAR(100) UNIQUE NOT NULL,
    amount NUMERIC(20,6) DEFAULT 0,
    plan_id INTEGER,
    is_used SMALLINT DEFAULT 0,
    used_by BIGINT,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Recharge Orders
CREATE TABLE IF NOT EXISTS recharge_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    order_no VARCHAR(100) UNIQUE NOT NULL,
    amount NUMERIC(20,6) DEFAULT 0,
    currency VARCHAR(20) DEFAULT 'USDT',
    status VARCHAR(20) DEFAULT 'pending',
    payment_method VARCHAR(50) DEFAULT 'usdt_trc20',
    tx_hash VARCHAR(255),
    screenshot_file_id VARCHAR(255),
    username VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

-- Main Monitoring Accounts (Telethon sessions)
CREATE TABLE IF NOT EXISTS main_accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    phone VARCHAR(50),
    api_id VARCHAR(50),
    api_hash VARCHAR(100),
    session_data TEXT,
    proxy_url TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    status SMALLINT DEFAULT 0,
    is_listening SMALLINT DEFAULT 0,
    fetch_groups_requested SMALLINT DEFAULT 0,
    last_fetched_at TIMESTAMP,
    login_state VARCHAR(30) DEFAULT 'idle',
    login_code VARCHAR(20),
    login_error VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Protocol Accounts (Small accounts for sending messages)
CREATE TABLE IF NOT EXISTS protocol_accounts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    session_data TEXT NOT NULL,
    phone VARCHAR(50) DEFAULT '',
    proxy_url TEXT DEFAULT '',
    status SMALLINT DEFAULT 1,
    status_label VARCHAR(50) DEFAULT '正常',
    username VARCHAR(255) DEFAULT '',
    outbound_count INTEGER DEFAULT 0,
    daily_outbound_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Monitored Groups
CREATE TABLE IF NOT EXISTS monitored_groups (
    id SERIAL PRIMARY KEY,
    main_account_id INTEGER REFERENCES main_accounts(id) ON DELETE CASCADE,
    telegram_group_id BIGINT NOT NULL,
    group_title VARCHAR(255),
    group_username VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User Keywords
CREATE TABLE IF NOT EXISTS user_keywords (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    keyword VARCHAR(255) NOT NULL,
    match_type VARCHAR(20) DEFAULT 'contains',
    status SMALLINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User Filters (blocklist keywords)
CREATE TABLE IF NOT EXISTS user_filters (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    keyword VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Auto Replies (keyword-triggered)
CREATE TABLE IF NOT EXISTS auto_replies (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    keyword VARCHAR(255),
    reply_content TEXT,
    reply_type VARCHAR(20) DEFAULT 'text',
    image_file_id VARCHAR(255),
    button_text VARCHAR(255),
    button_url VARCHAR(500),
    is_active SMALLINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Inbox Auto Replies (private message replies)
CREATE TABLE IF NOT EXISTS inbox_auto_replies (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE UNIQUE,
    reply_content TEXT,
    reply_type VARCHAR(20) DEFAULT 'text',
    image_file_id VARCHAR(255),
    button_text VARCHAR(255),
    button_url VARCHAR(500),
    is_enabled SMALLINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Listened/Captured Users (monitoring hits)
CREATE TABLE IF NOT EXISTS listened_users (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    sender_id BIGINT NOT NULL,
    sender_username VARCHAR(255),
    sender_name VARCHAR(255),
    group_id BIGINT,
    group_title VARCHAR(255),
    keyword VARCHAR(255),
    message_text TEXT,
    protocol_account_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Private Chat Logs
CREATE TABLE IF NOT EXISTS private_chat_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    protocol_account_id INTEGER,
    sender_id BIGINT,
    sender_username VARCHAR(255),
    sender_name VARCHAR(255),
    message_text TEXT,
    log_type VARCHAR(20) DEFAULT 'inbound',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User Target Chats (notification destinations)
CREATE TABLE IF NOT EXISTS user_target_chats (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chat_id VARCHAR(100),
    chat_title VARCHAR(255),
    chat_username VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Group Join Queue
CREATE TABLE IF NOT EXISTS group_join_queue (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    group_link VARCHAR(500) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    result_message VARCHAR(500),
    executed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Lead Software
CREATE TABLE IF NOT EXISTS lead_software (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    download_url TEXT,
    is_active SMALLINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Protocol Profile Settings
CREATE TABLE IF NOT EXISTS protocol_profile_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE UNIQUE,
    photo_zip_file_id VARCHAR(255),
    apply_requested SMALLINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- System Settings (key-value store)
CREATE TABLE IF NOT EXISTS system_settings (
    id SERIAL PRIMARY KEY,
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value TEXT DEFAULT '',
    description VARCHAR(255),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Default system settings
INSERT INTO system_settings (setting_key, setting_value, description) VALUES
('site_name', 'TG Monitor Pro', '系统名称'),
('customer_service_username', '', '客服用户名'),
('usdt_trc20_address', '', 'USDT TRC20收款地址'),
('usdt_payment_notice', '请向以下地址转账，并发送截图确认。', '支付说明'),
('welcome_message', '欢迎使用 TG Monitor Pro！', '欢迎消息'),
('menu_message', '请选择功能：', '菜单消息'),
('subscription_expired_message', '您的订阅已到期，请续费。', '订阅到期消息'),
('no_plan_message', '您尚未订阅，请选择套餐。', '未订阅消息'),
('max_keywords_per_user', '20', '每用户最大关键词数'),
('daily_outbound_limit', '50', '每日外发限制'),
('expire_soon_days', '3', '到期提醒天数'),
('protocol_proxy_url', '', '协议号代理地址'),
('frontend_notice', '', '前端公告')
ON CONFLICT (setting_key) DO NOTHING;

-- Bot Events (activity tracking)
CREATE TABLE IF NOT EXISTS bot_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    telegram_id BIGINT,
    username VARCHAR(255),
    detail TEXT,
    is_read SMALLINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_listened_users_user_id ON listened_users(user_id);
CREATE INDEX IF NOT EXISTS idx_listened_users_sender_id ON listened_users(user_id, sender_id);
CREATE INDEX IF NOT EXISTS idx_private_chat_logs_user_id ON private_chat_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_bot_events_created_at ON bot_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recharge_orders_status ON recharge_orders(status);
CREATE INDEX IF NOT EXISTS idx_user_keywords_user_id ON user_keywords(user_id);
CREATE INDEX IF NOT EXISTS idx_monitored_groups_account ON monitored_groups(main_account_id);
