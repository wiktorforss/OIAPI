from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from .database import engine
from .models import Base
from .routes import insider, my_trades, performance
from .routes import auth, company, portfolio


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Insider Trading Tracker API", version="1.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(insider.router)
app.include_router(my_trades.router)
app.include_router(performance.router)
app.include_router(company.router)
app.include_router(portfolio.router)

@app.get("/", tags=["Health"])
def root():
    return {"status": "ok"}

@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
