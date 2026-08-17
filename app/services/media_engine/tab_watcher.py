"""
tab_watcher.py — Sekme İzleyici (otomatik izlendi tespiti)
═══════════════════════════════════════════════════════════════════════════
CDP'den açık sekmeleri periyodik okur. Bir sekme takip listendeki bir animenin
URL'iyse süre saymaya başlar. 10 dakika dolunca tarayıcıda bir popup çıkarır
(JS injection ile) — Oktay onaylarsa "izlendi" işaretlenir, son bölüm güncellenir.

Otomatik ama son söz Oktay'da: 10dk eşik + popup onayı. Körlemesine işaretlemez.

main.py'de ayrı thread olarak başlar (scheduler/notifier gibi).
CDP: 127.0.0.1:9222 (browser_engine ile aynı tarayıcı).
"""

import time
import json
import urllib.request
from datetime import datetime
from loguru import logger

from app.services.media_engine.media import find_tracking_by_url, update_episode, add_history

tab_log = logger.bind(module="tab_watcher")

CDP_URL = "http://127.0.0.1:9222"
CHECK_INTERVAL = 30          # saniyede bir açık sekmeleri kontrol et
WATCH_THRESHOLD = 600        # 10 dakika (saniye) — bu süre kalırsan "izliyor" say

# URL -> {anime, started_at, prompted} — hangi sekmeyi ne zamandır izliyorsun
_watching = {}


def _get_open_tabs():
    """CDP'den açık sekmelerin URL'lerini al."""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json", timeout=5) as r:
            tabs = json.load(r)
        return [t for t in tabs if t.get("type") == "page" and t.get("url")]
    except Exception:
        return []


def _show_popup(tab_id, anime_adi, bolum):
    """Sekmede onay popup'ı göster (CDP Runtime.evaluate ile JS inject).
    Basit bir confirm — Oktay Tamam derse izlendi işaretlenir."""
    js = f"""
        (() => {{
            const d = document.createElement('div');
            d.id = 'hiro-watch-popup';
            d.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:999999;'+
                'background:#1a1a1a;color:#e5e5e5;padding:16px 20px;border-radius:12px;'+
                'border:1px solid #3a3a3a;box-shadow:0 8px 24px rgba(0,0,0,.5);'+
                'font-family:sans-serif;font-size:14px;max-width:300px;';
            d.innerHTML = '<div style="margin-bottom:10px;">🎬 <b>{anime_adi}</b> — '+
                'Bölüm {bolum}\\'i izliyor gibisin. İzlendi olarak işaretleyeyim mi?</div>'+
                '<button id="hiro-yes" style="background:#4a9eff;color:#fff;border:0;'+
                'padding:6px 14px;border-radius:6px;margin-right:8px;cursor:pointer;">Evet</button>'+
                '<button id="hiro-no" style="background:#333;color:#ccc;border:0;'+
                'padding:6px 14px;border-radius:6px;cursor:pointer;">Hayır</button>';
            document.body.appendChild(d);
            window.__hiroWatchAnswer = null;
            document.getElementById('hiro-yes').onclick = () => {{ window.__hiroWatchAnswer = true; d.remove(); }};
            document.getElementById('hiro-no').onclick = () => {{ window.__hiroWatchAnswer = false; d.remove(); }};
        }})();
    """
    _cdp_eval(tab_id, js)


def _read_popup_answer(tab_id):
    """Popup cevabını oku (window.__hiroWatchAnswer)."""
    res = _cdp_eval(tab_id, "window.__hiroWatchAnswer")
    return res  # True / False / None


def _cdp_eval(tab_id, expression):
    """Bir sekmede JS çalıştır (CDP Runtime.evaluate via websocket)."""
    try:
        # sekmenin websocket debugger url'ini bul
        with urllib.request.urlopen(f"{CDP_URL}/json", timeout=5) as r:
            tabs = json.load(r)
        ws_url = None
        for t in tabs:
            if t.get("id") == tab_id:
                ws_url = t.get("webSocketDebuggerUrl")
                break
        if not ws_url:
            return None
        # websocket ile Runtime.evaluate
        from websocket import create_connection
        ws = create_connection(ws_url, timeout=5)
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True}
        }))
        resp = json.loads(ws.recv())
        ws.close()
        return resp.get("result", {}).get("result", {}).get("value")
    except Exception as e:
        tab_log.debug(f"cdp_eval hata: {e}")
        return None


def _extract_episode(url):
    """URL'den bölüm no çıkar (animecix: .../episode/6)."""
    import re
    m = re.search(r"/episode/(\d+)", url)
    return int(m.group(1)) if m else 0


def tick():
    """Bir tur: açık sekmeleri kontrol et, izleme sürelerini güncelle, eşiği geçeni işaretle."""
    tabs = _get_open_tabs()
    now = time.time()
    open_urls = {t["url"]: t["id"] for t in tabs}

    # 1) yeni sekmeler: takip listesinde mi?
    for url, tab_id in open_urls.items():
        if url in _watching:
            continue
        tracked = find_tracking_by_url(url)
        if tracked:
            _watching[url] = {"anime": tracked["anime_adi"], "tab_id": tab_id,
                              "started_at": now, "prompted": False,
                              "bolum": _extract_episode(url)}
            tab_log.info(f"izleme başladı: {tracked['anime_adi']} ({url[:50]})")

    # 2) kapanan sekmeler: izlemeyi bırak
    for url in list(_watching.keys()):
        if url not in open_urls:
            del _watching[url]

    # 3) eşiği geçenlere popup, cevabı işle
    for url, w in list(_watching.items()):
        elapsed = now - w["started_at"]
        # popup henüz gösterilmediyse ve eşik geçtiyse göster
        if not w["prompted"] and elapsed >= WATCH_THRESHOLD:
            _show_popup(w["tab_id"], w["anime"], w["bolum"])
            w["prompted"] = True
            tab_log.info(f"popup gösterildi: {w['anime']} ({int(elapsed)}s izlendi)")
        # popup gösterildiyse cevabı oku
        elif w["prompted"]:
            ans = _read_popup_answer(w["tab_id"])
            if ans is True:
                update_episode(w["anime"], w["bolum"])
                add_history(w["anime"], bolum=f"B{w['bolum']}")
                tab_log.info(f"izlendi işaretlendi: {w['anime']} B{w['bolum']}")
                del _watching[url]  # işlendi, listeden çıkar
            elif ans is False:
                tab_log.info(f"izlendi reddedildi: {w['anime']}")
                del _watching[url]


def run():
    """Daemon: sürekli çalışır, her CHECK_INTERVAL saniyede tick."""
    tab_log.info(f"Sekme izleyici başladı (eşik {WATCH_THRESHOLD//60}dk, kontrol {CHECK_INTERVAL}s)")
    while True:
        try:
            tick()
        except Exception as e:
            tab_log.warning(f"tick hata: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()