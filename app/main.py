from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from loguru import logger
import threading
import uvicorn


from app.services.claudeflare import connect_claudeflare
from app.core.settings import settings
from app.api.sound import sound

root_info = logger.bind(module="root")



def connect_api():
    uvicorn.run(
        app="app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=False  # Thread içinde reload çalışmaz
    )


app = FastAPI(
    title=settings.app.name,
    version=str(settings.app.version),
    description=settings.app.description
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors.allow_origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)

app.include_router(sound)

@app.get("/")
def root():
    return {
        "status": "healthy",
        "name": settings.app.name,
        "description": settings.app.description,
        "version": settings.app.version
    }





if __name__ == "__main__":
    initiazlize_api = threading.Thread(target=connect_api,daemon=True)
    initiazlize_api.start()
    root_info.info("API initiazlized")
    

    connect_claudeflare()