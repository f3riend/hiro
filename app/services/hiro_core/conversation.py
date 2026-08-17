"""
conversation.py — Konuşma Belleği (Recent History)
═══════════════════════════════════════════════════════════════════════════
Modelin "az önce ne konuştuk"u görmesi için. Her kullanıcıyla son N mesajı
saklar, her /chat çağrısında bunlar modele verilir. Bu, "indir → neyi?"
sorununu çözen katman — modele geçmişi taşır.

Basit tut: SQLite, kullanıcı bazlı, son N mesaj. Vektör/embedding YOK — o
ileri katman. Bu sadece yakın bağlam (Recent History).

user_id: Telegram'da CHAT_ID, HTTP'de "default" ya da session id.
"""

import sqlite3
from pathlib import Path
from datetime import datetime

_here = Path(__file__).resolve()
if _here.parent.name == "hiro_core":
    ROOT = _here.parents[3]
else:
    ROOT = _here.parent
DB_PATH = ROOT / "hiro_state" / "conversation.db"

# kaç mesaj tutulsun / modele verilsin (Grok'un önerisi 8-15)
RECENT_LIMIT = 12

# aktif kullanıcı — chat() set eder, lazy-fetch tool'u okur (LangChain tool'a user_id geçemiyor)
_active_user = "default"


def set_active_user(user_id: str):
    global _active_user
    _active_user = str(user_id)


def get_active_user() -> str:
    return _active_user


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
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            user_id TEXT,              -- kim (telegram chat_id / 'default')
            role TEXT,                 -- 'user' | 'assistant'
            content TEXT,
            ts TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_user_ts ON messages(user_id, ts);
        -- konuşma özetleri: eski mesajlar özetlenip buraya, ham mesajlar temizlenebilir
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            ozet TEXT,                 -- eski konuşmanın GÜNCEL özeti (tek, context'e giren)
            mesaj_araligi TEXT,
            ts TEXT
        );
        -- ARŞİV: özetlenip ham halden silinen mesajlar burada kalır (lazy fetch arar)
        CREATE TABLE IF NOT EXISTS archive (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            role TEXT,
            content TEXT,
            ts TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_arch_user ON archive(user_id);
    """)
    conn.commit()
    conn.close()


def add_message(user_id: str, role: str, content: str):
    """Bir mesajı geçmişe ekle (kullanıcının ya da Hiro'nun)."""
    init_db()
    conn = db()
    conn.execute("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
                 (str(user_id), role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def recent_messages(user_id: str, limit: int = RECENT_LIMIT) -> list:
    """Son N mesajı kronolojik sırada döndür (eski→yeni). Modele verilecek format:
    [{"role": "user"/"assistant", "content": "..."}, ...]"""
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (str(user_id), limit)).fetchall()
    conn.close()
    # DESC çektik (son N), şimdi ters çevir (eski→yeni) ki konuşma sırası doğru olsun
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_history(user_id: str):
    """Bir kullanıcının geçmişini temizle (ör. /reset komutu)."""
    init_db()
    conn = db()
    conn.execute("DELETE FROM messages WHERE user_id=?", (str(user_id),))
    conn.commit()
    conn.close()


def message_count(user_id: str) -> int:
    init_db()
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM messages WHERE user_id=?",
                     (str(user_id),)).fetchone()["c"]
    conn.close()
    return n


# ─── ÖZETLEME (Katman 2) ────────────────────────────────────────────────────
# Konuşma bu kadar mesajı geçince, eski kısım özetlenir. Son SUMMARY_KEEP ham
# kalır, öncekiler özete gider. Böylece token patlamaz ama eski bağlam korunur.
SUMMARY_THRESHOLD = 30   # bu kadar mesajı geçince özetle
SUMMARY_KEEP = 12        # son bu kadarı ham kalsın (RECENT_LIMIT ile aynı mantık)


def get_summary(user_id: str) -> str:
    """Bir kullanıcının en güncel konuşma özetini getir (yoksa boş)."""
    init_db()
    conn = db()
    row = conn.execute("SELECT ozet FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT 1",
                       (str(user_id),)).fetchone()
    conn.close()
    return row["ozet"] if row else ""


def needs_summary(user_id: str) -> bool:
    """Ham mesaj sayısı eşiği geçti mi — özetleme zamanı mı?"""
    return message_count(user_id) > SUMMARY_THRESHOLD


def old_messages_for_summary(user_id: str) -> list:
    """Özetlenecek eski mesajları getir (son SUMMARY_KEEP hariç hepsi)."""
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT id, role, content FROM messages WHERE user_id=? ORDER BY id ASC",
        (str(user_id),)).fetchall()
    conn.close()
    if len(rows) <= SUMMARY_KEEP:
        return []
    return [dict(r) for r in rows[:-SUMMARY_KEEP]]  # son KEEP hariç


def save_summary_and_trim(user_id: str, ozet: str):
    """Özeti kaydet, özetlenen eski ham mesajları sil (son SUMMARY_KEEP kalır).
    Önceki özet varsa yenisi onun yerine geçer (özet zaten önceki özeti içerir)."""
    init_db()
    conn = db()
    # özetlenecek eski mesajların id'lerini bul
    rows = conn.execute("SELECT id FROM messages WHERE user_id=? ORDER BY id ASC",
                        (str(user_id),)).fetchall()
    ids = [r["id"] for r in rows]
    if len(ids) > SUMMARY_KEEP:
        to_delete = ids[:-SUMMARY_KEEP]
        # silmeden ÖNCE arşive kopyala (lazy fetch sonradan arayabilsin)
        conn.execute(
            f"INSERT INTO archive(user_id,role,content,ts) "
            f"SELECT user_id,role,content,ts FROM messages "
            f"WHERE id IN ({','.join('?'*len(to_delete))})", to_delete)
        conn.execute(f"DELETE FROM messages WHERE id IN ({','.join('?'*len(to_delete))})",
                     to_delete)
    # eski özeti sil, yenisini yaz (yeni özet eskiyi kapsıyor)
    conn.execute("DELETE FROM summaries WHERE user_id=?", (str(user_id),))
    conn.execute("INSERT INTO summaries(user_id,ozet,mesaj_araligi,ts) VALUES(?,?,?,?)",
                 (str(user_id), ozet, f"~{len(ids)} mesaj", datetime.now().isoformat()))
    conn.commit()
    conn.close()


# ─── LAZY FETCH (Katman 3) ──────────────────────────────────────────────────
# Hiro son 12 mesaj + özette olmayan bir şeyi ararsa (ör. "3 hafta önce ne
# konuştuk"), arşivdeki eski mesajları + eski özetleri arar. Her mesajda değil,
# sadece ihtiyaç halinde — token dostu.
def search_history(user_id: str, query: str, limit: int = 15) -> dict:
    """Arşivlenmiş eski mesajlarda + özetlerde kelime ara. Lazy fetch tool'u kullanır."""
    init_db()
    conn = db()
    terms = [t.strip() for t in query.lower().split() if len(t.strip()) > 2]
    if not terms:
        conn.close()
        return {"matches": [], "summary": get_summary(user_id)}

    # arşivde LIKE araması (her terim için)
    like_clauses = " OR ".join("LOWER(content) LIKE ?" for _ in terms)
    params = [str(user_id)] + [f"%{t}%" for t in terms]
    rows = conn.execute(
        f"SELECT role, content, ts FROM archive WHERE user_id=? AND ({like_clauses}) "
        f"ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
    conn.close()

    matches = [{"role": r["role"], "content": r["content"], "when": (r["ts"] or "")[:10]}
               for r in rows]
    return {"matches": matches, "summary": get_summary(user_id),
            "not": "arşivde eşleşme yoksa bu konu hiç konuşulmamış olabilir"}


def archive_count(user_id: str) -> int:
    init_db()
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM archive WHERE user_id=?",
                     (str(user_id),)).fetchone()["c"]
    conn.close()
    return n