from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from loguru import logger
import threading
import subprocess
import uvicorn

from app.services.claudeflare import connect_claudeflare
from app.services.telegram_engine import run_telegram
from app.core.settings import settings
from app.api.sound import sound
from app.api.hiro import hiro

root_info = logger.bind(module="root")


def connect_api():
    uvicorn.run(
        app="app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=False  # Thread içinde reload çalışmaz
    )


# arka plan engine'leri: scheduler (60sn tick) ve notifier (10sn drain)
def start_scheduler():
    subprocess.run(["python", "app/services/schedules_engine/scheduler.py", "run"])


def start_notifier():
    subprocess.run(["python", "app/services/notification_engine/notifier.py", "run"])


app = FastAPI(
    title=settings.app.name,
    version=str(settings.app.version),
    description=settings.app.description
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors.allow_origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)

app.include_router(sound)
app.include_router(hiro)


@app.get("/")
def root():
    return {
        "status": "healthy",
        "name": settings.app.name,
        "description": settings.app.description,
        "version": settings.app.version
    }


if __name__ == "__main__":
    threading.Thread(target=connect_api, daemon=True).start()
    root_info.info("API initiazlized")

    # arka plan engine'leri
    threading.Thread(target=start_scheduler, daemon=True).start()
    threading.Thread(target=start_notifier, daemon=True).start()

    # Telegram giriş kanalı (senden Hiro'ya yazma)
    threading.Thread(target=run_telegram, daemon=True).start()
    root_info.info("engines + telegram started")

    connect_claudeflare()