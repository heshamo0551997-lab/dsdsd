from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete, func, and_, or_, text
from typing import List, Optional, Any
import uuid
import zipfile
import tarfile
import io
import jwt
import bcrypt as bcrypt_lib
from datetime import datetime, timedelta
import os
import random
import string

from ..core.db import get_db
from ..models.all_models import (
    AdminUser, User, MainAccount, Plan, RechargeOrder, RechargeCard,
    ProtocolAccount, SystemSetting, UserKeyword, AutoReply,
    ListenedUser, UserTargetChat, MonitoredGroup, GroupJoinQueue,
    LeadSoftware, ProtocolProfileSetting, InboxAutoReply, PrivateChatLog,
    ProtoCheckRequest, BotEvent
)
from pydantic import BaseModel

SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-key-for-admin-panel")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/admin/login")

app = FastAPI(title="TG Monitor API")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/admin", StaticFiles(directory=static_dir, html=True), name="static")

@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_root():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(static_dir, "dashboard.html"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def ok(data: Any = None):
    return {"code": 0, "data": data}

def fail(msg: str):
    return {"code": 1, "message": msg}

def verify_password(plain, hashed):
    try:
        return bcrypt_lib.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def hash_password(password: str) -> str:
    return bcrypt_lib.hashpw(password.encode('utf-8'), bcrypt_lib.gensalt()).decode('utf-8')

def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_admin(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    result = await db.execute(select(AdminUser).where(AdminUser.username == username))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return admin

def row_to_dict(obj):
    if obj is None:
        return None
    d = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if isinstance(v, datetime):
            d[c.name] = v.isoformat()
        else:
            d[c.name] = v
    return d

def rows_to_list(rows):
    return [row_to_dict(r) for r in rows]

# ─── Health ─────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

# ─── Root ───────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "TG Monitor API is running. Admin panel at /admin"}

# ─── OAuth2 login (form-data, legacy) ──────────────────────────────
@app.post("/api/admin/login")
async def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminUser).where(AdminUser.username == form_data.username))
    admin = result.scalar_one_or_none()
    if not admin or not verify_password(form_data.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(data={"sub": admin.username})
    return {"access_token": token, "token_type": "bearer"}

# ─── JSON login (Node.js admin panel style) ─────────────────────────
@app.post("/api/admin/auth/login")
async def login_json(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
    except Exception:
        return fail("请求格式错误")
    result = await db.execute(select(AdminUser).where(AdminUser.username == username))
    admin = result.scalar_one_or_none()
    if not admin or not verify_password(password, admin.password_hash):
        return fail("账号或密码错误")
    await db.execute(update(AdminUser).where(AdminUser.id == admin.id).values(last_login_at=func.now()))
    await db.commit()
    token = create_access_token(data={"sub": admin.username})
    return ok({"token": token, "nickname": admin.nickname})

# ─── Change password ────────────────────────────────────────────────
@app.post("/api/admin/auth/change-password")
async def change_password(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    old_password = body.get("old_password", "")
    new_password = body.get("new_password", "")
    if not verify_password(old_password, current_admin.password_hash):
        return fail("旧密码错误")
    await db.execute(update(AdminUser).where(AdminUser.id == current_admin.id).values(password_hash=hash_password(new_password)))
    await db.commit()
    return ok(None)

# ─── Dashboard ──────────────────────────────────────────────────────
@app.get("/api/admin/dashboard")
async def dashboard(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(func.count(User.id)))).scalar()
    groups = (await db.execute(select(func.count(MonitoredGroup.id)).where(MonitoredGroup.status == 1, MonitoredGroup.is_blocked == 0))).scalar()
    accounts = (await db.execute(select(func.count(MainAccount.id)))).scalar()
    keywords = (await db.execute(select(func.count(UserKeyword.id)).where(UserKeyword.status == 1))).scalar()
    target_chats = (await db.execute(select(func.count(UserTargetChat.id)))).scalar()
    pending_orders = (await db.execute(select(func.count(RechargeOrder.id)).where(RechargeOrder.status == "pending"))).scalar()
    join_queue = (await db.execute(select(func.count(GroupJoinQueue.id)).where(GroupJoinQueue.status == "pending"))).scalar()
    return ok({
        "users": users,
        "monitored_groups": groups,
        "main_accounts": accounts,
        "keywords": keywords,
        "target_chats": target_chats,
        "pending_orders": pending_orders,
        "join_queue": join_queue
    })

@app.get("/api/admin/stats")
async def get_admin_stats(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(func.count(User.id)))).scalar()
    protocols = (await db.execute(select(func.count(ProtocolAccount.id)))).scalar()
    orders = (await db.execute(select(func.count(RechargeOrder.id)))).scalar()
    hits = (await db.execute(select(func.count(ListenedUser.id)))).scalar()
    return {"users": users, "protocols": protocols, "orders": orders, "hits": hits}

# ─── User Management ────────────────────────────────────────────────
@app.get("/api/admin/users")
async def list_users(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.id.desc()))
    return ok(rows_to_list(result.scalars().all()))

@app.put("/api/admin/users/{user_id}/listen-status")
async def toggle_listen_status(user_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(User).where(User.id == user_id).values(listen_status=body.get("listen_status", 0), updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/admin/users/{user_id}/adjust-days")
async def adjust_days(user_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    days = int(body.get("days", 0))
    user = await db.get(User, user_id)
    if not user:
        return fail("用户不存在")
    now = datetime.now()
    base = user.expire_at if user.expire_at and user.expire_at > now else now
    user.expire_at = base + timedelta(days=days)
    user.updated_at = now
    await db.commit()
    return ok(None)

@app.post("/api/admin/users/{user_id}/adjust-balance")
async def adjust_balance(user_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    amount = float(body.get("amount", 0))
    await db.execute(update(User).where(User.id == user_id).values(balance=User.balance + amount, updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/admin/users/{user_id}/update")
async def update_user(user_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    data = await request.json()
    data.pop("id", None)
    await db.execute(update(User).where(User.id == user_id).values(**data, updated_at=func.now()))
    await db.commit()
    return ok(None)

# ─── Order Management ───────────────────────────────────────────────
@app.get("/api/admin/orders")
async def list_orders(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RechargeOrder, User.username).outerjoin(User, User.id == RechargeOrder.user_id).order_by(RechargeOrder.id.desc()).limit(200)
    )
    rows = []
    for order, uname in result.all():
        d = row_to_dict(order)
        d["username"] = uname or ""
        rows.append(d)
    return ok(rows)

@app.post("/api/admin/orders/{order_id}/confirm")
async def confirm_order(order_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    order = await db.get(RechargeOrder, order_id)
    if not order:
        return fail("订单不存在")
    if order.status == "paid":
        return fail("订单已入账")
    plan = await db.get(Plan, order.plan_id)
    if not plan:
        return fail("套餐不存在")
    user = await db.get(User, order.user_id)
    if user:
        now = datetime.now()
        base = user.expire_at if user.expire_at and user.expire_at > now else now
        user.expire_at = base + timedelta(days=plan.duration_days)
        user.plan_id = plan.id
        user.plan_name = f"{plan.plan_group}-{plan.name}"
        user.plan_keyword_limit = plan.keyword_limit
        user.notif_expire_soon = 0
        user.notif_expired = 0
        user.updated_at = now
    order.status = "paid"
    order.updated_at = datetime.now()
    await db.commit()
    return ok(None)

@app.post("/api/admin/orders/{order_id}/approve")
async def approve_order(order_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    return await confirm_order(order_id, current_admin, db)

# ─── Plan Management ────────────────────────────────────────────────
@app.get("/api/admin/plans")
async def list_plans(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Plan).order_by(Plan.sort_no.asc(), Plan.id.asc()))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/admin/plans")
async def create_plan(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    plan = Plan(
        plan_group=body.get("plan_group", "Normal"),
        name=body.get("name", ""),
        duration_days=int(body.get("duration_days", 30)),
        keyword_limit=int(body.get("keyword_limit", 0)),
        price=float(body.get("price", 0)),
        sort_no=int(body.get("sort_no", 0)),
        status=int(body.get("status", 1))
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return ok(row_to_dict(plan))

@app.put("/api/admin/plans/{plan_id}")
async def update_plan(plan_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(Plan).where(Plan.id == plan_id).values(
        plan_group=body.get("plan_group", "Normal"),
        name=body.get("name", ""),
        duration_days=int(body.get("duration_days", 30)),
        keyword_limit=int(body.get("keyword_limit", 0)),
        price=float(body.get("price", 0)),
        sort_no=int(body.get("sort_no", 0)),
        status=int(body.get("status", 1)),
        updated_at=func.now()
    ))
    await db.commit()
    return ok(None)

@app.delete("/api/admin/plans/{plan_id}")
async def delete_plan(plan_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Plan).where(Plan.id == plan_id))
    await db.commit()
    return ok(None)

@app.post("/api/admin/plans/save")
async def save_plan(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    plan_data = await request.json()
    if "id" in plan_data and plan_data["id"]:
        plan_id = plan_data.pop("id")
        await db.execute(update(Plan).where(Plan.id == plan_id).values(**plan_data, updated_at=func.now()))
    else:
        plan_data.pop("id", None)
        db.add(Plan(**plan_data))
    await db.commit()
    return ok(None)

# ─── Card Management ────────────────────────────────────────────────
@app.get("/api/admin/cards")
async def list_cards(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RechargeCard, Plan.name.label("plan_name"))
        .outerjoin(Plan, Plan.id == RechargeCard.plan_id)
        .order_by(RechargeCard.id.desc()).limit(500)
    )
    rows = []
    for card, plan_name in result.all():
        d = row_to_dict(card)
        d["plan_name"] = plan_name or ""
        rows.append(d)
    return ok(rows)

@app.post("/api/admin/cards/generate")
async def generate_cards(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    plan_id = body.get("plan_id")
    count = min(int(body.get("count", 1)), 100)
    prefix = body.get("prefix", "")
    if not plan_id:
        return fail("请选择套餐")
    plan = await db.get(Plan, int(plan_id))
    if not plan:
        return fail("套餐不存在")
    codes = []
    for _ in range(count):
        rand = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        suffix = hex(int(datetime.now().timestamp()))[2:].upper()[-4:]
        code = (f"{prefix}-" if prefix else "") + rand + "-" + suffix
        db.add(RechargeCard(card_code=code, plan_id=plan.id, status="unused"))
        codes.append(code)
    await db.commit()
    return ok({"codes": codes})

@app.delete("/api/admin/cards/{card_id}")
async def delete_card(card_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(RechargeCard).where(RechargeCard.id == card_id, RechargeCard.status == "unused"))
    await db.commit()
    return ok(None)

# ─── Main Account Management ────────────────────────────────────────
@app.get("/api/admin/main-accounts")
async def list_main_accounts(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MainAccount).order_by(MainAccount.id.asc()))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/admin/main-accounts")
async def create_main_account(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    if not body.get("name") or not body.get("api_id") or not body.get("api_hash"):
        return fail("名称、API_ID、API_HASH 不能为空")
    acc = MainAccount(
        name=body.get("name"),
        phone=body.get("phone", ""),
        session_name=body.get("session_name", ""),
        api_id=body.get("api_id"),
        api_hash=body.get("api_hash"),
        proxy_url=body.get("proxy_url", ""),
        remark=body.get("remark", "")
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return ok(row_to_dict(acc))

@app.put("/api/admin/main-accounts/{acc_id}")
async def update_main_account(acc_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(
        name=body.get("name"),
        phone=body.get("phone", ""),
        api_id=body.get("api_id"),
        api_hash=body.get("api_hash"),
        proxy_url=body.get("proxy_url", ""),
        remark=body.get("remark", ""),
        updated_at=func.now()
    ))
    await db.commit()
    return ok(None)

@app.delete("/api/admin/main-accounts/{acc_id}")
async def delete_main_account(acc_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(MainAccount).where(MainAccount.id == acc_id))
    await db.commit()
    return ok(None)

@app.post("/api/admin/main-accounts/{acc_id}/send-code")
async def send_login_code(acc_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(login_status="request_code", login_error="", updated_at=func.now()))
    await db.commit()
    return ok({"message": "已发送验证码请求，请稍候..."})

@app.post("/api/admin/main-accounts/{acc_id}/login")
async def submit_login(acc_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    code = body.get("code")
    password = body.get("password")
    if code:
        await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(login_status="submit_code", login_code_hash=code, updated_at=func.now()))
        await db.commit()
        return ok({"message": "验证码已提交，正在登录..."})
    if password:
        await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(login_status="submit_password", login_code_hash=password, updated_at=func.now()))
        await db.commit()
        return ok({"message": "2FA 密码已提交..."})
    return fail("参数错误")

@app.post("/api/admin/main-accounts/{acc_id}/login-reset")
async def reset_login(acc_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(login_status="idle", login_code_hash="", login_error="", updated_at=func.now()))
    await db.commit()
    return ok({"message": "状态已重置"})

@app.get("/api/admin/main-accounts/{acc_id}/login-status")
async def get_login_status(acc_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    acc = await db.get(MainAccount, acc_id)
    if not acc:
        return fail("账号不存在")
    return ok({"login_status": acc.login_status, "login_error": acc.login_error})

@app.post("/api/admin/main-accounts/{acc_id}/set-session")
async def set_session_string(acc_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    session_string = body.get("session_string", "").strip()
    if not session_string:
        return fail("Session 字符串不能为空")
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(session_name=session_string, updated_at=func.now()))
    await db.commit()
    return ok({"message": "Session 已保存", "length": len(session_string)})

@app.post("/api/admin/main-accounts/{acc_id}/upload-session")
async def upload_session_file(acc_id: int, file: UploadFile = File(...), current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    content = await file.read()
    fname = (file.filename or "").lower()
    session_str = ""

    if fname.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    nl = name.lower()
                    if nl.endswith(".session") or nl.endswith("session.txt") or nl.endswith(".txt"):
                        raw = z.read(name)
                        session_str = raw.decode("utf-8", errors="ignore").replace("\x00", "").strip()
                        if session_str:
                            break
        except Exception as e:
            return fail(f"ZIP 解析失败: {str(e)}")
    else:
        session_str = content.decode("utf-8", errors="ignore").replace("\x00", "").strip()

    if not session_str:
        return fail("未能从文件中提取 session 字符串")

    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(session_name=session_str, updated_at=func.now()))
    await db.commit()
    return ok({"message": "Session 已更新", "length": len(session_str)})

@app.post("/api/admin/main-accounts/{acc_id}/request-fetch-groups")
async def request_fetch_groups(acc_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(fetch_groups_requested=1, updated_at=func.now()))
    await db.commit()
    return ok({"message": "已发送获取群组请求，监听器将在下次轮询时自动执行"})

# ─── Monitored Groups ───────────────────────────────────────────────
@app.get("/api/admin/monitored-groups")
async def list_monitored_groups(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MonitoredGroup, MainAccount.name.label("main_account_name"))
        .outerjoin(MainAccount, MainAccount.id == MonitoredGroup.main_account_id)
        .order_by(MonitoredGroup.is_kicked.desc(), MonitoredGroup.is_blocked.asc(), MonitoredGroup.id.desc())
    )
    rows = []
    for grp, acc_name in result.all():
        d = row_to_dict(grp)
        d["main_account_name"] = acc_name or ""
        rows.append(d)
    return ok(rows)

@app.post("/api/admin/monitored-groups")
async def add_monitored_group(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    grp = MonitoredGroup(
        main_account_id=body.get("main_account_id"),
        telegram_group_id=int(body.get("telegram_group_id", 0)),
        group_title=body.get("group_title", ""),
        group_username=body.get("group_username", ""),
        is_blocked=0,
        status=int(body.get("status", 1))
    )
    db.add(grp)
    await db.commit()
    await db.refresh(grp)
    return ok(row_to_dict(grp))

@app.put("/api/admin/monitored-groups/{grp_id}/toggle-block")
async def toggle_group_block(grp_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    grp = await db.get(MonitoredGroup, grp_id)
    if not grp:
        return fail("记录不存在")
    new_blocked = 0 if grp.is_blocked else 1
    await db.execute(update(MonitoredGroup).where(MonitoredGroup.id == grp_id).values(is_blocked=new_blocked, updated_at=func.now()))
    await db.commit()
    return ok({"is_blocked": new_blocked})

@app.put("/api/admin/monitored-groups/{grp_id}/toggle-status")
async def toggle_group_status(grp_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    grp = await db.get(MonitoredGroup, grp_id)
    if not grp:
        return fail("记录不存在")
    new_status = 0 if grp.status else 1
    await db.execute(update(MonitoredGroup).where(MonitoredGroup.id == grp_id).values(status=new_status, updated_at=func.now()))
    await db.commit()
    return ok({"status": new_status})

@app.delete("/api/admin/monitored-groups/{grp_id}")
async def delete_monitored_group(grp_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(MonitoredGroup).where(MonitoredGroup.id == grp_id))
    await db.commit()
    return ok(None)

# ─── Join Queue ─────────────────────────────────────────────────────
@app.get("/api/admin/join-queue")
async def list_join_queue(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GroupJoinQueue, MainAccount.name.label("account_name"))
        .outerjoin(MainAccount, MainAccount.id == GroupJoinQueue.main_account_id)
        .order_by(GroupJoinQueue.id.desc()).limit(200)
    )
    rows = []
    for item, acc_name in result.all():
        d = row_to_dict(item)
        d["account_name"] = acc_name or ""
        rows.append(d)
    return ok(rows)

@app.post("/api/admin/join-queue")
async def add_join_queue(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    main_account_id = body.get("main_account_id")
    group_links = body.get("group_links", [])
    min_delay = int(body.get("min_delay_s", 300))
    max_delay = int(body.get("max_delay_s", 600))
    if not main_account_id:
        return fail("请选择主号")
    if not isinstance(group_links, list) or not group_links:
        return fail("请提供群组链接列表")
    if min_delay < 10:
        return fail("最小延迟不能小于10秒")
    if max_delay < min_delay:
        return fail("最大延迟不能小于最小延迟")
    count = 0
    for link in group_links:
        l = (link or "").strip()
        if not l:
            continue
        db.add(GroupJoinQueue(main_account_id=main_account_id, group_link=l, min_delay_s=min_delay, max_delay_s=max_delay, status="pending"))
        count += 1
    await db.commit()
    return ok({"added": count})

@app.delete("/api/admin/join-queue/{item_id}")
async def delete_join_queue_item(item_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(GroupJoinQueue).where(GroupJoinQueue.id == item_id))
    await db.commit()
    return ok(None)

@app.delete("/api/admin/join-queue")
async def clear_join_queue(status: Optional[str] = Query(None), current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    if status:
        await db.execute(delete(GroupJoinQueue).where(GroupJoinQueue.status == status))
    else:
        await db.execute(delete(GroupJoinQueue))
    await db.commit()
    return ok(None)

# ─── Listened Users (Hit Data) ──────────────────────────────────────
@app.get("/api/admin/listened-users")
async def list_listened_users(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ListenedUser, User.username.label("bot_user_username"), User.nickname.label("bot_user_nickname"))
        .outerjoin(User, User.id == ListenedUser.user_id)
        .order_by(ListenedUser.id.desc()).limit(500)
    )
    rows = []
    for item, uname, unick in result.all():
        d = row_to_dict(item)
        d["bot_user_username"] = uname or ""
        d["bot_user_nickname"] = unick or ""
        d["source_group_title"] = d.get("group_title", "")
        d["matched_keyword"] = d.get("keyword", "")
        d["raw_text"] = d.get("message_text", "")
        rows.append(d)
    return ok(rows)

@app.delete("/api/admin/listened-users")
async def clear_listened_users(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ListenedUser))
    await db.commit()
    return ok(None)

@app.get("/api/admin/listened-users/export")
async def export_listened_users(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ListenedUser).order_by(ListenedUser.id.desc()).limit(5000))
    rows = result.scalars().all()
    lines = []
    for r in rows:
        ts = r.created_at.isoformat() if r.created_at else ""
        lines.append(
            f"[{ts}] 群:{r.group_title} | 关键词:{r.keyword} | "
            f"用户:{'@' + r.sender_username if r.sender_username else r.sender_name} | "
            f"ID:{r.sender_id}\n内容: {r.message_text}"
        )
    return PlainTextResponse("\n\n---\n\n".join(lines), headers={"Content-Disposition": "attachment; filename=listened_users.txt"})

# ─── Private Chat Logs ──────────────────────────────────────────────
@app.get("/api/admin/chat-logs")
async def list_chat_logs(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PrivateChatLog).order_by(PrivateChatLog.id.desc()).limit(500))
    return ok(rows_to_list(result.scalars().all()))

@app.delete("/api/admin/chat-logs")
async def clear_chat_logs(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(PrivateChatLog))
    await db.commit()
    return ok(None)

@app.get("/api/admin/chat-logs/export")
async def export_chat_logs(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PrivateChatLog).order_by(PrivateChatLog.id.desc()).limit(5000))
    rows = result.scalars().all()
    lines = []
    for r in rows:
        ts = r.created_at.isoformat() if r.created_at else ""
        uname = f"@{r.sender_username}" if r.sender_username else r.sender_name
        lines.append(f"[{ts}] 用户: {uname}\n{r.message_text}")
    return PlainTextResponse("\n\n---\n\n".join(lines), headers={"Content-Disposition": "attachment; filename=chat_logs.txt"})

# ─── Lead Software ──────────────────────────────────────────────────
@app.get("/api/admin/lead-softwares")
async def list_lead_softwares(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LeadSoftware).order_by(LeadSoftware.sort_no.asc(), LeadSoftware.id.asc()))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/admin/lead-softwares")
async def create_lead_software(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    item = LeadSoftware(
        title=body.get("title", ""),
        url=body.get("url", ""),
        description=body.get("description", ""),
        sort_no=int(body.get("sort_no", 0)),
        status=int(body.get("status", 1))
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return ok(row_to_dict(item))

@app.put("/api/admin/lead-softwares/{item_id}")
async def update_lead_software(item_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(LeadSoftware).where(LeadSoftware.id == item_id).values(
        title=body.get("title", ""),
        url=body.get("url", ""),
        description=body.get("description", ""),
        sort_no=int(body.get("sort_no", 0)),
        status=int(body.get("status", 1)),
        updated_at=func.now()
    ))
    await db.commit()
    return ok(None)

@app.delete("/api/admin/lead-softwares/{item_id}")
async def delete_lead_software(item_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(LeadSoftware).where(LeadSoftware.id == item_id))
    await db.commit()
    return ok(None)

# ─── Protocol Accounts (Admin) ──────────────────────────────────────
@app.get("/api/admin/protocol-accounts")
async def admin_list_protocols(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ProtocolAccount, User.username.label("owner_username"))
        .outerjoin(User, User.id == ProtocolAccount.user_id)
        .order_by(ProtocolAccount.id.desc())
    )
    rows = []
    for acc, uname in result.all():
        d = row_to_dict(acc)
        d["owner_username"] = uname or ""
        d.pop("session_data", None)
        rows.append(d)
    return ok(rows)

@app.post("/api/admin/protocol-accounts/import")
async def admin_import_protocols(
    user_id: int = Form(...),
    proxy_url: str = Form(""),
    file: UploadFile = File(...),
    current_admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    content = await file.read()
    fname = (file.filename or "").lower()
    accounts = []

    if fname.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    nl = name.lower()
                    if nl.endswith(".session") or nl.endswith("session.txt"):
                        raw = z.read(name)
                        sd = raw.decode("utf-8", errors="ignore").replace("\x00", "").strip()
                        if sd:
                            phone = name.rsplit(".", 1)[0].replace("\\", "/").split("/")[-1]
                            accounts.append({"session_data": sd, "phone": phone})
        except Exception as e:
            return fail(f"ZIP 解析失败: {str(e)}")
    else:
        sd = content.decode("utf-8", errors="ignore").replace("\x00", "").strip()
        if sd:
            accounts.append({"session_data": sd, "phone": file.filename})

    if not accounts:
        return fail("未在文件中找到有效的 session 数据")

    for acc in accounts:
        db.add(ProtocolAccount(user_id=user_id, session_data=acc["session_data"], proxy_url=proxy_url, phone=acc.get("phone", "")))
    await db.commit()
    return ok({"imported": len(accounts)})

@app.put("/api/admin/protocol-accounts/{acc_id}/proxy")
async def update_protocol_proxy(acc_id: int, request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    proxy_url = body.get("proxy_url", "").strip()
    res = await db.execute(select(ProtocolAccount).where(ProtocolAccount.id == acc_id))
    acc = res.scalar_one_or_none()
    if not acc:
        return fail("Account not found")
    await db.execute(update(ProtocolAccount).where(ProtocolAccount.id == acc_id).values(proxy_url=proxy_url))
    await db.commit()
    return ok({"proxy_url": proxy_url})

@app.delete("/api/admin/protocol-accounts/{acc_id}")
async def admin_delete_protocol(acc_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ProtocolAccount).where(ProtocolAccount.id == acc_id))
    await db.commit()
    return ok(None)

@app.post("/api/admin/protocols/delete-inactive")
async def delete_inactive_protocols(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ProtocolAccount).where(ProtocolAccount.status == 0))
    await db.commit()
    return ok({"message": "已清除异常账号"})

# ─── Auto Replies (Admin) ───────────────────────────────────────────
@app.get("/api/admin/auto-replies")
async def admin_list_replies(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AutoReply, User.username.label("owner_username"))
        .outerjoin(User, User.id == AutoReply.user_id)
        .order_by(AutoReply.id.desc())
    )
    rows = []
    for reply, uname in result.all():
        d = row_to_dict(reply)
        d["owner_username"] = uname or ""
        rows.append(d)
    return ok(rows)

@app.post("/api/admin/auto-replies")
async def admin_create_reply(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    reply = AutoReply(
        user_id=body.get("user_id"),
        keyword=body.get("keyword", ""),
        reply_content=body.get("reply_content", "")
    )
    db.add(reply)
    await db.commit()
    await db.refresh(reply)
    return ok(row_to_dict(reply))

@app.delete("/api/admin/auto-replies/{reply_id}")
async def admin_delete_reply(reply_id: int, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(AutoReply).where(AutoReply.id == reply_id))
    await db.commit()
    return ok(None)

# ─── System Settings ────────────────────────────────────────────────
@app.get("/api/admin/settings")
async def get_settings(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting))
    settings = {s.setting_key: s.setting_value for s in result.scalars().all()}
    return settings

@app.get("/api/admin/system-settings")
async def get_system_settings_list(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting).order_by(SystemSetting.setting_key))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/admin/settings/update")
async def update_settings_legacy(settings: dict, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    for key, value in settings.items():
        res = await db.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
        if res.scalar_one_or_none():
            await db.execute(update(SystemSetting).where(SystemSetting.setting_key == key).values(setting_value=str(value), updated_at=func.now()))
        else:
            db.add(SystemSetting(setting_key=key, setting_value=str(value)))
    await db.commit()
    return ok(None)

@app.post("/api/admin/system-settings")
async def save_system_settings(request: Request, current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    items = body.get("items", [])
    if not isinstance(items, list):
        return fail("items 格式错误")
    for item in items:
        key = item.get("setting_key")
        val = item.get("setting_value", "")
        if not key:
            continue
        res = await db.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
        if res.scalar_one_or_none():
            await db.execute(update(SystemSetting).where(SystemSetting.setting_key == key).values(setting_value=str(val), updated_at=func.now()))
        else:
            db.add(SystemSetting(setting_key=key, setting_value=str(val), setting_type=item.get("setting_type", "string"), description=item.get("description", "")))
    await db.commit()
    return ok(None)

@app.post("/api/admin/system/restart")
async def system_restart(request: Request, current_admin: AdminUser = Depends(get_current_admin)):
    return ok({"message": "✅ 设置已保存，各服务将在下一轮轮询时自动生效。"})

@app.post("/api/admin/system/sync-db")
async def sync_db(current_admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    from ..core.init_db import init_db
    await init_db()
    return ok({"message": "✅ 数据库结构已同步完成"})

# ─── Internal Endpoints (for Listener) ─────────────────────────────
@app.get("/api/internal/login-request")
async def internal_get_login_request(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MainAccount).where(MainAccount.login_status.in_(["request_code", "submit_code", "submit_password"])).order_by(MainAccount.updated_at.asc()).limit(1)
    )
    acc = result.scalar_one_or_none()
    return ok(row_to_dict(acc))

@app.post("/api/internal/login-status-update")
async def internal_update_login_status(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    acc_id = body.get("id")
    login_status = body.get("login_status", "idle")
    login_error = body.get("login_error", "")
    session_name = body.get("session_name")
    values = {"login_status": login_status, "login_error": login_error, "updated_at": func.now()}
    if session_name:
        values["session_name"] = session_name
        values["online_status"] = 1
        values["run_status"] = 1
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(**values))
    await db.commit()
    return ok(None)

@app.get("/api/internal/protocols-for-reply/{user_id}")
async def internal_get_protocols_for_reply(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ProtocolAccount).where(ProtocolAccount.user_id == user_id).order_by(ProtocolAccount.outbound_count.asc(), ProtocolAccount.id.asc())
    )
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/internal/protocols-for-reply/{acc_id}/increment")
async def internal_increment_protocol(acc_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(update(ProtocolAccount).where(ProtocolAccount.id == acc_id).values(outbound_count=ProtocolAccount.outbound_count + 1, updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/internal/protocols-for-reply/{acc_id}/fail")
async def internal_fail_protocol(acc_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(update(ProtocolAccount).where(ProtocolAccount.id == acc_id).values(status=2, updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/internal/reset-reply-counts")
async def internal_reset_reply_counts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(update(ProtocolAccount).where(ProtocolAccount.status == 1).values(outbound_count=0, updated_at=func.now()).returning(ProtocolAccount.id))
    await db.commit()
    return ok({"reset": result.rowcount if hasattr(result, 'rowcount') else 0})

@app.get("/api/internal/check-hit")
async def internal_check_hit(user_id: int = Query(...), sender_id: int = Query(0), db: AsyncSession = Depends(get_db)):
    two_hours_ago = datetime.now() - timedelta(hours=2)
    result = await db.execute(
        select(ListenedUser).where(ListenedUser.user_id == user_id, ListenedUser.sender_id == sender_id, ListenedUser.created_at > two_hours_ago).limit(1)
    )
    return ok({"duplicate": result.scalar_one_or_none() is not None})

@app.post("/api/internal/hit")
async def internal_hit(request: Request, db: AsyncSession = Depends(get_db)):
    p = await request.json()
    two_hours_ago = datetime.now() - timedelta(hours=2)
    dup = await db.execute(
        select(ListenedUser).where(ListenedUser.user_id == p.get("user_id"), ListenedUser.sender_id == p.get("sender_id", 0), ListenedUser.created_at > two_hours_ago).limit(1)
    )
    if dup.scalar_one_or_none():
        return ok(None)
    hit = ListenedUser(
        user_id=p.get("user_id"),
        sender_id=p.get("sender_id"),
        sender_username=p.get("sender_username", ""),
        sender_name=p.get("sender_name", ""),
        group_id=p.get("source_group_id"),
        group_title=p.get("source_group_title", ""),
        keyword=p.get("matched_keyword", ""),
        message_text=p.get("raw_text", ""),
        protocol_account_id=p.get("protocol_account_id")
    )
    db.add(hit)
    await db.commit()
    return ok(None)

@app.get("/api/internal/user-tgid/{user_id}")
async def internal_get_user_tgid(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        return fail("用户不存在")
    return ok({"telegram_id": str(user.telegram_id)})

@app.get("/api/internal/inbox-reply-all")
async def internal_inbox_reply_all(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(InboxAutoReply).where(InboxAutoReply.is_enabled == 1))
    return ok(rows_to_list(result.scalars().all()))

@app.get("/api/internal/proto-profile-queue")
async def internal_proto_profile_queue(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProtocolProfileSetting).where(ProtocolProfileSetting.apply_requested.in_([1, 2])))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/internal/proto-profile-done/{user_id}")
async def internal_proto_profile_done(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    results = body.get("results", [])
    failed_phones = [r.get("phone") for r in results if not r.get("success")]
    retry_json = str(failed_phones) if failed_phones else None
    await db.execute(update(ProtocolProfileSetting).where(ProtocolProfileSetting.user_id == user_id).values(apply_requested=0, retry_phones=retry_json or "[]", updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.get("/api/internal/proto-check-queue")
async def internal_proto_check_queue(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProtoCheckRequest).where(ProtoCheckRequest.status == "pending").order_by(ProtoCheckRequest.created_at.asc()).limit(5))
    rows = []
    for req in result.scalars().all():
        rows.append({"req_id": req.id, "user_id": req.user_id})
    return ok(rows)

@app.post("/api/internal/proto-check-update/{account_id}")
async def internal_proto_check_update(account_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(ProtocolAccount).where(ProtocolAccount.id == account_id).values(status_label=body.get("status_label", "未知"), updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/internal/proto-check-done/{req_id}")
async def internal_proto_check_done(req_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await db.execute(update(ProtoCheckRequest).where(ProtoCheckRequest.id == req_id).values(status="done", updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.get("/api/internal/main-accounts-for-listener")
async def internal_main_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MainAccount).where(MainAccount.run_status == 1))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/internal/main-account-status")
async def internal_update_main_account_status(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    acc_id = body.get("id")
    online_status = body.get("online_status", 0)
    await db.execute(update(MainAccount).where(MainAccount.id == acc_id).values(online_status=online_status, updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/internal/sync-groups")
async def internal_sync_groups(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    main_account_id = body.get("main_account_id")
    groups = body.get("groups", [])
    for g in groups:
        existing = await db.execute(select(MonitoredGroup).where(MonitoredGroup.main_account_id == main_account_id, MonitoredGroup.telegram_group_id == g.get("telegram_group_id")))
        if not existing.scalar_one_or_none():
            db.add(MonitoredGroup(
                main_account_id=main_account_id,
                telegram_group_id=g.get("telegram_group_id"),
                group_title=g.get("group_title", ""),
                group_username=g.get("group_username", ""),
                status=1
            ))
    await db.execute(update(MainAccount).where(MainAccount.id == main_account_id).values(fetch_groups_requested=0, last_fetch_at=func.now(), updated_at=func.now()))
    await db.commit()
    return ok({"synced": len(groups)})

# ─── Bot Endpoints ──────────────────────────────────────────────────
@app.get("/api/bot/config")
async def get_bot_config(db: AsyncSession = Depends(get_db)):
    plans_res = await db.execute(select(Plan).where(Plan.status == 1).order_by(Plan.sort_no.asc(), Plan.id.asc()))
    settings_res = await db.execute(select(SystemSetting))
    leads_res = await db.execute(select(LeadSoftware).where(LeadSoftware.status == 1).order_by(LeadSoftware.sort_no.asc()))
    settings = {s.setting_key: s.setting_value for s in settings_res.scalars().all()}
    return ok({
        "plans": rows_to_list(plans_res.scalars().all()),
        "settings": settings,
        "leadSoftwares": rows_to_list(leads_res.scalars().all())
    })

@app.post("/api/bot/orders/create")
async def bot_create_order(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    telegram_id = body.get("telegram_id")
    plan_id = body.get("plan_id")
    if not telegram_id or not plan_id:
        return fail("参数不完整")
    plan = await db.get(Plan, int(plan_id))
    if not plan or not plan.status:
        return fail("套餐不存在")
    user_res = await db.execute(select(User).where(User.telegram_id == int(telegram_id)))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    addr_res = await db.execute(select(SystemSetting).where(SystemSetting.setting_key == "usdt_trc20_address"))
    addr_setting = addr_res.scalar_one_or_none()
    pay_address = addr_setting.setting_value if addr_setting else ""
    order_no = f"ORD{int(datetime.now().timestamp())}{uuid.uuid4().hex[:4].upper()}"
    base_price = float(plan.price)
    pending_res = await db.execute(select(func.count(RechargeOrder.id)).where(RechargeOrder.status == "pending", RechargeOrder.amount >= base_price, RechargeOrder.amount < base_price + 1))
    pending_count = pending_res.scalar()
    unique_amount = round(base_price + pending_count * 0.001, 3)
    order = RechargeOrder(
        order_no=order_no,
        user_id=user.id,
        plan_id=plan.id,
        plan_name=f"{plan.plan_group}-{plan.name}",
        amount=unique_amount,
        pay_address=pay_address,
        status="pending",
        expire_at=datetime.now() + timedelta(minutes=30)
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return ok(row_to_dict(order))

@app.post("/api/bot/redeem-card")
async def bot_redeem_card(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    telegram_id = body.get("telegram_id")
    card_code = (body.get("card_code") or "").strip()
    if not card_code:
        return fail("请输入卡密")
    user_res = await db.execute(select(User).where(User.telegram_id == int(telegram_id)))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    card_res = await db.execute(select(RechargeCard).where(RechargeCard.card_code == card_code, RechargeCard.status == "unused"))
    card = card_res.scalar_one_or_none()
    if not card:
        return fail("卡密无效或已使用")
    plan = await db.get(Plan, card.plan_id)
    if not plan:
        return fail("关联套餐不存在")
    now = datetime.now()
    base = user.expire_at if user.expire_at and user.expire_at > now else now
    user.expire_at = base + timedelta(days=plan.duration_days)
    user.plan_name = plan.name
    user.plan_keyword_limit = plan.keyword_limit
    user.notif_expire_soon = 0
    user.notif_expired = 0
    user.updated_at = now
    card.status = "used"
    card.used_by = user.id
    card.used_at = now
    db.add(RechargeOrder(
        user_id=user.id,
        order_no=f"CARD-{card.id}",
        plan_id=plan.id,
        plan_name=plan.name,
        amount=0,
        pay_address="card",
        status="paid",
        expire_at=datetime.now()
    ))
    await db.commit()
    return ok({"plan_name": plan.name})

@app.get("/api/bot/auto-replies/{telegram_id}")
async def bot_get_auto_replies(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    result = await db.execute(select(AutoReply).where(AutoReply.user_id == user.id).order_by(AutoReply.id.desc()))
    return ok(rows_to_list(result.scalars().all()))

@app.post("/api/bot/auto-replies/add")
async def bot_add_auto_reply(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    user_res = await db.execute(select(User).where(User.telegram_id == int(body.get("telegram_id", 0))))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    db.add(AutoReply(user_id=user.id, keyword=body.get("keyword", ""), reply_content=body.get("reply_content", "")))
    await db.commit()
    return ok(None)

@app.post("/api/bot/auto-replies/delete")
async def bot_delete_auto_reply(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    user_res = await db.execute(select(User).where(User.telegram_id == int(body.get("telegram_id", 0))))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    await db.execute(delete(AutoReply).where(AutoReply.id == int(body.get("id", 0)), AutoReply.user_id == user.id))
    await db.commit()
    return ok(None)

@app.post("/api/bot/auto-replies/clear")
async def bot_clear_auto_replies(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    user_res = await db.execute(select(User).where(User.telegram_id == int(body.get("telegram_id", 0))))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    await db.execute(delete(AutoReply).where(AutoReply.user_id == user.id))
    await db.commit()
    return ok(None)

@app.get("/api/bot/private-settings/{telegram_id}")
async def bot_get_private_settings(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    return ok({"is_enabled": user.private_status, "filter_non_pmable": user.privacy_filter_status})

@app.post("/api/bot/private-settings/toggle")
async def bot_toggle_private(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(User).where(User.telegram_id == int(body.get("telegram_id", 0))).values(private_status=int(body.get("is_enabled", 0)), updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.post("/api/bot/private-settings/toggle-filter")
async def bot_toggle_filter(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    await db.execute(update(User).where(User.telegram_id == int(body.get("telegram_id", 0))).values(privacy_filter_status=int(body.get("filter_non_pmable", 0)), updated_at=func.now()))
    await db.commit()
    return ok(None)

@app.get("/api/bot/protocol-accounts/{telegram_id}")
async def bot_get_protocol_accounts(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    result = await db.execute(select(ProtocolAccount).where(ProtocolAccount.user_id == user.id).order_by(ProtocolAccount.id.desc()))
    rows = []
    for acc in result.scalars().all():
        rows.append({"id": acc.id, "phone": acc.phone, "username": acc.username, "status": acc.status, "status_label": acc.status_label, "outbound_count": acc.outbound_count, "inbound_count": acc.inbound_count, "created_at": acc.created_at.isoformat() if acc.created_at else None})
    return ok(rows)

@app.delete("/api/bot/protocol-accounts/clear")
async def bot_clear_protocol_accounts(telegram_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    await db.execute(delete(ProtocolAccount).where(ProtocolAccount.user_id == user.id))
    await db.commit()
    return ok(None)

@app.post("/api/bot/protocol-accounts/import")
async def bot_import_protocol(telegram_id: int = Form(...), file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == int(telegram_id)))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    proxy_res = await db.execute(select(SystemSetting).where(SystemSetting.setting_key == "protocol_proxy_url"))
    proxy_setting = proxy_res.scalar_one_or_none()
    proxy_url = proxy_setting.setting_value if proxy_setting else ""
    content = await file.read()
    fname = (file.filename or "").lower()
    accounts = []
    if fname.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    nl = name.lower()
                    if nl.endswith(".session") or nl.endswith("session.txt"):
                        raw = z.read(name)
                        sd = raw.decode("utf-8", errors="ignore").replace("\x00", "").strip()
                        if sd:
                            phone = name.rsplit(".", 1)[0].replace("\\", "/").split("/")[-1]
                            accounts.append({"session_data": sd, "phone": phone})
        except Exception as e:
            return fail(f"ZIP 解析失败: {str(e)}")
    else:
        sd = content.decode("utf-8", errors="ignore").replace("\x00", "").strip()
        if sd:
            accounts.append({"session_data": sd, "phone": file.filename})
    for acc in accounts:
        db.add(ProtocolAccount(user_id=user.id, session_data=acc["session_data"], proxy_url=proxy_url, phone=acc.get("phone", "")))
    await db.commit()
    return ok({"imported": len(accounts)})

@app.get("/api/bot/proto-profile/{telegram_id}")
async def bot_get_proto_profile(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return ok({})
    result = await db.execute(select(ProtocolProfileSetting).where(ProtocolProfileSetting.user_id == user.id))
    ps = result.scalar_one_or_none()
    return ok(row_to_dict(ps) if ps else {})

@app.post("/api/bot/proto-profile/{telegram_id}")
async def bot_save_proto_profile(telegram_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    body = await request.json()
    result = await db.execute(select(ProtocolProfileSetting).where(ProtocolProfileSetting.user_id == user.id))
    ps = result.scalar_one_or_none()
    if not ps:
        ps = ProtocolProfileSetting(user_id=user.id)
        db.add(ps)
    for field in ["display_name", "usernames_txt", "bio", "photo_zip_file_id", "photo_file_id", "apply_requested", "retry_phones"]:
        if field in body and body[field] is not None:
            setattr(ps, field, body[field])
    ps.updated_at = datetime.now()
    await db.commit()
    return ok({"message": "资料已保存"})

@app.get("/api/bot/inbox-reply/{telegram_id}")
async def bot_get_inbox_reply(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return ok({})
    result = await db.execute(select(InboxAutoReply).where(InboxAutoReply.user_id == user.id))
    ir = result.scalar_one_or_none()
    return ok(row_to_dict(ir) if ir else {})

@app.post("/api/bot/inbox-reply/{telegram_id}")
async def bot_save_inbox_reply(telegram_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    body = await request.json()
    result = await db.execute(select(InboxAutoReply).where(InboxAutoReply.user_id == user.id))
    ir = result.scalar_one_or_none()
    if not ir:
        ir = InboxAutoReply(user_id=user.id)
        db.add(ir)
    for field in ["is_enabled", "reply_content", "reply_type", "image_file_id", "button_text"]:
        if field in body and body[field] is not None:
            setattr(ir, field, body[field])
    ir.updated_at = datetime.now()
    await db.commit()
    return ok({"message": "设置已保存"})

@app.post("/api/bot/proto-check/{telegram_id}")
async def bot_proto_check(telegram_id: int, db: AsyncSession = Depends(get_db)):
    user_res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return fail("用户不存在")
    db.add(ProtoCheckRequest(user_id=user.id, status="pending"))
    await db.commit()
    return ok(None)

@app.get("/api/bot/recharge-records/{telegram_id}")
async def bot_recharge_records(telegram_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RechargeOrder).join(User, User.id == RechargeOrder.user_id).where(User.telegram_id == telegram_id).order_by(RechargeOrder.id.desc()).limit(10)
    )
    return ok(rows_to_list(result.scalars().all()))

# ─── Bot Events (Notifications) ──────────────────────────────────────
@app.get("/api/admin/bot-events")
async def get_bot_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin)
):
    q = select(BotEvent).order_by(BotEvent.id.desc())
    if event_type:
        q = q.where(BotEvent.event_type == event_type)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    events = result.scalars().all()
    count_q = select(func.count()).select_from(BotEvent)
    total = (await db.execute(count_q)).scalar()
    unread_q = select(func.count()).select_from(BotEvent).where(
        BotEvent.created_at >= (datetime.utcnow() - timedelta(hours=1))
    )
    unread = (await db.execute(unread_q)).scalar()
    return ok({
        "events": rows_to_list(events),
        "total": total,
        "unread": unread
    })

@app.delete("/api/admin/bot-events")
async def clear_bot_events(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    await db.execute(delete(BotEvent))
    await db.commit()
    return ok({"message": "cleared"})

# ─── Realtime Stats ──────────────────────────────────────────────────
@app.get("/api/admin/realtime")
async def realtime_stats(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar()
    active_users = (await db.execute(select(func.count()).select_from(User).where(User.listen_status == 1))).scalar()
    pending_orders = (await db.execute(select(func.count()).select_from(RechargeOrder).where(RechargeOrder.status == "pending"))).scalar()
    main_accounts = (await db.execute(select(func.count()).select_from(MainAccount))).scalar()
    online_accounts = (await db.execute(select(func.count()).select_from(MainAccount).where(MainAccount.online_status == 1))).scalar()
    monitored_groups = (await db.execute(select(func.count()).select_from(MonitoredGroup))).scalar()
    today_events = (await db.execute(
        select(func.count()).select_from(BotEvent).where(
            BotEvent.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        )
    )).scalar()
    recent_events_q = await db.execute(
        select(BotEvent).order_by(BotEvent.id.desc()).limit(5)
    )
    recent_events = rows_to_list(recent_events_q.scalars().all())
    return ok({
        "total_users": total_users,
        "active_users": active_users,
        "pending_orders": pending_orders,
        "main_accounts": main_accounts,
        "online_accounts": online_accounts,
        "monitored_groups": monitored_groups,
        "today_events": today_events,
        "recent_events": recent_events,
        "server_time": datetime.utcnow().isoformat(),
        "status": "online"
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
