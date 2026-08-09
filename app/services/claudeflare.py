from app.core.settings import settings
from loguru import logger
from platform import system
import subprocess
import os



CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE")
WHICH_ONE = "cloudflared-linux-amd64" if system() == "Linux" else "cloudflared-windows-amd64.exe"
CLOUDFLARED_BIN = f"./assets/{WHICH_ONE}"


claudeflare_info = logger.bind(module="claudeflare_info")

def connect_claudeflare():
    if not CLOUDFLARE_TOKEN:
        claudeflare_info.warning("CLOUDFLARE token not set, skipping tunnel")
        return
    
    tunnel_cmd = [
        CLOUDFLARED_BIN,
        "tunnel",
        "--no-autoupdate",
        "run",
        "--token",
        CLOUDFLARE_TOKEN
    ]

    try:
        claudeflare_info.info("Cloudflare tunnel starting...")
        subprocess.run(tunnel_cmd, check=True)
    except Exception as e:
        claudeflare_info.error(f"Tunnel error: {e}")

