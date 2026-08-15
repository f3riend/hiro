"""
notifier.py — Hiro Notification Engine
═══════════════════════════════════════════════════════════════════════════
schedules-engine ile aynı desen: CLI, lokal SQLite (aynı events tablosu).
Görevi TEK: bir mesajı Oktay'a ulaştırmak. Neyin ne zaman bildirileceğine
karar vermez — event tablosundaki 'notify_request' event'lerini okur, gönderir.

Engine'ler birbirini tanımaz. schedules-engine event yazar, notifier okur.

KULLANIM (CLI)
  # Doğrudan bildirim gönder (test / manuel)
  python notifier.py send --message "Su iç"

  # Bekleyen notify_request event'lerini işle (bir kez)
  python notifier.py drain

  # Daemon: sürekli çalışır, her 10sn yeni event'leri gönderir
  python notifier.py run

  # Gönderilen bildirim geçmişi
  python notifier.py history

KANAL
  Şimdilik: masaüstü bildirimi (notify-send, Linux) + log kaydı.
  Sonra: Supabase'e yaz → Flutter push (channel="supabase" eklenince).
"""

import os
import json
import time
import urllib.request
import urllib.parse
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT = os.getenv("TELEGRAM_BOT")
CHAT_ID = os.getenv("CHAT_ID")

# DB yolu repo köküne sabit (çalışma dizininden bağımsız)
_here = Path(__file__).resolve()
if _here.parent.name == "notification_engine":
    ROOT = _here.parents[3]
else:
    ROOT = _here.parent
DB_PATH = ROOT / "hiro_state" / "schedules.db"   # schedules-engine ile AYNI db (ortak events)


def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # eşzamanlı okuma/yazma — kilitlenmeyi önler
    conn.execute("PRAGMA busy_timeout=30000") # kilitliyse 30sn bekle, hemen hata verme
    return conn


def init_db():
    conn = db()
    # events tablosu schedules-engine tarafından da oluşturulur; burada garanti
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            type TEXT,
            payload TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY,
            message TEXT,
            channel TEXT,
            sent_at TEXT,
            seen INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# GÖNDERME
# ═══════════════════════════════════════════════════════════════════════════
def send_telegram(message):
    """Telegram Bot API ile mesaj yolla. TELEGRAM_BOT + CHAT_ID gerekli."""
    if not TELEGRAM_BOT or not CHAT_ID:
        return False, "TELEGRAM_BOT / CHAT_ID .env'de yok"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": message}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            return resp.status == 200, None
    except Exception as e:
        return False, str(e)


def deliver(message, channel="telegram"):
    """Mesajı Oktay'a ulaştır. Telegram üzerinden; başarısızsa konsola düşer."""
    ok, err = send_telegram(message)
    # her durumda konsola da bas (görünür olsun / telegram yoksa yedek)
    tag = "🔔" if ok else "⚠️"
    print(f"{tag} [{datetime.now().strftime('%H:%M')}] {message}" + (f"  (telegram: {err})" if err else ""))

    conn = db()
    conn.execute("INSERT INTO notifications(message,channel,sent_at,seen) VALUES(?,?,?,0)",
                 (message, "telegram" if ok else "console", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return ok


# ═══════════════════════════════════════════════════════════════════════════
# KOMUTLAR
# ═══════════════════════════════════════════════════════════════════════════
def cmd_send(args):
    ok = deliver(args.message, args.channel)
    print(json.dumps({"ok": ok, "message": args.message, "channel": args.channel}, ensure_ascii=False))


def cmd_drain(args):
    """Bekleyen notify_request event'lerini işle (bir kez)."""
    conn = db()
    rows = conn.execute(
        "SELECT * FROM events WHERE type='notify_request' AND status='new' ORDER BY id").fetchall()
    sent = 0
    for row in rows:
        payload = json.loads(row["payload"] or "{}")
        msg = payload.get("message", "(boş bildirim)")
        deliver(msg, payload.get("channel", "local"))
        conn.execute("UPDATE events SET status='done' WHERE id=?", (row["id"],))
        sent += 1
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "sent": sent}, ensure_ascii=False))


def cmd_history(args):
    conn = db()
    rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    print(json.dumps({"ok": True, "count": len(out), "notifications": out}, ensure_ascii=False, indent=2))


def cmd_run(args):
    """Daemon: her 10sn yeni notify_request event'lerini gönder."""
    print(json.dumps({"ok": True, "daemon": "started", "interval_s": 10}, ensure_ascii=False))
    while True:
        cmd_drain(argparse.Namespace())
        time.sleep(10)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def main():
    init_db()
    ap = argparse.ArgumentParser(description="Hiro Notification Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send")
    s.add_argument("--message", required=True)
    s.add_argument("--channel", default="telegram")
    s.set_defaults(func=cmd_send)

    d = sub.add_parser("drain"); d.set_defaults(func=cmd_drain)
    h = sub.add_parser("history"); h.set_defaults(func=cmd_history)
    r = sub.add_parser("run"); r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()