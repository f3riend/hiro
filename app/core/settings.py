from pydantic import BaseModel
from typing import List
from app.core.config import load_config
from dotenv import load_dotenv
import os


class AppSettings(BaseModel):
    name: str
    description: str
    env: str
    version: float


class ServerSettings(BaseModel):
    host: str
    port: int
    reload: bool


class LoggerSettings(BaseModel):
    level: str
    format: str
    rotation: str


class CorsSettings(BaseModel):
    allow_origins: List[str]
    allow_methods: List[str]
    allow_headers: List[str]
    allow_credentials: bool


class Ai(BaseModel):
    provider: str
    model: str
    auth: str = "apikey"   # apikey | oauth  (config.yaml'da yoksa apikey varsayılır)


class Settings(BaseModel):
    app: AppSettings
    server: ServerSettings
    logger: LoggerSettings
    cors: CorsSettings
    ai: Ai


def get_settings() -> Settings:
    raw = load_config()
    load_dotenv()
    if 'REDIS_URL' in os.environ and 'celery' in raw:
        raw['celery']['broker'] = os.environ['REDIS_URL']
        raw['celery']['backend'] = os.environ['REDIS_URL']
    return Settings(**raw)


settings = get_settings()

from app.core.logging import setup_logging
setup_logging()