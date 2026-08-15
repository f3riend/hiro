from nanowakeword import NanoInterpreter
from app.core.settings import settings
from loguru import logger
import numpy as np
import pyaudio
import time


wakeword = logger.bind("wakeword")

interpreter = NanoInterpreter.load_model("./models/hey_hiro_v1.onnx")
pa = pyaudio.PyAudio()
stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1280)

THRESHOLD = 0.9      
COOLDOWN = 1.5         
last_trigger = 0

wakeword.info(f"Listening {THRESHOLD}")
try:
    while True:
        chunk = np.frombuffer(stream.read(1280, exception_on_overflow=False), dtype=np.int16)
        score = interpreter.predict(chunk).score
        now = time.time()
        if score > THRESHOLD and (now - last_trigger) > COOLDOWN:
            wakeword.info(f"  >>> TRIGERED{score:.3f}")
            last_trigger = now
            interpreter.reset()
except KeyboardInterrupt:
    stream.stop_stream(); stream.close(); pa.terminate()