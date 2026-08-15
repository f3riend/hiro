from langchain_core.tools import tool
from loguru import logger
from pathlib import Path
from openai import OpenAI
import subprocess
import asyncio
import json

from app.services.browser_engine import run_template as engine_run
from app.services.memory_engine import get_memory, save_memory, list_topics
from app.services.media_engine import (get_metadata, add_watched, list_watched,
    add_tracking, list_tracking, add_history, recent_history)
from app.core.settings import settings

tools_log = logger.bind(module="hiro_tools")

TEMPLATES_DIR = Path("templates")


def available_templates() -> list[dict]:
    out = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            out.append({"name": p.stem, "description": t.get("description", ""),
                        "params": t.get("params", [])})
        except Exception:
            out.append({"name": p.stem, "description": "", "params": []})
    return out


@tool
def web_search(query: str) -> str:
    """Search the web for current information. Use for anything that must be up to
    date or is outside your training data: whether a new season/episode is out,
    prices, latest news, release dates. Don't guess — search and verify."""
    import os
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return json.dumps({"error": "web_search için OPENAI_API_KEY gerekli"}, ensure_ascii=False)
    # web arama ana modelden BAĞIMSIZ — provider anthropic olsa bile OpenAI'ın
    # web-search destekli modelini kullan (settings.ai.model'i BURAYA gönderme)
    client = OpenAI(api_key=key)
    try:
        resp = client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search_preview"}],
            input=query,
        )
        tools_log.info(f"web_search: {query}")
        return resp.output_text
    except Exception as e:
        tools_log.warning(f"web_search failed: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# engine is async but this tool runs inside FastAPI's running loop;
# asyncio.run would fail, so run the coroutine in a dedicated thread.
def _run_async(coro):
    import threading
    result = {}
    def worker():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro)
        finally:
            loop.close()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get("value")


@tool
def run_template(template_name: str, params: dict) -> str:
    """Run a browser automation template (Hiro engine) in the real browser.
    Use to actually do things: search a site, check for new episodes, open pages,
    scrape data. template_name MUST be one of the exact names listed in your system
    prompt (don't invent names). params are what that template expects.
    Returns structured JSON — look at ok, data, changes. If the name is wrong the
    result lists the available templates; pick the correct one and call again."""
    tpl_path = TEMPLATES_DIR / f"{template_name}.json"
    if not tpl_path.exists():
        return json.dumps({"ok": False, "error": f"template '{template_name}' not found",
                           "available": available_templates()}, ensure_ascii=False)
    template = json.loads(tpl_path.read_text(encoding="utf-8"))
    try:
        result = _run_async(engine_run(template, params))
        tools_log.info(f"run_template: {template_name} ok={result.get('ok')}")
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        tools_log.warning(f"run_template failed: {e}")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)




# schedules-engine is a CLI like browser_engine; call it as a subprocess so the
# AI produces the same dynamic params it would on the command line.
SCHEDULER = "app/services/schedules_engine/scheduler.py"


@tool
def schedule_task(action: str, when: str = "", repeat: str = "once", at: str = "",
                  template: str = "", params: dict = None, message: str = "",
                  habit: str = "", notify: str = "") -> str:
    """Schedule a task to run later (Hiro schedules-engine). Use for anything
    time-based: download tonight at midnight, remind every 3 days, weekly checks.
    action: 'browser_engine' (run a template) or 'notify' (send a reminder).
    when: ALWAYS use relative format — '+2min' '+3h' '+1d' 'tonight' 'tomorrow 09:00'.
          NEVER compute or guess an ISO date yourself — the scheduler converts these.
    repeat: 'once'|'every:3d'|'every:1w'|'weekdays:1,3,5,6' (1=Mon..7=Sun).
    at: 'HH:MM' for repeating jobs (e.g. '09:00').
    For browser_engine give template + params; for notify give message (+ optional
    habit key for tracking). notify: message to push when the job finishes.
    Returns JSON with the job id and next_run."""
    cmd = ["python", SCHEDULER, "add", "--action", action]
    if when:
        cmd += ["--when", when]
    if repeat and repeat != "once":
        cmd += ["--repeat", repeat]
    if at:
        cmd += ["--at", at]
    if template:
        cmd += ["--template", template]
    if params:
        cmd += ["--params", *[f"{k}={v}" for k, v in params.items()]]
    if message:
        cmd += ["--message", message]
    if habit:
        cmd += ["--habit", habit]
    if notify:
        cmd += ["--notify", notify]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        tools_log.info(f"schedule_task: {action} repeat={repeat}")
        return out.stdout.strip() or json.dumps({"ok": False, "error": out.stderr[:300]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def list_scheduled() -> str:
    """List all scheduled tasks (pending, repeating, etc). Use when the user asks
    what's scheduled, what's coming up, or before editing/cancelling a task."""
    try:
        out = subprocess.run(["python", SCHEDULER, "list"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def missed_tasks() -> str:
    """Show tasks whose time passed while the machine was off (missed jobs).
    Use at startup or when the user asks what they missed, so they can reschedule
    or cancel. The system never auto-decides — it surfaces missed jobs for the user."""
    try:
        out = subprocess.run(["python", SCHEDULER, "missed"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)




NOTIFIER = "app/services/notification_engine/notifier.py"


@tool
def send_notification(message: str) -> str:
    """Send a notification to Oktay right now (desktop + log). Use for immediate
    alerts. For time-based reminders use schedule_task with action='notify' instead."""
    try:
        out = subprocess.run(["python", NOTIFIER, "send", "--message", message],
                             capture_output=True, text=True, timeout=15)
        tools_log.info(f"send_notification: {message[:40]}")
        return out.stdout.strip()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)




@tool
def hafiza_getir(konu: str = "") -> str:
    """Oktay hakkında kayıtlı bilgiyi çek (lazy-fetch). konu boşsa mevcut konuları
    listeler; bir konu verilirse o konunun içeriğini döner.
    Konular: profil (kim, tercihler), hedefler, rutinler, favori_anime, vb.
    Kişiselleştirme gereken, Oktay'ın kendisiyle/tercihleriyle/hedefleriyle ilgili
    bir şey sorulduğunda çağır. Her şeyi peşin bilme — ihtiyaç olunca bunu çağır."""
    if not konu:
        topics = list_topics()
        return json.dumps({"mevcut_konular": topics}, ensure_ascii=False)
    veri = get_memory(konu)
    if not veri:
        return json.dumps({"konu": konu, "not": "bu konuda henüz kayıt yok"}, ensure_ascii=False)
    tools_log.info(f"hafiza_getir: {konu}")
    return json.dumps({"konu": konu, "icerik": veri}, ensure_ascii=False)


@tool
def hafiza_kaydet(konu: str, veri: dict) -> str:
    """Oktay hakkında yeni bir bilgiyi hafızaya kaydet/güncelle. konu: 'profil'|
    'hedefler'|'rutinler'|'favori_anime' gibi. veri: kaydedilecek alanlar (dict).
    Mevcut içerikle BİRLEŞTİRİR (üzerine yazmaz), yani eski bilgi korunur.
    Oktay kendisi hakkında kalıcı bir şey söylediğinde çağır: hedef, tercih, rutin,
    favori. Geçici/anlık şeyleri kaydetme — sadece kalıcı bilgiyi."""
    saved = save_memory(konu, veri, birlestir=True)
    tools_log.info(f"hafiza_kaydet: {konu} <- {list(veri.keys())}")
    return json.dumps({"ok": True, "konu": konu, "guncel": saved}, ensure_ascii=False)




@tool
def kutuphaneye_ekle(isimler: list, media_type: str = "tv") -> str:
    """Bir film/dizi/animeyi TMDB'den zenginleştirip izlenenler kütüphanesine ekle.
    isimler: eşleştirme için ad listesi — animecix hem Japonca hem İngilizce ad
    tutar, İKİSİNİ DE ver (ör. ["Tensei shitara Slime", "That Time I Got
    Reincarnated as a Slime"]) ki doğru eşleşsin. media_type: tv (dizi/anime) | movie.
    TMDB'den kapak, oyuncu, özet, tür çeker, kütüphaneye kaydeder."""
    meta = get_metadata(isimler, media_type)
    if not meta:
        return json.dumps({"ok": False, "not": "TMDB'de eşleşme bulunamadı"}, ensure_ascii=False)
    add_watched(meta)
    add_history(meta["title"], meta.get("id"))
    tools_log.info(f"kutuphaneye_ekle: {meta['title']}")
    return json.dumps({"ok": True, "eklendi": meta["title"], "yil": meta.get("year"),
                       "tur": meta.get("genres"), "kapak": meta.get("poster_url")},
                      ensure_ascii=False)


@tool
def takibe_al(anime_adi: str, animecix_url: str = "", son_bolum: int = 0) -> str:
    """Bir animeyi takip listesine ekle — yeni bölüm çıkınca haber verilir (otomatik
    indirilmez, Oktay karar verir). anime_adi: takip adı. animecix_url: bölüm sayfası
    URL'i (kontrol için). son_bolum: şu anki en son bölüm no."""
    res = add_tracking(anime_adi, animecix_url, son_bolum)
    tools_log.info(f"takibe_al: {anime_adi} (bölüm {son_bolum})")
    return json.dumps({"ok": True, "takip": anime_adi, "son_bolum": son_bolum,
                       "not": "yeni bölüm çıkınca haber vereceğim"}, ensure_ascii=False)


@tool
def kutuphane_getir(ne: str = "izlenenler") -> str:
    """Medya kütüphanesini sorgula. ne: 'izlenenler' (arşiv) | 'takip' (takip listesi)
    | 'gecmis' (son izlenenler). Öneri yaparken ya da 'ne izledim' sorulunca kullan.
    İzlenenlerin tür/oyuncu bilgisiyle benzer öneriler yapabilirsin."""
    if ne == "takip":
        data = list_tracking()
    elif ne == "gecmis":
        data = recent_history()
    else:
        data = [{"title": w["title"], "type": w.get("type"), "year": w.get("year"),
                 "genres": w.get("genres"), "rating": w.get("rating")}
                for w in list_watched()]
    return json.dumps({"ok": True, "ne": ne, "kayitlar": data}, ensure_ascii=False)


TOOLS = [web_search, run_template, schedule_task, list_scheduled, missed_tasks, send_notification, hafiza_getir, hafiza_kaydet, kutuphaneye_ekle, takibe_al, kutuphane_getir]