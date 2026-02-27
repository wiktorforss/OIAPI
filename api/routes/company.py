from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta, timezone
import httpx
import os

from ..database import get_db
from ..models import InsiderTrade, MyTrade, StockPrice
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/company", tags=["Company"])

POLYGON_KEY         = os.getenv("POLYGON_KEY", "")
CACHE_MAX_AGE_HOURS = 24


def is_purchase(tx_type: str | None) -> bool:
    return (tx_type or "").upper() in ("P", "P - PURCHASE", "PURCHASE")

def is_sale(tx_type: str | None) -> bool:
    return (tx_type or "").upper() in ("S", "S - SALE", "SALE")


# ── Polygon fetcher ───────────────────────────────────────────────────────────

async def fetch_polygon_prices(ticker: str) -> list[dict]:
    if not POLYGON_KEY:
        raise HTTPException(status_code=500, detail="POLYGON_KEY not set in .env")

    date_from = (datetime.now(timezone.utc) - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    date_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    all_prices = []
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{date_from}/{date_to}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_KEY}"
    )

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        while url:
            resp = await client.get(url)

            if resp.status_code == 403:
                raise HTTPException(status_code=403, detail="Invalid Polygon API key")
            if resp.status_code == 429:
                raise HTTPException(status_code=429, detail="Polygon rate limit — wait 60s and retry")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Polygon returned {resp.status_code}")

            data = resp.json()

            if data.get("status") == "ERROR":
                raise HTTPException(status_code=502, detail=data.get("error", "Polygon error"))

            for r in data.get("results", []):
                date_str = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                all_prices.append({
                    "date":   date_str,
                    "open":   round(r.get("o", 0), 2),
                    "high":   round(r.get("h", 0), 2),
                    "low":    round(r.get("l", 0), 2),
                    "close":  round(r["c"], 2),
                    "volume": int(r.get("v", 0)),
                })

            next_url = data.get("next_url")
            url = f"{next_url}&apiKey={POLYGON_KEY}" if next_url else None

    return all_prices


# ── Cache helpers ─────────────────────────────────────────────────────────────

def get_cached_prices(ticker: str, db: Session) -> list[dict] | None:
    latest = (
        db.query(StockPrice)
        .filter(StockPrice.ticker == ticker)
        .order_by(desc(StockPrice.fetched_at))
        .first()
    )
    if not latest:
        return None
    age = datetime.now(timezone.utc) - latest.fetched_at.replace(tzinfo=timezone.utc)
    if age.total_seconds() > CACHE_MAX_AGE_HOURS * 3600:
        return None
    rows = (
        db.query(StockPrice)
        .filter(StockPrice.ticker == ticker)
        .order_by(StockPrice.price_date)
        .all()
    )
    return [
        {"date": r.price_date.isoformat(), "close": r.close, "open": r.open, "high": r.high, "low": r.low}
        for r in rows
    ]


def save_prices_to_cache(ticker: str, prices: list[dict], db: Session):
    for p in prices:
        existing = db.query(StockPrice).filter(
            StockPrice.ticker     == ticker,
            StockPrice.price_date == p["date"],
        ).first()
        if existing:
            existing.close      = p["close"]
            existing.open       = p.get("open")
            existing.high       = p.get("high")
            existing.low        = p.get("low")
            existing.volume     = p.get("volume")
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            db.add(StockPrice(
                ticker     = ticker,
                price_date = p["date"],
                close      = p["close"],
                open       = p.get("open"),
                high       = p.get("high"),
                low        = p.get("low"),
                volume     = p.get("volume"),
            ))
    db.commit()


def nearest_price(price_map: dict, date_str: str) -> float | None:
    if not date_str:
        return None
    if date_str in price_map:
        return price_map[date_str]
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    for delta in [1, -1, 2, -2, 3, -3, 4, -4]:
        candidate = (d + timedelta(days=delta)).strftime("%Y-%m-%d")
        if candidate in price_map:
            return price_map[candidate]
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/prices/refresh/{ticker}")
async def refresh_prices(
    ticker: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticker = ticker.upper()
    prices = await fetch_polygon_prices(ticker)
    save_prices_to_cache(ticker, prices, db)
    return {
        "ticker":  ticker,
        "cached":  len(prices),
        "message": f"Refreshed {len(prices)} days of price data for {ticker}",
    }


@router.get("/{ticker}")
async def get_company(
    ticker: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticker = ticker.upper()

    prices = get_cached_prices(ticker, db)
    if prices is None:
        try:
            raw    = await fetch_polygon_prices(ticker)
            save_prices_to_cache(ticker, raw, db)
            prices = raw
        except HTTPException:
            prices = []
        except Exception:
            prices = []

    price_map = {p["date"]: p["close"] for p in prices}

    insider_trades = (
        db.query(InsiderTrade)
        .filter(InsiderTrade.ticker == ticker)
        .order_by(InsiderTrade.trade_date)
        .all()
    )
    my_trades = (
        db.query(MyTrade)
        .filter(MyTrade.ticker == ticker)
        .order_by(MyTrade.trade_date)
        .all()
    )

    purchases = [t for t in insider_trades if is_purchase(t.transaction_type)]
    sales     = [t for t in insider_trades if is_sale(t.transaction_type)]

    return {
        "ticker":      ticker,
        "yahoo_url":   f"https://finance.yahoo.com/quote/{ticker}",
        "prices":      prices,
        "price_count": len(prices),
        "insider_trades": [
            {
                "id":               t.id,
                "date":             t.trade_date.isoformat() if t.trade_date else None,
                "price_at_date":    nearest_price(price_map, t.trade_date.isoformat()) if t.trade_date else None,
                "insider_name":     t.insider_name,
                "insider_title":    t.insider_title,
                "transaction_type": t.transaction_type,
                "is_purchase":      is_purchase(t.transaction_type),
                "price":            t.price,
                "qty":              t.qty,
                "value":            t.value,
            }
            for t in insider_trades
        ],
        "my_trades": [
            {
                "id":            t.id,
                "date":          t.trade_date.isoformat() if t.trade_date else None,
                "price_at_date": nearest_price(price_map, t.trade_date.isoformat()) if t.trade_date else None,
                "trade_type":    t.trade_type,
                "shares":        t.shares,
                "price":         t.price,
                "total_value":   t.total_value,
                "notes":         t.notes,
                "return_1m":     t.performance.return_1m if t.performance else None,
                "return_3m":     t.performance.return_3m if t.performance else None,
            }
            for t in my_trades
        ],
        "summary": {
            "total_insider_purchases":      len(purchases),
            "total_insider_sales":          len(sales),
            "total_insider_purchase_value": sum(t.value or 0 for t in purchases),
            "total_insider_sale_value":     sum(t.value or 0 for t in sales),
            "my_trade_count":               len(my_trades),
            "my_buy_count":                 sum(1 for t in my_trades if t.trade_type == "buy"),
            "my_sell_count":                sum(1 for t in my_trades if t.trade_type == "sell"),
        },
    }
