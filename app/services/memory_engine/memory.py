"""
memory.py — Hiro Memory Engine
═══════════════════════════════════════════════════════════════════════════
Claude × Obsidian mantığı: hiçbir şey peşin yüklenmez. Hiro bir bilgiye
ihtiyaç duyunca hafiza_getir ile ilgili konuyu çeker (lazy-fetch), az token.
Yazma: hafiza_kaydet ile konu bazlı günceller.

Depolama: lokal SQLite (hiro_state/memory.db), konu bazlı key-value + JSON.
Konular esnek — yeni konu çıkınca şema değişmez, yeni satır eklenir.
Örnek konular: profil, hedefler, rutinler, tercihler, favori_anime.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

_here = Path(__file__).resolve()
if _here.parent.name == "memory_engine":
    ROOT = _here.parents[3]
else:
    ROOT = _here.parent
DB_PATH = ROOT / "hiro_state" / "memory.db"


def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    conn = db()
    conn.executescript("""
        -- SEMANTİK katman: kalıcı gerçekler (profil, hedefler, tercihler) — mevcut tablo
        CREATE TABLE IF NOT EXISTS memory (
            konu TEXT PRIMARY KEY,     -- 'profil' | 'hedefler' | 'rutinler' | ...
            icerik TEXT,               -- JSON
            updated_at TEXT
        );
        -- EPİZODİK katman: ne zaman ne oldu (olaylar, zaman damgalı)
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY,
            olay TEXT,                 -- "spor yaptı", "Tensura B17 izledi", "X'i tamamladı"
            detay TEXT,                -- opsiyonel JSON detay
            ne_zaman TEXT
        );
        -- ÇALIŞMA katmanı: bugünkü/anlık bağlam (kısa ömürlü, heartbeat temizler)
        CREATE TABLE IF NOT EXISTS working (
            anahtar TEXT PRIMARY KEY,  -- "bugun_odak", "acik_konu"
            deger TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()


def get_memory(konu: str) -> dict:
    """Bir konuyu çek. Yoksa boş döner."""
    init_db()
    conn = db()
    row = conn.execute("SELECT icerik FROM memory WHERE konu=?", (konu,)).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["icerik"])
    except Exception:
        return {}


def list_topics() -> list:
    """Hangi konular kayıtlı — Hiro neyi çekebileceğini bilsin diye."""
    init_db()
    conn = db()
    rows = conn.execute("SELECT konu FROM memory ORDER BY konu").fetchall()
    conn.close()
    return [r["konu"] for r in rows]


def save_memory(konu: str, veri: dict, birlestir: bool = True) -> dict:
    """Bir konuyu kaydet/güncelle. birlestir=True ise mevcut içerikle birleştirir
    (üzerine yazmaz), False ise tamamen değiştirir."""
    init_db()
    conn = db()
    if birlestir:
        mevcut = get_memory(konu)
        mevcut.update(veri)
        veri = mevcut
    conn.execute(
        "INSERT INTO memory(konu,icerik,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(konu) DO UPDATE SET icerik=excluded.icerik, updated_at=excluded.updated_at",
        (konu, json.dumps(veri, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return veri


# ─── EPİZODİK katman: zaman damgalı olaylar ─────────────────────────────────
def add_episode(olay: str, detay: dict = None) -> dict:
    """Bir olayı epizodik hafızaya kaydet (ne zaman ne oldu)."""
    init_db()
    conn = db()
    conn.execute("INSERT INTO episodes(olay,detay,ne_zaman) VALUES(?,?,?)",
                 (olay, json.dumps(detay or {}, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"olay": olay}


def recent_episodes(gun: int = 1, limit: int = 50) -> list:
    """Son N günün olaylarını getir (heartbeat brifing için)."""
    from datetime import timedelta
    init_db()
    esik = (datetime.now() - timedelta(days=gun)).isoformat()
    conn = db()
    rows = conn.execute("SELECT * FROM episodes WHERE ne_zaman >= ? ORDER BY ne_zaman DESC LIMIT ?",
                        (esik, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── ÇALIŞMA katmanı: anlık bağlam ──────────────────────────────────────────
def set_working(anahtar: str, deger: str) -> dict:
    init_db()
    conn = db()
    conn.execute("INSERT INTO working(anahtar,deger,updated_at) VALUES(?,?,?) "
                 "ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger, updated_at=excluded.updated_at",
                 (anahtar, deger, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {anahtar: deger}


def get_working(anahtar: str = "") -> dict:
    init_db()
    conn = db()
    if anahtar:
        row = conn.execute("SELECT deger FROM working WHERE anahtar=?", (anahtar,)).fetchone()
        conn.close()
        return {anahtar: row["deger"]} if row else {}
    rows = conn.execute("SELECT anahtar,deger FROM working").fetchall()
    conn.close()
    return {r["anahtar"]: r["deger"] for r in rows}


def clear_working() -> dict:
    """Çalışma katmanını temizle (heartbeat gün sonu/başı çağırır)."""
    init_db()
    conn = db()
    conn.execute("DELETE FROM working")
    conn.commit()
    conn.close()
    return {"cleared": True}


def compute_streaks() -> dict:
    """Epizodik olaylardan alışkanlık streak'i hesapla (kaç gün üst üste)."""
    from collections import defaultdict
    from datetime import timedelta
    olaylar = recent_episodes(gun=30, limit=200)
    gunler = defaultdict(set)
    for e in olaylar:
        tarih = (e.get("ne_zaman") or "")[:10]
        if tarih:
            gunler[e["olay"]].add(tarih)
    streaks = {}
    bugun = datetime.now().date()
    for olay, tarihler in gunler.items():
        streak = 0
        d = bugun
        while d.isoformat() in tarihler:
            streak += 1
            d = d - timedelta(days=1)
        if streak > 0:
            streaks[olay] = streak
    return streaks