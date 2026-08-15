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
        CREATE TABLE IF NOT EXISTS memory (
            konu TEXT PRIMARY KEY,     -- 'profil' | 'hedefler' | 'rutinler' | ...
            icerik TEXT,               -- JSON
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