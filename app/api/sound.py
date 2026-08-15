from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import tempfile
import time
import shutil
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

from app.services.hiro_core import chat
from app.api.hiro import agent   # tek agent örneğini paylaş (çift kurulum olmasın)

load_dotenv()

client = OpenAI()

sound = APIRouter()


@sound.post("/transcribe")
async def transcribe(file: UploadFile = File(...), talk: bool = True):
    """Sesi yazıya çevir. talk=True ise yazıyı Hiro'ya besle, cevabını da döndür.
    Akıllı saat tek istek atar; transkript + Hiro cevabı birlikte döner."""
    allowed_extensions = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".webm"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Desteklenmeyen format: {ext}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        start = time.time()
        with open(tmp_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="tr",
                response_format="verbose_json",
                temperature=0,
                prompt="Türkçe konuşma. Noktalama ve büyük/küçük harf kurallarına dikkat et.",
            )
        elapsed = round(time.time() - start, 2)
        text = result.text.strip()

        response = {
            "text": text,
            "language": result.language,
            "elapsed_seconds": elapsed,
        }

        # yazıyı Hiro'ya ilet, cevabını da ekle
        if talk and text:
            reply = chat(agent, text)
            response["reply"] = reply

        return JSONResponse(response)

    finally:
        Path(tmp_path).unlink(missing_ok=True)