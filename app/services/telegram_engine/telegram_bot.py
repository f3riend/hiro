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
    # /reset — konuşma geçmişini temizle (yeni bağlamla başla)
    if text.lower() in ("/reset", "/temizle", "/yeni"):
        from app.services.hiro_core.conversation import clear_history
        clear_history(str(update.effective_chat.id))
        await update.message.reply_text("Konuşma geçmişi temizlendi, temiz başlıyoruz.")
        return
    tg_log.info(f"telegram in: {text[:60]}")
    # "yazıyor..." göstergesi — uzun işlemlerde kullanıcı beklediğini bilsin
    try:
        await update.message.chat.send_action("typing")
    except Exception:
        pass

    try:
        reply = chat(_agent, text, user_id=str(update.effective_chat.id))
    except Exception as e:
        tg_log.warning(f"chat hata: {e}")
        reply = f"Bir hata oldu: {str(e)[:200]}"

    # Telegram gönderimi — timeout'a dayanıklı: uzun mesajı böl + retry
    await _safe_reply(update, reply)


async def _safe_reply(update, text):
    """Telegram'a güvenli gönderim: 4000 karakter sınırı için böl, timeout'ta retry."""
    import asyncio
    if not text:
        text = "(boş cevap)"
    # Telegram mesaj sınırı ~4096 — uzun cevabı parçala
    chunks = [text[i:i+3800] for i in range(0, len(text), 3800)] or ["(boş)"]
    for chunk in chunks:
        for attempt in range(3):  # 3 deneme (timeout geçici olabilir)
            try:
                await update.message.reply_text(chunk)
                break
            except Exception as e:
                tg_log.warning(f"telegram gönderim denemesi {attempt+1} başarısız: {str(e)[:100]}")
                if attempt < 2:
                    await asyncio.sleep(2)  # bekle, tekrar dene
                # 3. denemede de olmazsa sessizce geç (log'da görünür)


def run_telegram():
    """Telegram bot'unu başlat (polling). Ayrı thread'den çağrılır."""
    if not TELEGRAM_BOT:
        tg_log.warning("TELEGRAM_BOT yok, telegram bot başlatılmadı")
        return
    app = (Application.builder()
           .token(TELEGRAM_BOT)
           .connect_timeout(30)      # bağlantı için 30sn (varsayılan düşüktü)
           .read_timeout(30)
           .write_timeout(30)
           .pool_timeout(30)
           .build())
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    tg_log.info("Telegram bot started (polling)")
    # kendi event loop'unu kurar; thread içinde çalışması için
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    # drop_pending_updates: başlangıçta birikmiş eski mesajları atla (geç işleme sorunu)
    app.run_polling(close_loop=False, stop_signals=None, drop_pending_updates=True)


if __name__ == "__main__":
    run_telegram()