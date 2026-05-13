from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router

app = FastAPI(title="Crestview Onboarding Pipeline API")
app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory="ui", html=True), name="ui")
