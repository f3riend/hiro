"""
media.py — Hiro Medya Kütüphanesi deposu
═══════════════════════════════════════════════════════════════════════════
Tek db (media.db), üç bölüm:
  izlenenler    → TMDB metadata'lı arşiv (kapak, oyuncu, özet...)
  takip         → favori animeler: adı, animecix URL, son görülen bölüm
  izleme_gecmisi → ne zaman ne izledin (öneri için)

Hiro buna tool ile erişir: kütüphaneye ekle, takibe al, öneri için sorgula,
izlendi işaretle. Öneri: TMDB'nin genres/keywords/cast alanları sayesinde
"bunu sevdiysen şunu da" yapılabilir.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

_here = Path(__file__).resolve()
if _here.parent.name == "media_engine":
    ROOT = _here.parents[3]
else:
    ROOT = _here.parent
DB_PATH = ROOT / "hiro_state" / "media.db"


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
        CREATE TABLE IF NOT EXISTS izlenenler (
            id INTEGER PRIMARY KEY,        -- TMDB id
            type TEXT,                     -- movie | tv | anime
            title TEXT,
            metadata TEXT,                 -- tam TMDB JSON (kapak, oyuncu, özet...)
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS takip (
            anime_adi TEXT PRIMARY KEY,
            animecix_url TEXT,
            son_bolum INTEGER DEFAULT 0,   -- en son görülen/bilinen bölüm no
            tmdb_id INTEGER,
            eklendi TEXT,
            son_kontrol TEXT
        );
        CREATE TABLE IF NOT EXISTS izleme_gecmisi (
            id INTEGER PRIMARY KEY,
            baslik TEXT,
            tmdb_id INTEGER,
            bolum TEXT,
            izlendi_at TEXT
        );
    """)
    conn.commit()
    conn.close()


# ─── İZLENENLER (metadata'lı arşiv) ─────────────────────────────────────────
def add_watched(metadata: dict) -> dict:
    """TMDB metadata'sını izlenenler kütüphanesine ekle."""
    init_db()
    conn = db()
    conn.execute(
        "INSERT INTO izlenenler(id,type,title,metadata,added_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata, title=excluded.title",
        (metadata.get("id"), metadata.get("type"), metadata.get("title"),
         json.dumps(metadata, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return metadata


def list_watched(limit=50) -> list:
    init_db()
    conn = db()
    rows = conn.execute("SELECT metadata FROM izlenenler ORDER BY added_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [json.loads(r["metadata"]) for r in rows]


# ─── TAKİP (favori + yeni bölüm) ────────────────────────────────────────────
def add_tracking(anime_adi, animecix_url="", son_bolum=0, tmdb_id=None) -> dict:
    init_db()
    conn = db()
    conn.execute(
        "INSERT INTO takip(anime_adi,animecix_url,son_bolum,tmdb_id,eklendi,son_kontrol) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(anime_adi) DO UPDATE SET "
        "animecix_url=excluded.animecix_url, tmdb_id=excluded.tmdb_id",
        (anime_adi, animecix_url, son_bolum, tmdb_id,
         datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"anime_adi": anime_adi, "son_bolum": son_bolum}


def list_tracking() -> list:
    init_db()
    conn = db()
    rows = conn.execute("SELECT * FROM takip ORDER BY anime_adi").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_episode(anime_adi, yeni_bolum) -> dict:
    """Takipteki animenin son bölümünü güncelle (yeni bölüm çıkınca/izlenince)."""
    init_db()
    conn = db()
    conn.execute("UPDATE takip SET son_bolum=?, son_kontrol=? WHERE anime_adi=?",
                 (yeni_bolum, datetime.now().isoformat(), anime_adi))
    conn.commit()
    conn.close()
    return {"anime_adi": anime_adi, "son_bolum": yeni_bolum}


def find_tracking_by_url(url) -> dict:
    """Bir URL takip listesindeki bir animeye ait mi? (sekme izleyici kullanır)"""
    init_db()
    conn = db()
    rows = conn.execute("SELECT * FROM takip").fetchall()
    conn.close()
    for r in rows:
        if r["animecix_url"] and r["animecix_url"] in url:
            return dict(r)
        # title_id eşleşmesi (URL'de /titles/13289/ gibi)
    return None


# ─── İZLEME GEÇMİŞİ (öneri için) ────────────────────────────────────────────
def add_history(baslik, tmdb_id=None, bolum="") -> dict:
    init_db()
    conn = db()
    conn.execute("INSERT INTO izleme_gecmisi(baslik,tmdb_id,bolum,izlendi_at) VALUES(?,?,?,?)",
                 (baslik, tmdb_id, bolum, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"baslik": baslik, "bolum": bolum}


def recent_history(limit=10) -> list:
    init_db()
    conn = db()
    rows = conn.execute("SELECT * FROM izleme_gecmisi ORDER BY izlendi_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]