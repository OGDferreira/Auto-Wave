import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import base64
import binascii
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Account,
    AsyncSessionLocal,
    PushSubscription,
    ScheduledPost,
    SharkbotEvent,
    SystemConfig,
    User,
    init_db,
    database_target,
    engine,
    session_dependency,
)

logger = logging.getLogger("auto_wave")

app = FastAPI(title="Auto-Wave", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.middleware("http")
async def require_panel_login(request: Request, call_next):
    protected_path = request.url.path.startswith("/api/") or request.url.path.startswith("/contas")
    if protected_path and request.url.path not in {"/api/auth/login", "/api/health/db"}:
        async with AsyncSessionLocal() as db:
            user = await current_user(request, db)
            if user is None:
                return JSONResponse({"detail": "Faça login para acessar o painel."}, status_code=401)
            collaborator_paths = {
                "/api/auth/me",
                "/api/auth/logout",
                "/api/contas",
                "/api/push/subscribe",
            }
            is_account_route = request.url.path.startswith("/contas")
            if not user.is_owner and request.url.path not in collaborator_paths and not is_account_route:
                return JSONResponse({"detail": "Colaboradores têm acesso somente ao Hub de contas."}, status_code=403)
    return await call_next(request)


class ConfigPayload(BaseModel):
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""


class SchedulePayload(BaseModel):
    account_id: int
    media_url: str
    caption: str = ""
    scheduled_for: datetime


class LoginPayload(BaseModel):
    username: str
    password: str


class CollaboratorPayload(BaseModel):
    username: str
    password: str


META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0")
META_REDIRECT_URI = os.getenv(
    "META_REDIRECT_URI",
    "https://auto-wave.onrender.com/auth/callback",
)
META_SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
]
RENDER_API_BASE_URL = os.getenv("RENDER_API_BASE_URL", "https://api.render.com/v1").rstrip("/")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "").strip()
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "").strip()
AUTH_SECRET = os.getenv("AUTH_SECRET", "auto-wave-change-this-secret").encode()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        _, salt_text, digest_text = encoded.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, binascii.Error):
        return False


def auth_cookie(user_id: int) -> str:
    value = str(user_id)
    signature = hmac.new(AUTH_SECRET, value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{signature}"


async def current_user(request: Request, db: AsyncSession) -> User | None:
    raw = request.cookies.get("auto_wave_session", "")
    user_id, separator, signature = raw.partition(".")
    if not separator or not user_id or not hmac.compare_digest(
        signature, hmac.new(AUTH_SECRET, user_id.encode(), hashlib.sha256).hexdigest()
    ):
        return None
    try:
        user = await db.get(User, int(user_id))
    except (ValueError, TypeError):
        return None
    return user if user and user.active else None


def serialize_config(config: SystemConfig) -> dict[str, str]:
    return {
        "meta_app_id": config.meta_app_id or "",
        "meta_app_secret": config.meta_app_secret or "",
        "meta_webhook_verify_token": config.meta_webhook_verify_token or "",
        "vapid_public_key": config.vapid_public_key or "",
        "vapid_private_key": config.vapid_private_key or "",
    }


async def get_or_create_system_config(db: AsyncSession) -> SystemConfig:
    result = await db.execute(select(SystemConfig).order_by(SystemConfig.id.asc()))
    config = result.scalars().first()
    if config is None:
        config = SystemConfig(
            meta_app_id="",
            meta_app_secret="",
            meta_webhook_verify_token="",
            vapid_public_key="",
            vapid_private_key="",
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


async def get_dashboard_metrics(db: AsyncSession) -> dict[str, Any]:
    views_total = await db.scalar(select(func.coalesce(func.sum(Account.views_count), 0))) or 0
    leads_total = await db.scalar(select(func.coalesce(func.sum(Account.leads_count), 0))) or 0

    sharkbot_rows = (
        await db.execute(
            select(SharkbotEvent.event_type, func.count(SharkbotEvent.id), func.coalesce(func.sum(SharkbotEvent.valor), 0.0))
            .group_by(SharkbotEvent.event_type)
        )
    ).all()

    metric_map = {row[0]: {"count": row[1], "valor": float(row[2] or 0.0)} for row in sharkbot_rows}
    lead_events = int(metric_map.get("lead", {}).get("count", 0))
    pix_gerado = int(metric_map.get("pix_gerado", {}).get("count", 0))
    pix_pago = int(metric_map.get("pix_pago", {}).get("count", 0))
    valor_total = float(sum(item["valor"] for item in metric_map.values()))

    def safe_rate(part: float, total: float) -> float:
        if total in (None, 0):
            return 0.0
        return round((part / total) * 100, 2)

    return {
        "views_total": int(views_total),
        "leads_total": int(leads_total),
        "lead_events": lead_events,
        "pix_gerado": pix_gerado,
        "pix_pago": pix_pago,
        "valor_total": round(valor_total, 2),
        "lead_rate": safe_rate(lead_events, views_total),
        "pix_rate": safe_rate(pix_pago, pix_gerado),
        "overall_conversion": safe_rate(leads_total, views_total),
        "accounts_total": await db.scalar(select(func.count(Account.id))) or 0,
    }


async def get_all_accounts(db: AsyncSession):
    result = await db.execute(select(Account).order_by(Account.created_at.desc()))
    return result.scalars().all()


async def send_push_notification(payload: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as db:
        config = await get_or_create_system_config(db)
        if not config.vapid_public_key or not config.vapid_private_key:
            return

        subscriptions = (await db.execute(select(PushSubscription))).scalars().all()
        if not subscriptions:
            return

        data = json.dumps(payload)
        for sub in subscriptions:
            try:
                from pywebpush import webpush

                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                    data=data,
                    vapid_private_key=config.vapid_private_key,
                    vapid_claims={"sub": "mailto:contato@auto-wave.app"},
                )
            except Exception:
                continue


async def run_playwright_login(account_id: int) -> None:
    async with AsyncSessionLocal() as db:
        account = await db.get(Account, account_id)
        if account is None:
            return

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto("https://www.instagram.com/accounts/login/")
                await page.fill('input[name="username"]', account.username)
                await page.fill('input[name="password"]', account.password)
                await page.click('button[type="submit"]')
                await page.wait_for_timeout(5000)
                await browser.close()

            account.meta_access_token = f"meta_{account.username}_{int(time.time())}"
            account.status = "conectada"
            await db.commit()
        except Exception:
            account.status = "suspensa"
            await db.commit()


@app.on_event("startup")
async def startup_event() -> None:
    try:
        await init_db()
        async with AsyncSessionLocal() as db:
            owner_username = os.getenv("ADMIN_USERNAME", "").strip()
            owner_password = os.getenv("ADMIN_PASSWORD", "")
            if owner_username and owner_password:
                existing = await db.scalar(select(User).where(User.username == owner_username))
                if existing is None:
                    db.add(User(username=owner_username, password_hash=password_hash(owner_password), is_owner=True))
                    await db.commit()
        logger.info("Database initialized successfully")
    except Exception:
        logger.exception("Database initialization failed; application started without database access")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(
        """<!doctype html><html lang="pt-BR"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Entrar · Auto-Wave</title><style>body{margin:0;background:#050505;color:#f5f5f5;font:16px Segoe UI;display:grid;place-items:center;min-height:100vh}form{width:min(360px,calc(100% - 40px));padding:28px;background:#111;border:1px solid #35205a;border-radius:18px;box-shadow:0 0 30px #8b5cf633}h1{margin-top:0}input,button{width:100%;padding:13px;margin:8px 0;border-radius:10px;border:1px solid #444;background:#080808;color:#fff;box-sizing:border-box}button{background:#8b5cf6;border:0;font-weight:700;cursor:pointer}#error{color:#f87171;min-height:22px}</style>
        <form id="login"><h1>Auto-Wave</h1><p>Acesse seu painel</p><input name="username" placeholder="Usuário" autocomplete="username" required><input name="password" type="password" placeholder="Senha" autocomplete="current-password" required><button>Entrar</button><div id="error"></div></form>
        <script>document.querySelector('#login').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.get('username'),password:f.get('password')})});if(r.ok)location.href='/';else document.querySelector('#error').textContent=(await r.json()).detail||'Falha ao entrar'};</script></html>"""
    )


@app.post("/api/auth/login")
async def login(payload: LoginPayload, response: Response, db: AsyncSession = Depends(session_dependency)):
    user = await db.scalar(select(User).where(User.username == payload.username.strip()))
    if user is None or not password_matches(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
    response.set_cookie("auto_wave_session", auth_cookie(user.id), httponly=True, secure=True, samesite="lax", max_age=86400 * 7)
    return {"status": "ok", "role": "owner" if user.is_owner else "collaborator"}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("auto_wave_session")
    return {"status": "ok"}


@app.get("/api/auth/me")
async def auth_me(request: Request, db: AsyncSession = Depends(session_dependency)):
    user = await current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Faça login.")
    return {"username": user.username, "role": "owner" if user.is_owner else "collaborator"}


@app.post("/api/auth/collaborators")
async def create_collaborator(payload: CollaboratorPayload, request: Request, db: AsyncSession = Depends(session_dependency)):
    owner = await current_user(request, db)
    if owner is None or not owner.is_owner:
        raise HTTPException(status_code=403, detail="Somente o proprietário pode criar colaboradores.")
    username = payload.username.strip()
    if len(username) < 3 or len(payload.password) < 8:
        raise HTTPException(status_code=422, detail="Usuário deve ter 3 caracteres e senha pelo menos 8.")
    if await db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=409, detail="Esse usuário já existe.")
    db.add(User(username=username, password_hash=password_hash(payload.password), is_owner=False))
    await db.commit()
    return {"status": "ok", "username": username}


@app.get("/")
async def home(request: Request):
    metrics = {
        "views_total": 0,
        "leads_total": 0,
        "lead_events": 0,
        "pix_gerado": 0,
        "pix_pago": 0,
        "valor_total": 0.0,
        "lead_rate": 0.0,
        "pix_rate": 0.0,
        "overall_conversion": 0.0,
        "accounts_total": 0,
    }
    try:
        async with AsyncSessionLocal() as db:
            metrics = await get_dashboard_metrics(db)
    except Exception:
        logger.exception("Dashboard metrics unavailable")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "initial_metrics": metrics,
        },
    )


@app.get("/api/config")
async def get_config(db: AsyncSession = Depends(session_dependency)):
    config = await get_or_create_system_config(db)
    return {
        "meta_app_id": config.meta_app_id,
        "meta_app_secret": config.meta_app_secret,
        "meta_webhook_verify_token": config.meta_webhook_verify_token,
        "vapid_public_key": config.vapid_public_key,
        "vapid_private_key": config.vapid_private_key,
    }


@app.get("/api/health/db")
async def database_health():
    try:
        async with engine.connect() as connection:
            await connection.run_sync(lambda sync_connection: sync_connection.exec_driver_sql("SELECT 1"))
        return {"status": "ok", "database": database_target()}
    except Exception as exc:
        logger.exception("Database health check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": database_target(),
                "message": "Não foi possível conectar ao PostgreSQL. Verifique host, porta, senha e acesso de rede do Supabase.",
                "error_type": type(exc).__name__,
            },
        )


@app.post("/api/config")
async def post_config(payload: ConfigPayload, db: AsyncSession = Depends(session_dependency)):
    try:
        config = await get_or_create_system_config(db)
        config.meta_app_id = payload.meta_app_id.strip()
        config.meta_app_secret = payload.meta_app_secret.strip()
        config.meta_webhook_verify_token = payload.meta_webhook_verify_token.strip()
        config.vapid_public_key = payload.vapid_public_key.strip()
        config.vapid_private_key = payload.vapid_private_key.strip()
        await db.commit()
        await db.refresh(config)
        return {"status": "ok", "config": serialize_config(config)}
    except Exception as exc:
        await db.rollback()
        logger.exception("Could not save system configuration")
        raise HTTPException(
            status_code=503,
            detail="Não foi possível salvar as chaves: o banco de dados está indisponível. Verifique DATABASE_URL e o status do Supabase.",
        ) from exc


@app.get("/auth/login")
@app.get("/auth/meta/login")
async def meta_login(
    request: Request,
    db: AsyncSession = Depends(session_dependency),
):
    config = await get_or_create_system_config(db)
    if not config.meta_app_id:
        raise HTTPException(status_code=503, detail="Configure o Meta App ID antes do login.")

    account_id = request.query_params.get("account_id", "")
    if account_id:
        try:
            account_id_value = int(account_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="ID de conta inválido.") from exc
        account = await db.get(Account, account_id_value)
        if account is None:
            raise HTTPException(status_code=404, detail="Conta não encontrada.")
    state_payload = f"{secrets.token_urlsafe(24)}.{int(time.time())}.{account_id}"
    state_signature = hmac.new(
        (config.meta_app_secret or "").encode(),
        state_payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    state = f"{state_payload}.{state_signature}"
    query = urlencode(
        {
            "client_id": config.meta_app_id,
            "redirect_uri": META_REDIRECT_URI,
            "state": state,
            "response_type": "code",
            "scope": ",".join(META_SCOPES),
        }
    )
    from fastapi.responses import RedirectResponse

    return RedirectResponse(f"https://www.facebook.com/{META_GRAPH_VERSION}/dialog/oauth?{query}")


@app.get("/auth/callback")
@app.get("/auth/meta/callback")
async def meta_callback(
    request: Request,
    db: AsyncSession = Depends(session_dependency),
):
    error = request.query_params.get("error")
    if error:
        raise HTTPException(
            status_code=400,
            detail=request.query_params.get("error_description") or error,
        )

    code = request.query_params.get("code")
    state = request.query_params.get("state", "")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Resposta OAuth sem code ou state.")

    config = await get_or_create_system_config(db)
    state_parts = state.rsplit(".", 3)
    if len(state_parts) != 4:
        raise HTTPException(status_code=400, detail="State OAuth inválido.")
    state_payload = ".".join(state_parts[:3])
    expected_signature = hmac.new(
        (config.meta_app_secret or "").encode(),
        state_payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(state_parts[2], expected_signature):
        raise HTTPException(status_code=400, detail="State OAuth inválido.")

    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.get(
            f"https://graph.facebook.com/{META_GRAPH_VERSION}/oauth/access_token",
            params={
                "client_id": config.meta_app_id,
                "client_secret": config.meta_app_secret,
                "redirect_uri": META_REDIRECT_URI,
                "code": code,
            },
        )
    if token_response.is_error:
        logger.error("Meta OAuth token exchange failed: %s", token_response.text)
        raise HTTPException(status_code=502, detail="A Meta recusou o código OAuth.")

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="A Meta não retornou um access token.")

    account_id = state_parts[2]
    if account_id:
        account = await db.get(Account, int(account_id))
        if account is not None:
            account.meta_access_token = access_token
            account.status = "conectada"
            await db.commit()

    from fastapi.responses import RedirectResponse

    return RedirectResponse("/#contas?connected=1")


@app.get("/webhook/meta")
async def verify_meta_webhook(request: Request, db: AsyncSession = Depends(session_dependency)):
    configured_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "").strip()
    if not configured_token:
        try:
            config = await get_or_create_system_config(db)
            configured_token = config.meta_webhook_verify_token or ""
        except Exception:
            logger.exception("Could not load Meta webhook verification token")
            raise HTTPException(status_code=503, detail="Banco indisponível para validar o webhook.")
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and hmac.compare_digest(
            params.get("hub.verify_token", ""),
            configured_token,
        )
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Token de verificação inválido.")


@app.post("/webhook/meta")
async def receive_meta_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored", "message": "payload inválido"}, status_code=200)
    logger.info("Meta webhook received: %s", json.dumps(payload, ensure_ascii=False))
    return JSONResponse({"status": "ok"}, status_code=200)


@app.api_route("/deletar-dados", methods=["GET", "POST"])
async def delete_data(request: Request):
    payload = {}
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    logger.info("Data deletion request received: %s", payload)
    return JSONResponse(
        {
            "status": "received",
            "message": "Solicitação de exclusão recebida. O usuário será contatado para confirmação.",
        }
    )


@app.get("/privacidade")
async def privacy_policy():
    return JSONResponse(
        {
            "title": "Política de Privacidade - Auto-Wave",
            "message": "Esta página descreve o tratamento de dados da plataforma Auto-Wave.",
        }
    )


@app.get("/termos")
async def terms_of_service():
    return JSONResponse(
        {
            "title": "Termos de Serviço - Auto-Wave",
            "message": "O uso do Auto-Wave depende da aceitação dos termos aplicáveis.",
        }
    )


@app.get("/api/contas")
async def list_accounts(db: AsyncSession = Depends(session_dependency)):
    accounts = await get_all_accounts(db)
    return [
        {
            "id": account.id,
            "username": account.username,
            "status": account.status,
            "meta_access_token": account.meta_access_token,
            "views_count": account.views_count,
            "leads_count": account.leads_count,
            "created_at": account.created_at.isoformat() if account.created_at else None,
        }
        for account in accounts
    ]


@app.post("/contas/importar")
async def importar_contas(request: Request, db: AsyncSession = Depends(session_dependency)):
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
            content = str(payload.get("contas", ""))
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form_data = await request.form()
            content = str(form_data.get("contas", ""))
        else:
            content = (await request.body()).decode("utf-8", errors="ignore")
    except Exception:
        content = (await request.body()).decode("utf-8", errors="ignore")

    if "contas=" in content:
        from urllib.parse import parse_qs

        content = parse_qs(content).get("contas", [content])[0]

    created = 0
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ";" not in line:
            continue
        username, password = [part.strip() for part in line.split(";", 1)]
        if not username:
            continue
        existing = await db.execute(select(Account).where(Account.username == username))
        if existing.scalars().first() is not None:
            continue
        account = Account(username=username, password=password, status="pendente")
        db.add(account)
        created += 1

    await db.commit()
    return {"status": "ok", "created": created}


@app.get("/api/fila")
async def list_scheduled_posts(db: AsyncSession = Depends(session_dependency)):
    rows = (await db.execute(select(ScheduledPost).order_by(ScheduledPost.scheduled_for.asc()))).scalars().all()
    return [
        {
            "id": row.id,
            "account_id": row.account_id,
            "media_url": row.media_url,
            "caption": row.caption,
            "scheduled_for": row.scheduled_for.isoformat(),
            "status": row.status,
        }
        for row in rows
    ]


@app.post("/api/fila/agendar")
async def schedule_post(payload: SchedulePayload, db: AsyncSession = Depends(session_dependency)):
    account = await db.get(Account, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    if not payload.media_url.strip():
        raise HTTPException(status_code=400, detail="Informe a URL da mídia")

    post = ScheduledPost(
        account_id=payload.account_id,
        media_url=payload.media_url.strip(),
        caption=payload.caption.strip(),
        scheduled_for=payload.scheduled_for,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return {"status": "ok", "id": post.id}


@app.delete("/api/fila/{post_id}")
async def delete_scheduled_post(post_id: int, db: AsyncSession = Depends(session_dependency)):
    post = await db.get(ScheduledPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post não encontrado")
    await db.delete(post)
    await db.commit()
    return {"status": "ok"}


@app.post("/contas/{account_id}/conectar")
async def conectar_conta(account_id: int, db: AsyncSession = Depends(session_dependency)):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Conta não encontrada")

    asyncio.create_task(run_playwright_login(account_id))
    return {"status": "queued", "account_id": account_id}


@app.post("/webhook/sharkbot")
async def webhook_sharkbot(request: Request, db: AsyncSession = Depends(session_dependency)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored", "message": "payload inválido"}, status_code=200)

    if not isinstance(payload, dict):
        return JSONResponse({"status": "ignored", "message": "payload inválido"}, status_code=200)

    event_type = str(payload.get("event") or payload.get("tipo") or payload.get("name") or "lead")
    valor = payload.get("valor", 0)
    try:
        valor_float = float(valor)
    except (TypeError, ValueError):
        valor_float = 0.0

    event = SharkbotEvent(
        event_type=event_type,
        valor=valor_float,
        raw_payload=payload,
    )
    db.add(event)
    await db.commit()
    return JSONResponse({"status": "ok", "event": event_type}, status_code=200)


@app.get("/api/metricas")
async def metricas(db: AsyncSession = Depends(session_dependency)):
    return await get_dashboard_metrics(db)


@app.get("/api/logs")
async def render_logs():
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        raise HTTPException(
            status_code=503,
            detail="Logs do Render não configurados. Defina RENDER_API_KEY e RENDER_SERVICE_ID.",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{RENDER_API_BASE_URL}/logs",
                headers={"Authorization": f"Bearer {RENDER_API_KEY}"},
                params={"resource": RENDER_SERVICE_ID, "limit": 100},
            )
        if response.is_error:
            logger.error("Render logs API returned HTTP %s", response.status_code)
            raise HTTPException(
                status_code=502,
                detail=f"A API do Render recusou a consulta de logs (HTTP {response.status_code}).",
            )

        payload = response.json()
        raw_logs = payload.get("logs", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_logs, list):
            raw_logs = []

        logs = []
        for item in raw_logs:
            if isinstance(item, str):
                logs.append(item)
                continue
            if isinstance(item, dict):
                message = item.get("message") or item.get("text") or item.get("log") or json.dumps(item)
                timestamp = item.get("timestamp") or item.get("time") or item.get("createdAt")
                logs.append(f"[{timestamp}] {message}" if timestamp else str(message))

        return {"logs": logs[-100:], "source": "render"}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Could not retrieve logs from Render")
        raise HTTPException(status_code=502, detail="Não foi possível consultar os logs do Render.") from exc


@app.post("/api/push/subscribe")
async def subscribe_push(request: Request, db: AsyncSession = Depends(session_dependency)):
    payload = await request.json()
    endpoint = payload.get("endpoint")
    p256dh = payload.get("keys", {}).get("p256dh")
    auth = payload.get("keys", {}).get("auth")

    if not endpoint or not p256dh or not auth:
        return JSONResponse({"status": "error", "message": "Dados de subscrição inválidos"}, status_code=400)

    existing = await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if existing.scalars().first() is not None:
        return {"status": "ok", "message": "Subscription already registered"}

    sub = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth)
    db.add(sub)
    await db.commit()
    return {"status": "ok"}


@app.post("/api/push/send")
async def send_push(payload: dict[str, Any], db: AsyncSession = Depends(session_dependency)):
    title = str(payload.get("title") or "Auto-Wave")
    body = str(payload.get("body") or "Atualização do dashboard")
    await send_push_notification({"title": title, "body": body, "url": "/"})
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)
