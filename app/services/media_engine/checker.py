"""
checker.py — Takip listesindeki animelerin yeni bölümünü kontrol et
═══════════════════════════════════════════════════════════════════════════
Periyodik çalışır (scheduler tetikler). Takip listesindeki her anime için
animecix sayfasına bakar, son bölüm numarası arttıysa "yeni bölüm var" bildirir.
İndirmez — sadece haber verir (Oktay karar verir).

Kontrol yöntemi: animecix'in title sayfasındaki en yüksek bölüm numarasını
oku, hafızadaki son_bolum ile karşılaştır. Fark varsa event/bildirim.
"""

import json
import asyncio
from loguru import logger

from app.services.media_engine.media import list_tracking, update_episode

check_log = logger.bind(module="media_checker")


async def _check_one(anime):
    """Bir animenin animecix sayfasında en son bölümü bul. browser_engine kullanır."""
    from app.services.browser_engine import run_template
    url = anime.get("animecix_url", "")
    if not url:
        return None
    # basit: title sayfasına git, bölüm listesindeki en yüksek numarayı al
    # (küçük bir inline şablon — ayrı JSON'a gerek yok)
    template = {
        "name": "_bolum_kontrol",
        "steps": [
            {"do": "navigate", "url": url},
            {"do": "wait", "selector": "body", "timeout_ms": 15000},
            {"do": "delay", "seconds": 2},
            {"do": "js_eval",
             "code": "() => { const links=[...document.querySelectorAll('a[href*=\"/episode/\"]')]; "
                     "const nums=links.map(a=>{const m=a.href.match(/episode\\/(\\d+)/);return m?+m[1]:0;}); "
                     "return Math.max(0,...nums); }",
             "as": "son_bolum"}
        ]
    }
    try:
        result = await run_template(template, {})
        son = result.get("data", {}).get("son_bolum", 0)
        return int(son) if son else 0
    except Exception as e:
        check_log.warning(f"{anime['anime_adi']} kontrol hata: {e}")
        return None


def check_all() -> list:
    """Tüm takip listesini kontrol et. Yeni bölümü olanları döndür (bildirim için).
    Senkron — scheduler'dan çağrılır."""
    tracking = list_tracking()
    if not tracking:
        return []

    yeni_olanlar = []

    async def _run():
        for anime in tracking:
            bulunan = await _check_one(anime)
            if bulunan and bulunan > anime.get("son_bolum", 0):
                yeni_olanlar.append({
                    "anime": anime["anime_adi"],
                    "eski_bolum": anime.get("son_bolum", 0),
                    "yeni_bolum": bulunan,
                    "url": anime.get("animecix_url", ""),
                })
                update_episode(anime["anime_adi"], bulunan)

    # ayrı event loop'ta çalıştır
    import threading
    def worker():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
    t = threading.Thread(target=worker)
    t.start()
    t.join()

    for y in yeni_olanlar:
        check_log.info(f"yeni bölüm: {y['anime']} B{y['eski_bolum']}→B{y['yeni_bolum']}")
    return yeni_olanlar