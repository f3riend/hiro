from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import tempfile
import time
import shutil
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()

sound = APIRouter()


@sound.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    allowed_extensions = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".webm"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Desteklenmeyen format: {ext}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        print(f"🎙️ {file.filename} işleniyor...")
        start = time.time()

        with open(tmp_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="tr",
                response_format="verbose_json",
                temperature=0,  # daha tutarlı/deterministik çıktı
                prompt="Türkçe konuşma. Noktalama ve büyük/küçük harf kurallarına dikkat et.",
            )

        segments_list = [
            {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            for seg in (result.segments or [])
        ]

        elapsed = round(time.time() - start, 2)
        print(f"✅ Tamamlandı! ({elapsed} sn)")

        return JSONResponse({
            "text": result.text.strip(),
            "segments": segments_list,
            "language": result.language,
            "elapsed_seconds": elapsed,
        })

    finally:
        Path(tmp_path).unlink(missing_ok=True)