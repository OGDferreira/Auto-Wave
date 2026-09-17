import asyncio
import json
import os
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Account,
    AsyncSessionLocal,
    PushSubscription,
    SharkbotEvent,
    SystemConfig,
    init_db,
    session_dependency,
)


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


class ConfigPayload(BaseModel):
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""


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
    await init_db()


@app.get("/")
async def home(request: Request, db: AsyncSession = Depends(session_dependency)):
    metrics = await get_dashboard_metrics(db)
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


@app.post("/api/config")
async def post_config(payload: ConfigPayload, db: AsyncSession = Depends(session_dependency)):
    config = await get_or_create_system_config(db)
    config.meta_app_id = payload.meta_app_id.strip()
    config.meta_app_secret = payload.meta_app_secret.strip()
    config.meta_webhook_verify_token = payload.meta_webhook_verify_token.strip()
    config.vapid_public_key = payload.vapid_public_key.strip()
    config.vapid_private_key = payload.vapid_private_key.strip()
    await db.commit()
    await db.refresh(config)
    return {"status": "ok", "config": await get_config(db)}


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
        form_data = await request.form()
        content = str(form_data.get("contas", ""))
    except Exception:
        content = (await request.body()).decode("utf-8", errors="ignore")

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
