from functools import lru_cache
from pathlib import Path
import yaml
import os

CONFIG_PATH = Path.cwd() / "config" / "config.yaml"

@lru_cache
def load_config():
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    

    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    

    if 'celery' in config:
        config['celery']['broker'] = redis_url
        config['celery']['backend'] = redis_url
    
    return config