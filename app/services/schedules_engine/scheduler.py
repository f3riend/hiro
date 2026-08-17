import sys
from pathlib import Path as _Path
# subprocess olarak çalışınca (python scheduler.py run) proje kökünü bul, app'i görebil
_root = _Path(__file__).resolve().parents[3]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

"""
scheduler.py — Hiro Schedules Engine
═══════════════════════════════════════════════════════════════════════════
browser_engine ile aynı desen: CLI gibi çalışır, JSON şablon + dinamik params
okur, lokal SQLite'a yazar. Hiro bunu bir tool olarak kullanır, dinamik
parametre üretir. İşler event tablosu üzerinden diğer engine'lere geçer
(engine'ler birbirini tanımaz — DB ile konuşur).

KULLANIM (CLI)
  # Tek seferlik iş — bu akşam 12'de Tensura indir
  python scheduler.py add --when "2026-08-12 00:00" \\
    --action browser_engine --template animecix_ara_ve_indir \\
    --params anime_adi=Tensura --notify "Tensura indi"

  # Tekrarlayan — 3 günde bir duş hatırlat
  python scheduler.py add --repeat every:3d --at 09:00 \\
    --action notify --message "Duş / kişisel bakım vakti" --habit bakim

  # Tekrarlayan — haftanın belirli günleri spor (1=Pzt ... 7=Paz)
  python scheduler.py add --repeat weekdays:1,3,5,6 --at 18:00 \\
    --action notify --message "Spor zamanı"

  # Haftada bir kontrol — Solo Leveling yeni sezon
  python scheduler.py add --repeat every:1w --at 10:00 \\
    --action browser_engine --template animecix_ara_ve_indir \\
    --params anime_adi="Solo Leveling" --notify "Solo Leveling kontrol"

  python scheduler.py list                 # tüm işler
  python scheduler.py missed               # kaçan işler (açılışta hatırlatma)
  python scheduler.py cancel <id>          # iptal
  python scheduler.py reschedule <id> --when "2026-08-13 00:00"
  python scheduler.py tick                 # zamanı gelenleri çalıştır (bir kez)
  python scheduler.py run                  # daemon: her 60sn tick (sunucu gibi)

REPEAT FORMATLARI
  once                → tek seferlik (--when ile tarih-saat)
  every:3d            → her 3 günde bir
  every:1w            → her hafta
  weekdays:1,3,5,6    → haftanın günleri (1=Pzt, 7=Paz)

DAYANIKLILIK
  Her iş DB'de. PC kapanıp açılınca zamanı geçmiş ama çalışmamış işler
  'missed' olur — 'missed' komutuyla görürsün, reschedule/cancel edersin.
  Otomatik karar vermez; kontrol sende.
"""

import re
import json
import time
import argparse
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

# repo kökünü baz al (bu dosya nerede olursa olsun DB hep aynı yerde)
# scheduler.py repo kökünde → parent; app/services/... içindeyse 3 üst
_here = Path(__file__).resolve()
if _here.parent.name == "schedules_engine":
    ROOT = _here.parents[3]        # app/services/schedules_engine/scheduler.py → repo kökü
else:
    ROOT = _here.parent            # repo kökündeki scheduler.py
DB_PATH = ROOT / "hiro_state" / "schedules.db"
TEMPLATES_DIR = ROOT / "templates"


# ═══════════════════════════════════════════════════════════════════════════
# DB
# ═══════════════════════════════════════════════════════════════════════════
def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # eşzamanlı okuma/yazma — kilitlenmeyi önler
    conn.execute("PRAGMA busy_timeout=30000") # kilitliyse 30sn bekle, hemen hata verme
    return conn


def init_db():
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY,
            action TEXT,              -- browser_engine | notify
            template TEXT,            -- browser_engine için şablon adı
            params TEXT,              -- JSON, şablon parametreleri
            message TEXT,             -- notify mesajı
            habit_key TEXT,           -- alışkanlık takibi anahtarı
            notify_msg TEXT,          -- iş bitince gönderilecek bildirim
            repeat TEXT,              -- once | every:3d | weekdays:1,3,5
            at_time TEXT,             -- "18:00" (tekrarlayan için)
            next_run TEXT,            -- ISO datetime, bir sonraki çalışma
            status TEXT DEFAULT 'pending',  -- pending|done|missed|cancelled
            last_run TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            type TEXT,                -- download_request | notify_request | habit_tick
            payload TEXT,             -- JSON
            status TEXT DEFAULT 'new',  -- new|processing|done|error
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()


def emit_event(etype, payload):
    conn = db()
    conn.execute("INSERT INTO events(type,payload,status,created_at) VALUES(?,?,?,?)",
                 (etype, json.dumps(payload, ensure_ascii=False), "new", datetime.now().isoformat()))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# ZAMAN HESABI
# ═══════════════════════════════════════════════════════════════════════════
def parse_at(at_time):
    # "18:00" -> (18, 0)
    h, m = at_time.split(":")
    return int(h), int(m)


def resolve_when(when):
    """Göreli/mutlak zaman ifadesini gerçek datetime'a çevir. Modelin tarih
    aritmetiğine güvenme — burada hesapla. Desteklenen:
      +5min / +2h / +3d           → şimdiden itibaren
      tonight / bu aksam          → bugün 00:00'a giden gece (ertesi gün 00:00)
      today HH:MM / bugun HH:MM    → bugün o saat
      tomorrow HH:MM / yarin HH:MM → yarın o saat
      2026-08-12T00:00 (ISO)       → aynen
    """
    if not when:
        return datetime.now()
    w = when.strip().lower()
    now = datetime.now()

    # +5min / +2h / +3d
    m = re.match(r"\+(\d+)\s*(min|m|h|d)", w)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        if unit in ("min", "m"):
            return now + timedelta(minutes=n)
        if unit == "h":
            return now + timedelta(hours=n)
        if unit == "d":
            return now + timedelta(days=n)

    if w in ("tonight", "bu aksam", "bu akşam", "gece 12", "gece yarisi", "gece yarısı"):
        # bu gece 00:00 = yarın 00:00
        base = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return base

    m = re.match(r"(today|bugun|bugün)\s+(\d{1,2}):(\d{2})", w)
    if m:
        return now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)

    m = re.match(r"(tomorrow|yarin|yarın)\s+(\d{1,2}):(\d{2})", w)
    if m:
        base = now + timedelta(days=1)
        return base.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)

    # ISO ya da "YYYY-MM-DD HH:MM"
    try:
        return datetime.fromisoformat(when.replace(" ", "T"))
    except ValueError:
        return now


def compute_next_run(repeat, at_time, when=None, after=None):
    """Bir sonraki çalışma zamanını hesapla. after: baz alınacak an (default now)."""
    now = after or datetime.now()

    if repeat in ("once", "none", None, ""):
        return resolve_when(when) if when else now

    h, m = parse_at(at_time or "09:00")

    if repeat.startswith("every:"):
        amount = repeat.split(":", 1)[1]  # 3d, 1w, 2h
        n = int(amount[:-1])
        unit = amount[-1]
        base = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if unit == "d":
            delta = timedelta(days=n)
        elif unit == "w":
            delta = timedelta(weeks=n)
        elif unit == "h":
            delta = timedelta(hours=n)
        else:
            delta = timedelta(days=n)
        # bugünün hedef saati geçtiyse bir periyot sonra
        nxt = base
        while nxt <= now:
            nxt = nxt + delta
        return nxt

    if repeat.startswith("weekdays:"):
        days = [int(x) for x in repeat.split(":", 1)[1].split(",")]  # 1=Pzt..7=Paz
        for ahead in range(0, 8):
            cand = (now + timedelta(days=ahead)).replace(hour=h, minute=m, second=0, microsecond=0)
            iso_weekday = cand.isoweekday()  # 1..7
            if iso_weekday in days and cand > now:
                return cand
        return now + timedelta(days=1)

    return now


# ═══════════════════════════════════════════════════════════════════════════
# KOMUTLAR
# ═══════════════════════════════════════════════════════════════════════════
def cmd_add(args):
    params = {}
    for p in args.params or []:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip()] = v.strip()

    repeat = args.repeat or "once"
    next_run = compute_next_run(repeat, args.at, when=args.when)

    conn = db()
    cur = conn.execute(
        """INSERT INTO schedules
           (action,template,params,message,habit_key,notify_msg,repeat,at_time,
            next_run,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (args.action, args.template, json.dumps(params, ensure_ascii=False),
         args.message, args.habit, args.notify, repeat, args.at,
         next_run.isoformat(), "pending", datetime.now().isoformat()))
    conn.commit()
    job_id = cur.lastrowid
    conn.close()
    print(json.dumps({
        "ok": True, "id": job_id, "action": args.action,
        "next_run": next_run.isoformat(), "repeat": repeat
    }, ensure_ascii=False))


def cmd_list(args):
    conn = db()
    rows = conn.execute("SELECT * FROM schedules WHERE status != 'cancelled' ORDER BY next_run").fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    print(json.dumps({"ok": True, "count": len(out), "jobs": out}, ensure_ascii=False, indent=2))


def cmd_missed(args):
    """Zamanı geçmiş ama çalışmamış işler — açılışta hatırlatma için."""
    now = datetime.now().isoformat()
    conn = db()
    # pending ve next_run geçmişte kalmış olanları missed yap
    conn.execute("UPDATE schedules SET status='missed' WHERE status='pending' AND next_run < ?", (now,))
    conn.commit()
    rows = conn.execute("SELECT * FROM schedules WHERE status='missed' ORDER BY next_run").fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    print(json.dumps({"ok": True, "missed_count": len(out), "missed": out}, ensure_ascii=False, indent=2))


def cmd_cancel(args):
    conn = db()
    conn.execute("UPDATE schedules SET status='cancelled' WHERE id=?", (args.id,))
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "cancelled": args.id}, ensure_ascii=False))


def cmd_reschedule(args):
    next_run = datetime.fromisoformat(args.when)
    conn = db()
    conn.execute("UPDATE schedules SET next_run=?, status='pending' WHERE id=?",
                 (next_run.isoformat(), args.id))
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "id": args.id, "next_run": next_run.isoformat()}, ensure_ascii=False))



def _run_engine(template, params):
    """browser_engine (async) motorunu ayrı thread'de çalıştır — event loop çakışması olmasın.
    CLI'dan da çağrılabilsin diye ROOT'u sys.path'e ekler."""
    import threading, sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.services.browser_engine import run_template as engine_run
    result = {}
    def worker():
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(engine_run(template, params))
        finally:
            loop.close()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get("value", {})


def run_job(job):
    """Bir işi çalıştır. browser_engine ise motoru ROOT'tan çağır + sonucu bildirime ekle."""
    action = job["action"]
    result = {"id": job["id"], "action": action}

    if action == "browser_engine":
        params = json.loads(job["params"] or "{}")
        tpl = TEMPLATES_DIR / f"{job['template']}.json"
        notify_msg = job["notify_msg"] or job["template"]

        if not tpl.exists():
            result["error"] = f"template yok: {job['template']}"
            emit_event("notify_request", {"message": f"❌ Hata: '{job['template']}' şablonu bulunamadı"})
            return result

        # browser_engine'i direct import ile çalıştır (chat ile aynı yol, tek kaynak)
        engine_out = {}
        try:
            template = json.loads(tpl.read_text(encoding="utf-8"))
            engine_out = _run_engine(template, params)
            result["engine_output"] = str(engine_out.get("ok"))
        except Exception as e:
            result["error"] = str(e)

        # sonuca göre anlamlı bildirim mesajı oluştur
        if engine_out.get("ok"):
            data = engine_out.get("data", {})
            matched = data.get("matched_results", [])
            notifications = engine_out.get("notifications", [])
            if matched:
                titles = ", ".join(m.get("title", "?") for m in matched[:3])
                final_msg = f"✅ {notify_msg} — Bulundu: {titles}"
            elif notifications:
                final_msg = f"ℹ️ {notify_msg} — {notifications[0]}"
            else:
                final_msg = f"ℹ️ {notify_msg} — gridde bulunamadı (yayınlanmamış olabilir)"
        else:
            errs = engine_out.get("errors", [])
            err_str = errs[0]["error"] if errs else result.get("error", "bilinmeyen hata")
            final_msg = f"❌ {notify_msg} — hata: {err_str[:80]}"

        # yt-dlp indirme yarım kaldı mı? (capture_video download:true ile indirmişse)
        dl = engine_out.get("data", {}).get("download_result") if engine_out.get("ok") else None
        if dl and not dl.get("done"):
            # indirme tamamlanmadı — job'u bitirme, tekrar denenecek (yt-dlp --continue devam eder)
            result["download_incomplete"] = True
            final_msg = f"⏳ {notify_msg} — indirme yarım kaldı, tekrar denenecek"

        emit_event("notify_request", {"message": final_msg})
        result["notification"] = final_msg

    elif action == "notify":
        emit_event("notify_request", {"message": job["message"]})
        if job["habit_key"]:
            emit_event("habit_tick", {"habit": job["habit_key"], "at": datetime.now().isoformat()})
        result["message"] = job["message"]

    elif action == "heartbeat":
        # proaktif günlük brifing üret + gönder
        try:
            from app.services.hiro_core.heartbeat import run_heartbeat
            run_heartbeat()
            result["heartbeat"] = "gönderildi"
        except Exception as e:
            result["error"] = str(e)

    return result


def cmd_tick(args):
    """Zamanı gelen işleri çalıştır (bir kez)."""
    now = datetime.now()
    conn = db()
    due = conn.execute(
        "SELECT * FROM schedules WHERE status='pending' AND next_run <= ? ORDER BY next_run",
        (now.isoformat(),)).fetchall()

    ran = []
    for row in due:
        job = dict(row)
        try:
            res = run_job(job)
            ran.append(res)
        except Exception as e:
            # bir iş patlarsa tüm tick çökmesin — o işi missed yap, devam et
            engine_log_msg = f"job {job.get('id')} hata: {e}"
            try:
                conn.execute("UPDATE schedules SET status='missed' WHERE id=?", (job["id"],))
                conn.commit()
            except Exception:
                pass
            ran.append({"id": job.get("id"), "error": str(e)[:100]})
            continue

        if res.get("download_incomplete"):
            # indirme yarım kaldı → job'u SİLME/tamamlama. next_run'ı biraz ileri al,
            # bir sonraki tick'te yt-dlp --continue ile kaldığı yerden devam etsin.
            retry_at = now + timedelta(minutes=2)
            conn.execute("UPDATE schedules SET last_run=?, next_run=? WHERE id=?",
                         (now.isoformat(), retry_at.isoformat(), job["id"]))
        elif job["repeat"] and job["repeat"] != "once":
            # tekrarlayan → bir sonraki zamanı hesapla, pending kal
            nxt = compute_next_run(job["repeat"], job["at_time"], after=now)
            conn.execute("UPDATE schedules SET last_run=?, next_run=? WHERE id=?",
                         (now.isoformat(), nxt.isoformat(), job["id"]))
        else:
            # tek seferlik + indirme tamam → DB'den SİL
            conn.execute("DELETE FROM schedules WHERE id=?", (job["id"],))
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "ran": len(ran), "jobs": ran}, ensure_ascii=False))


def cmd_run(args):
    """Daemon: sürekli çalışır, her 60sn tick. Sunucu gibi."""
    print(json.dumps({"ok": True, "daemon": "started", "interval_s": 60}, ensure_ascii=False))
    # açılışta kaçanları işaretle
    now = datetime.now().isoformat()
    conn = db()
    conn.execute("UPDATE schedules SET status='missed' WHERE status='pending' AND next_run < ?", (now,))
    conn.commit()
    conn.close()
    while True:
        args_ns = argparse.Namespace()
        cmd_tick(args_ns)
        time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def main():
    init_db()
    ap = argparse.ArgumentParser(description="Hiro Schedules Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--when")                    # tek seferlik: "2026-08-12 00:00"
    a.add_argument("--repeat")                  # every:3d | weekdays:1,3,5 | once
    a.add_argument("--at")                      # "18:00" (tekrarlayan için)
    a.add_argument("--action", required=True, choices=["browser_engine", "notify", "heartbeat"])
    a.add_argument("--template")
    a.add_argument("--params", nargs="*")
    a.add_argument("--message")
    a.add_argument("--habit")
    a.add_argument("--notify")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list"); l.set_defaults(func=cmd_list)
    m = sub.add_parser("missed"); m.set_defaults(func=cmd_missed)

    c = sub.add_parser("cancel"); c.add_argument("id", type=int); c.set_defaults(func=cmd_cancel)

    r = sub.add_parser("reschedule")
    r.add_argument("id", type=int); r.add_argument("--when", required=True)
    r.set_defaults(func=cmd_reschedule)

    t = sub.add_parser("tick"); t.set_defaults(func=cmd_tick)
    rn = sub.add_parser("run"); rn.set_defaults(func=cmd_run)

    # --when "2026-08-12 00:00" formatını ISO'ya çevir
    args = ap.parse_args()
    if getattr(args, "when", None):
        args.when = args.when.replace(" ", "T")
    args.func(args)


if __name__ == "__main__":
    main()