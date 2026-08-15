"""
video_detector.py — Extension'sız video URL yakalayıcı (CDP tabanlı)
═══════════════════════════════════════════════════════════════════════════
Senin Chrome extension'ının (Media Vault) yaptığı işi extension olmadan yapar.
Extension iki yol kullanıyordu:
  1. injected.js  → sayfanın fetch/XHR/video.src fonksiyonlarını hook'lardı
  2. background.js → chrome.webRequest ile HTTP isteklerini dinlerdi

CDP'nin Network domain'i İKİSİNİ BİRDEN tek katmanda yapar: sayfadaki tüm
fetch/XHR/media istekleri (hook'a gerek yok) network seviyesinde görünür.

Playwright'ın page.on("response"/"request") ile aynı video pattern'lerini
(.m3u8, .mp4, master.txt, .mpd ...) yakalar. Bulunca yt-dlp komutu üretir.

browser_engine ile uyumlu: bir sayfada oynatma tetiklenince (İzle'ye basınca)
ağ trafiğine düşen video URL'ini yakalar. Extension mantığının motor içine
gömülmüş hali.
"""

import re
import asyncio
from urllib.parse import urlparse

# injected.js / background.js'teki isVideoUrl ile AYNI pattern
VIDEO_RE = re.compile(r"\.(m3u8|mp4|webm|mkv|avi|mpd)(\?|$)", re.I)


def is_video_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    return bool(VIDEO_RE.search(url)) or "master.txt" in url


def detect_video_type(url: str) -> str:
    u = url.lower()
    if ".mp4" in u:
        return "MP4"
    if ".m3u8" in u:
        return "M3U8"
    if "master.txt" in u:
        return "HLS Master"
    if ".webm" in u:
        return "WEBM"
    if ".mkv" in u:
        return "MKV"
    if ".mpd" in u:
        return "DASH"
    return "Video"


def get_referer(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}/"
    except Exception:
        return url


def generate_ytdlp_command(url: str) -> str:
    # background.js'teki generateYtdlpCommand ile aynı — dayanıklı indirme bayrakları
    return (
        f'yt-dlp --no-check-certificate --no-warnings --ignore-errors '
        f'--no-abort-on-error '
        f'--user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        f'(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" '
        f'--referer "{get_referer(url)}" '
        f'--retries 10 --fragment-retries 10 --concurrent-fragments 5 '
        f'-o "%(title)s.%(ext)s" "{url}"'
    )


async def capture_video_urls(page, trigger_selector=None, wait_ms=8000):
    """Sayfada video URL'lerini yakala (extension'ın injected+background işi).

    page:             Playwright page (browser_engine'in kullandığı sayfa)
    trigger_selector: oynatmayı başlatan buton (ör. "İzle" / video). Verilirse
                      tıklanır, sonra ağ trafiği dinlenir. None ise sadece dinler.
    wait_ms:          tetikten sonra kaç ms video isteği beklensin

    dönüş: [{url, type, ytdlp}] — bulunan benzersiz video URL'leri
    """
    found = {}

    def on_request(request):
        url = request.url
        if is_video_url(url) and url not in found:
            found[url] = {
                "url": url,
                "type": detect_video_type(url),
                "ytdlp": generate_ytdlp_command(url),
            }

    def on_response(response):
        url = response.url
        if url in found:
            return
        # content-type ile de doğrula (background.js'teki header kontrolü gibi)
        ctype = (response.headers or {}).get("content-type", "")
        if is_video_url(url) or "mpegurl" in ctype or "video/" in ctype or "dash+xml" in ctype:
            if is_video_url(url):  # gürültüyü azalt: sadece net video uzantıları
                found[url] = {
                    "url": url,
                    "type": detect_video_type(url),
                    "ytdlp": generate_ytdlp_command(url),
                }

    # dinlemeyi tıklamadan ÖNCE başlat — video URL'i tıklama anında düşerse kaçmasın
    page.on("request", on_request)
    page.on("response", on_response)

    try:
        # 1) zaten DOM'da olan <video> src'lerini tara
        await _scan_dom(page, found)

        # 2) İzle butonuna bas → player açılsın
        if trigger_selector:
            try:
                await page.click(trigger_selector, timeout=8000)
            except Exception:
                # buton bulunamazsa video elementini doğrudan denemeye devam
                pass
            await asyncio.sleep(2)

        # 3) video elementini bul ve play() çağır (autoplay olmayabilir)
        #    ayrıca iframe içindeki player'ları da dene
        try:
            await page.evaluate("""
                async () => {
                    const vids = document.querySelectorAll('video');
                    for (const v of vids) {
                        try { v.muted = true; await v.play(); } catch(e) {}
                    }
                }
            """)
        except Exception:
            pass

        # 4) player iframe'i varsa içine gir, oradaki video'yu da oynat
        try:
            for frame in page.frames:
                try:
                    await frame.evaluate("""
                        async () => {
                            const vids = document.querySelectorAll('video');
                            for (const v of vids) {
                                try { v.muted = true; await v.play(); } catch(e) {}
                            }
                        }
                    """)
                except Exception:
                    pass
        except Exception:
            pass

        # 5) ağ trafiğinin video URL'ini üretmesini bekle (parça parça kontrol)
        waited = 0
        step = 1000
        while waited < wait_ms:
            await asyncio.sleep(step / 1000)
            waited += step
            await _scan_dom(page, found)
            if found:  # video yakalandıysa erken çık
                # bir tur daha bekle (master.m3u8'den sonra asıl stream gelebilir)
                await asyncio.sleep(2)
                await _scan_dom(page, found)
                break
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

    return list(found.values())


async def _scan_dom(page, found):
    """DOM'daki video/source src'lerini tara, bulunanları found'a ekle."""
    try:
        for frame in page.frames:
            try:
                urls = await frame.evaluate(
                    "() => [...document.querySelectorAll('video,source')]"
                    ".map(v => v.currentSrc || v.src).filter(Boolean)"
                )
                for u in urls:
                    if is_video_url(u) and u not in found:
                        found[u] = {"url": u, "type": detect_video_type(u),
                                    "ytdlp": generate_ytdlp_command(u)}
            except Exception:
                pass
    except Exception:
        pass