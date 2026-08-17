"""
heartbeat.py — Hiro Heartbeat (proaktif günlük brifing)
═══════════════════════════════════════════════════════════════════════════
Sen sormadan çalışan kendi kendine düşünme döngüsü. Her sabah (scheduler
tetikler) tüm katmanları toplar, bir brifing üretir, Telegram'dan gönderir:
  - dünkü ilerleme (epizodik hafıza)
  - bugünkü hatırlatmalar (scheduler)
  - takip ettiğin animelerde yenilik (checker)
  - alışkanlık durumu (streak — "🔥 5 gündür spor")
  - açık konular (çalışma katmanı)

Bu senin "mental yükü azalt + ilerlemeyi görünür kıl" hedefinin kalbi.
Brifing METNİNİ Hiro'nun (Opus) üretmesi için chat'e verilir — kuru liste değil,
seni tanıyan bir dostun günaydın mesajı gibi.
"""

import json
from datetime import datetime, timedelta
from loguru import logger

hb_log = logger.bind(module="heartbeat")


def gather_context() -> dict:
    """Tüm engine'lerden brifing verisini topla (ham veri, metin değil)."""
    ctx = {}

    # epizodik: dün ne yaptın
    try:
        from app.services.memory_engine.memory import recent_episodes, get_working, get_memory
        ctx["dun_olaylar"] = [e["olay"] for e in recent_episodes(gun=1)]
        ctx["acik_konular"] = get_working()
        ctx["hedefler"] = get_memory("hedefler")
    except Exception as e:
        hb_log.warning(f"hafıza toplama hata: {e}")

    # scheduler: bugünkü işler (subprocess DEĞİL — doğrudan DB'den oku, "No module named app" olmasın)
    try:
        import sqlite3
        from pathlib import Path
        sch_db = Path("hiro_state") / "schedules.db"
        if sch_db.exists():
            conn = sqlite3.connect(sch_db, timeout=10)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT action, message, at_time, next_run FROM schedules "
                "WHERE status='pending' ORDER BY next_run LIMIT 20").fetchall()
            conn.close()
            ctx["zamanli_isler"] = [dict(r) for r in rows]
    except Exception as e:
        hb_log.warning(f"scheduler toplama hata: {e}")

    # medya: takip + yeni bölüm
    try:
        from app.services.media_engine.media import list_tracking
        ctx["takip"] = list_tracking()
    except Exception as e:
        hb_log.warning(f"medya toplama hata: {e}")

    # alışkanlık streak (epizodik olaylardan hesapla)
    from app.services.memory_engine.memory import compute_streaks
    ctx["streakler"] = compute_streaks()

    return ctx


def _compute_streaks() -> dict:
    """Epizodik olaylardan alışkanlık streak'i hesapla (kaç gün üst üste)."""
    try:
        from app.services.memory_engine.memory import recent_episodes
        olaylar = recent_episodes(gun=30, limit=200)
        # olay adına göre son yapılan tarihleri topla
        from collections import defaultdict
        gunler = defaultdict(set)
        for e in olaylar:
            tarih = (e.get("ne_zaman") or "")[:10]
            if tarih:
                gunler[e["olay"]].add(tarih)
        streaks = {}
        bugun = datetime.now().date()
        for olay, tarihler in gunler.items():
            # bugünden geriye kaç gün kesintisiz
            streak = 0
            d = bugun
            while d.isoformat() in tarihler:
                streak += 1
                d = d - timedelta(days=1)
            if streak > 0:
                streaks[olay] = streak
        return streaks
    except Exception:
        return {}


def build_briefing_prompt(ctx: dict) -> str:
    """Ham veriyi Hiro'ya verilecek bir isteme çevir — Opus metni üretsin."""
    return (
        "Bu, Oktay'a göndereceğin GÜNAYDIN brifingi için ham veri. Bunları kuru "
        "liste olarak değil, seni tanıyan bir dost + iş ortağı tonuyla, kısa ve "
        "net bir brifinge çevir. Sadece anlamlı olanları söyle, boş başlık açma. "
        "İlerlemeyi görünür kıl (streak'leri öv ama abartma), bugüne odak ver, "
        "takıldığı bir şey varsa nazikçe hatırlat.\n\n"
        f"VERİ:\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\n"
        "Şimdi brifingi yaz (Oktay'a doğrudan hitap et):"
    )


def run_heartbeat():
    """Brifingi üret ve Telegram'dan gönder. scheduler ya da daemon çağırır."""
    hb_log.info("heartbeat: BAŞLADI")
    try:
        ctx = gather_context()
        hb_log.info(f"heartbeat: bağlam toplandı ({len(ctx)} alan)")
    except Exception as e:
        hb_log.error(f"heartbeat: bağlam toplama PATLADI: {e}")
        ctx = {}

    # Hiro (Opus) brifing metnini üretsin
    briefing = None
    try:
        from app.core.settings import settings
        auth = getattr(settings.ai, "auth", "apikey").lower()
        hb_log.info(f"heartbeat: brifing üretiliyor (provider={settings.ai.provider}, auth={auth})")
        if settings.ai.provider.lower() == "anthropic" and auth == "oauth":
            from app.services.hiro_core.oauth_engine import oauth_chat
            briefing = oauth_chat(build_briefing_prompt(ctx), settings.ai.model)
        else:
            from app.services.hiro_core import build_agent, chat
            from app.services.hiro_core.conversation import clear_history
            clear_history("_heartbeat")  # her brifing temiz — önceki brifinglerle kirlenmesin
            agent = build_agent()
            briefing = chat(agent, build_briefing_prompt(ctx), user_id="_heartbeat")
        hb_log.info(f"heartbeat: brifing üretildi ({len(briefing or '')} karakter)")
    except Exception as e:
        import traceback
        hb_log.error(f"heartbeat: brifing üretim PATLADI: {e}")
        hb_log.error(traceback.format_exc())
        briefing = None

    # brifing üretilemezse basit bir yedek brifing gönder (yine de haber ver)
    if not briefing:
        streaks = ctx.get("streakler", {})
        takip = ctx.get("takip", [])
        parts = ["Günaydın Oktay! (basit brifing — AI metni üretilemedi)"]
        if streaks:
            parts.append("Streak: " + ", ".join(f"{k}: {v}g" for k, v in streaks.items()))
        if takip:
            parts.append(f"Takip: {len(takip)} anime izleniyor")
        briefing = "\n".join(parts)

    # Telegram'dan gönder (notifier üzerinden)
    try:
        from app.services.notification_engine.notifier import deliver
        sent = deliver(f"☀️ Günaydın Oktay!\n\n{briefing}")
        hb_log.info(f"heartbeat: gönderim sonucu ok={sent}")
    except Exception as e:
        import traceback
        hb_log.error(f"heartbeat: gönderim PATLADI: {e}")
        hb_log.error(traceback.format_exc())

    return briefing


if __name__ == "__main__":
    print(run_heartbeat())