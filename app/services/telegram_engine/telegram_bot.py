"""
telegram_bot.py — Hiro Telegram giriş kanalı (çift yön)
═══════════════════════════════════════════════════════════════════════════
notifier.py Hiro'dan SANA yazar (tek yön). Bu ise SENDEN Hiro'ya yazmanı sağlar:
Telegram'a yazdığın mesajı alır → Hiro'ya (chat) verir → cevabı Telegram'a döner.
Böylece hem saatten hem Telegram'dan konuşabilirsin.

python-telegram-bot (>=22) kullanır. TELEGRAM_BOT token gerekli.
main.py içinde ayrı bir thread olarak başlatılır (wakeword/scheduler gibi).
"""

import os
from dotenv import load_dotenv
from loguru import logger
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.services.hiro_core import build_agent, chat

load_dotenv()

tg_log = logger.bind(module="telegram_bot")

TELEGRAM_BOT = os.getenv("TELEGRAM_BOT")
CHAT_ID = os.getenv("CHAT_ID")

# kendi agent örneği (thread'de çalışır, chat endpoint'inden bağımsız)
_agent = build_agent()


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # sadece senin chat'inden gelenleri işle (başkası botu bulsa bile cevap verme)
    if CHAT_ID and str(update.effective_chat.id) != str(CHAT_ID):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    tg_log.info(f"telegram in: {text[:60]}")
    try:
        reply = chat(_agent, text)
    except Exception as e:
        reply = f"Hata: {e}"
    await update.message.reply_text(reply)


def run_telegram():
    """Telegram bot'unu başlat (polling). Ayrı thread'den çağrılır."""
    if not TELEGRAM_BOT:
        tg_log.warning("TELEGRAM_BOT yok, telegram bot başlatılmadı")
        return
    app = Application.builder().token(TELEGRAM_BOT).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    tg_log.info("Telegram bot started (polling)")
    # kendi event loop'unu kurar; thread içinde çalışması için
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    # drop_pending_updates: başlangıçta birikmiş eski mesajları atla (geç işleme sorunu)
    app.run_polling(close_loop=False, stop_signals=None, drop_pending_updates=True)


if __name__ == "__main__":
    run_telegram()