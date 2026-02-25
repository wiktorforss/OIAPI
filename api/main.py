from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from .database import engine
from .models import Base
from .routes import insider, my_trades, performance


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup if they don't exist
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Insider Trading Tracker API",
    description=(
        "Personal API for tracking SEC insider trades from openinsider.com "
        "alongside your own buy/sell decisions and performance data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — restrict to your frontend domain in production
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(insider.router)
app.include_router(my_trades.router)
app.include_router(performance.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "Insider Trading Tracker API is running"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}