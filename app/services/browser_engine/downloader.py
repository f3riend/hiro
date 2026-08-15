"""
downloader.py — yt-dlp ile dayanıklı video indirme
═══════════════════════════════════════════════════════════════════════════
yt-dlp ağır işi yapar: parçalı indirir, birleştirir, --continue ile kaldığı
yerden devam eder. Bizim işimiz: doğru bayraklarla çalıştır, çıkış kodunu
döndür. Çıkış kodu 0 değilse "yarım kaldı" demektir — scheduler bir sonraki
tick'te tekrar çağırır, yt-dlp kaldığı yerden devam eder.

Kesinti dayanıklılığı:
  --continue          → yarım .part dosyasından devam (tek dosya)
  --fragment-retries  → HLS segment düzeyinde retry (tek çalıştırma içinde)
  parçalı videolarda yarım segmentler klasörde kalır; tekrar çalışınca
  yt-dlp indirilmişleri atlar, eksikleri tamamlar, birleştirir.
"""

import subprocess
from pathlib import Path
from urllib.parse import urlparse

# indirilenler buraya (repo kökü / downloads)
_here = Path(__file__).resolve()
if _here.parent.name == "browser_engine":
    ROOT = _here.parents[3]
else:
    ROOT = _here.parent
DOWNLOAD_DIR = ROOT / "downloads"


def get_referer(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}/"
    except Exception:
        return url


def build_command(url: str, out_dir: Path = None, title: str = None) -> list:
    """yt-dlp komutunu liste olarak kur (subprocess için). --continue dahil."""
    out_dir = out_dir or DOWNLOAD_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # başlık verilmişse onu kullan, yoksa yt-dlp'nin kendi başlığı
    out_tmpl = f"{title}.%(ext)s" if title else "%(title)s.%(ext)s"
    return [
        "yt-dlp",
        "--continue",                 # kaldığı yerden devam — KRİTİK
        "--no-check-certificate",
        "--no-warnings",
        "--ignore-errors",
        "--no-abort-on-error",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--referer", get_referer(url),
        "--retries", "10",
        "--fragment-retries", "10",
        "--concurrent-fragments", "5",
        "-o", str(out_dir / out_tmpl),
        url,
    ]


def download(url: str, out_dir: Path = None, title: str = None, timeout: int = 3600) -> dict:
    """Videoyu indir. Dönüş: {ok, returncode, url, done}.
    ok/done True → indirme tamamlandı. False → yarım kaldı, tekrar denenebilir
    (yt-dlp --continue ile kaldığı yerden devam eder)."""
    cmd = build_command(url, out_dir, title)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        done = proc.returncode == 0
        return {
            "ok": done,
            "done": done,
            "returncode": proc.returncode,
            "url": url,
            "stdout_tail": proc.stdout.strip()[-300:] if proc.stdout else "",
            "stderr_tail": proc.stderr.strip()[-300:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        # timeout → yarım kaldı sayılır, bir dahaki sefere devam
        return {"ok": False, "done": False, "returncode": -1, "url": url,
                "error": "timeout — yarım kaldı, tekrar denenecek"}
    except FileNotFoundError:
        return {"ok": False, "done": False, "returncode": -2, "url": url,
                "error": "yt-dlp kurulu değil (pip install yt-dlp / pacman -S yt-dlp)"}
    except Exception as e:
        return {"ok": False, "done": False, "returncode": -3, "url": url, "error": str(e)}