from datetime import datetime
import json
import random
import urllib.parse

def format_money(v):
    if v is None:
        return "0"
    try:
        s = f"{float(v):.6f}".rstrip('0').rstrip('.')
        return s if s else "0"
    except (ValueError, TypeError):
        return str(v)

def format_date(v):
    if not v:
        return "-"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)

def format_status(s):
    status_map = {
        'pending': '待支付',
        'paid': '已支付',
        'expired': '已过期',
        'done': '已完成',
        'failed': '失败'
    }
    return status_map.get(s, str(s))

import re

def resolve_template(text: str, random_emojis: list) -> str:
    if not text:
        return text
    text = text.replace("{随机数字}", str(random.randint(1000000, 9999999)))
    text = text.replace("{随机英文}", ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ', k=6)))
    text = text.replace("{随机表情}", random.choice(random_emojis))
    # NEW: Automatically convert @username to clickable links in the content
    # Robust regex for @username that doesn't match email addresses or inside existing tags
    text = re.sub(r'(?<![\w\/.])@(\w{4,32})', r'<a href="https://t.me/\1">@\1</a>', text)
    return text

def parse_proxy_url(proxy_url: str):
    """Parse proxy URL string into Telethon-compatible proxy tuple.
    Supports: socks5://[user:pass@]host:port
              socks4://host:port
              http://[user:pass@]host:port
    Returns: (socks_type, host, port, rdns, username, password) or None
    """
    if not proxy_url or not proxy_url.strip():
        return None
    try:
        import socks
        url = proxy_url.strip()
        if "://" not in url:
            url = "socks5://" + url
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port or 1080
        username = parsed.username or None
        password = parsed.password or None
        if scheme == "socks4":
            proxy_type = socks.SOCKS4
        elif scheme in ("http", "https"):
            proxy_type = socks.HTTP
        else:
            proxy_type = socks.SOCKS5
        return (proxy_type, host, port, True, username, password)
    except Exception:
        return None

def translate_tg_error(error_msg: str) -> str:
    """Translates Telegram API errors to Chinese for the user."""
    error_msg = str(error_msg).upper()
    
    translations = {
        "FROZEN_METHOD_INVALID": "账号被冻结，暂时无法执行此操作",
        "AUTH_KEY_UNREGISTERED": "会话已失效，请重新登录",
        "USER_DEACTIVATED_BAN": "账号已被封禁",
        "PEER_ID_INVALID": "无效的用户或群组ID",
        "CHAT_WRITE_FORBIDDEN": "在此群组没有发言权限",
        "FLOOD_WAIT": "操作频繁，请稍后再试",
        "SESSION_REVOKED": "会话已被撤销",
        "PHONE_NUMBER_BANNED": "此手机号已被封禁",
        "USERNAME_INVALID": "无效的用户名",
        "USERNAME_OCCUPIED": "用户名已被占用",
        "IMAGE_PROCESS_FAILED": "图片处理失败",
        "RPC_CALL_FAIL": "远程调用失败",
    }
    
    for key, value in translations.items():
        if key in error_msg:
            return value
            
    return f"操作失败: {error_msg}" # Fallback
