"""
tmdb.py — TMDB metadata çekici (script.js'in Python portu)
═══════════════════════════════════════════════════════════════════════════
Bir başlık (JP ya da EN) → TMDB'den zengin metadata: kapak, oyuncu, özet,
tür, sezon/bölüm, IMDB id... Senin MediaTracker._buildEntry mantığının aynısı.

İSİM EŞLEŞTİRME (senin isteğin):
Animecix hem Japonca hem İngilizce ad tutuyor. search_best() ikisini de
TMDB'de aratır, en iyi eşleşeni seçer — biri tutmazsa diğeri tutar.
Bu, "Tensura" vs "Tensei shitara Slime" sorununu çözer.
"""

import urllib.request
import urllib.parse
import json
from difflib import SequenceMatcher

TMDB_KEY = "d31b64f5d64d10f8f6f96ebc395fa902"
BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"
IMG_BACKDROP = "https://image.tmdb.org/t/p/w1280"


def _get(path, **params):
    params["api_key"] = TMDB_KEY
    params.setdefault("language", "en-US")
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def _similar(a, b):
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def search(query, media_type="tv"):
    """TMDB'de ara. media_type: tv | movie | multi. Sonuç listesi döner."""
    try:
        data = _get(f"search/{media_type}", query=query)
        return data.get("results", [])
    except Exception:
        return []


def search_best(names, media_type="tv"):
    """Birden çok ad (JP + EN) ver, en iyi eşleşen TMDB sonucunu döndür.
    names: ["Tensei shitara Slime", "That Time I Got Reincarnated as a Slime"]
    Her adı aratır, tüm sonuçları toplar, ada en yakın olanı seçer."""
    names = [n for n in names if n and n.strip()]
    if not names:
        return None

    candidates = {}  # id -> (result, best_score)
    for name in names:
        for r in search(name, media_type):
            rid = r.get("id")
            if not rid:
                continue
            # bu sonucun adıyla (name/original_name) sorgu ne kadar benziyor
            score = max(
                _similar(name, r.get("name", "")),
                _similar(name, r.get("original_name", "")),
            )
            # popülerlik küçük bir bonus (aynı skorda popüleri seç)
            score += min(r.get("popularity", 0), 100) / 1000
            if rid not in candidates or score > candidates[rid][1]:
                candidates[rid] = (r, score)

    if not candidates:
        return None
    # en yüksek skorlu
    best = max(candidates.values(), key=lambda x: x[1])
    return best[0]


def fetch_details(tmdb_id, media_type="tv"):
    """Bir TMDB id için tam metadata çek (credits, keywords, external_ids dahil).
    MediaTracker._buildEntry ile aynı alanlar."""
    d = _get(f"{media_type}/{tmdb_id}",
             append_to_response="credits,keywords,external_ids")

    is_tv = media_type == "tv"
    release = d.get("first_air_date") if is_tv else d.get("release_date")
    crew = (d.get("credits", {}) or {}).get("crew", []) or []
    cast = (d.get("credits", {}) or {}).get("cast", []) or []
    kw_root = d.get("keywords", {}) or {}
    keywords = kw_root.get("results") if is_tv else kw_root.get("keywords")
    poster = d.get("poster_path")
    backdrop = d.get("backdrop_path")

    return {
        "id": tmdb_id,
        "type": "anime" if is_tv and _is_anime(d) else media_type,
        "title": d.get("name") if is_tv else d.get("title"),
        "original_title": d.get("original_name") if is_tv else d.get("original_title"),
        "year": (release or "")[:4],
        "release_date": release or "",
        "poster_url": IMG + poster if poster else "",
        "backdrop_url": IMG_BACKDROP + backdrop if backdrop else "",
        "rating": d.get("vote_average", 0),
        "votes": d.get("vote_count", 0),
        "directors": ", ".join(c["name"] for c in crew if c.get("job") == "Director"),
        "writers": ", ".join(c["name"] for c in crew if c.get("job") in ("Writer", "Screenplay")),
        "creators": ", ".join(c.get("name", "") for c in (d.get("created_by") or [])),
        "cast": ", ".join(c.get("name", "") for c in cast[:5]),
        "genres": ", ".join(g["name"] for g in (d.get("genres") or [])),
        "keywords": ", ".join(k["name"] for k in (keywords or [])),
        "overview": d.get("overview", ""),
        "tagline": d.get("tagline", ""),
        "status": d.get("status", ""),
        "original_language": d.get("original_language", ""),
        "seasons": d.get("number_of_seasons", 0),
        "episodes": d.get("number_of_episodes", 0),
        "in_production": d.get("in_production", False),
        "last_air_date": d.get("last_air_date", ""),
        "imdb_id": (d.get("external_ids", {}) or {}).get("imdb_id", ""),
        "tmdb_url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
    }


def _is_anime(d):
    # tür 16 (Animation) + Japonca = anime say
    genres = [g.get("id") for g in (d.get("genres") or [])]
    return 16 in genres and d.get("original_language") == "ja"


def _normalize_type(media_type):
    # TMDB'de anime diye ayrı tip yok — animeler "tv" altında. Otomatik çevir.
    return "tv" if media_type == "anime" else media_type


def get_metadata(names, media_type="tv"):
    """Tek çağrı: ad(lar) → en iyi eşleşme → tam metadata. Kütüphane tool'u bunu kullanır.
    names: tek string ya da liste (JP+EN). Bulamazsa None.
    media_type "anime" verilse bile TMDB'de tv olarak aranır (anime tv altında)."""
    if isinstance(names, str):
        names = [names]
    mt = _normalize_type(media_type)
    best = search_best(names, mt)
    if not best:
        return None
    return fetch_details(best["id"], mt)