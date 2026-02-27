from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta, date, timezone
import httpx
import os

from ..database import get_db
from ..models import InsiderTrade, MyTrade, StockPrice
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/company", tags=["Company"])

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
CACHE_MAX_AGE_HOURS = 24  # Refresh prices once per day


# ── Alpha Vantage fetcher ─────────────────────────────────────────────────────

async def fetch_alpha_vantage(ticker: str) -> list[dict]:
    """
    Fetch full daily OHLCV history from Alpha Vantage (outputsize=full = 20 years).
    Returns list of {date, open, high, low, close, volume}.
    """
    if not ALPHA_VANTAGE_KEY:
        raise HTTPException(status_code=500, detail="ALPHA_VANTAGE_KEY not set in .env")

    url = "https://www.alphavantage.co/query"
    params = {
        "function":   "TIME_SERIES_DAILY",
        "symbol":     ticker,
        "outputsize": "full",
        "apikey":     ALPHA_VANTAGE_KEY,
        "datatype":   "json",
    }

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Alpha Vantage returned {resp.status_code}")

    data = resp.json()

    if "Error Message" in data:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found on Alpha Vantage")

    if "Note" in data:
        raise HTTPException(status_code=429, detail="Alpha Vantage rate limit reached (25 req/day on free tier)")

    if "Information" in data:
        raise HTTPException(status_code=429, detail="Alpha Vantage API limit reached")

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        raise HTTPException(status_code=502, detail="No price data returned from Alpha Vantage")

    prices = []
    for date_str, ohlcv in sorted(ts.items()):  # sorted = oldest first
        try:
            prices.append({
                "date":   date_str,
                "open":   float(ohlcv["1. open"]),
                "high":   float(ohlcv["2. high"]),
                "low":    float(ohlcv["3. low"]),
                "close":  round(float(ohlcv["4. close"]), 2),
                "volume": int(ohlcv["5. volume"]),
            })
        except (KeyError, ValueError):
            continue

    return prices


# ── Cache helpers ─────────────────────────────────────────────────────────────

def get_cached_prices(ticker: str, db: Session) -> list[dict] | None:
    """
    Return cached prices if they exist and are fresh (< CACHE_MAX_AGE_HOURS old).
    Returns None if cache is missing or stale.
    """
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
        return None  # Stale — trigger a refresh

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
    """Upsert fetched prices into stock_prices table."""
    for p in prices:
        existing = db.query(StockPrice).filter(
            StockPrice.ticker     == ticker,
            StockPrice.price_date == p["date"],
        ).first()
        if existing:
            existing.close     = p["close"]
            existing.open      = p.get("open")
            existing.high      = p.get("high")
            existing.low       = p.get("low")
            existing.volume    = p.get("volume")
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
    """Find close price on date or nearest trading day within ±4 days."""
    if not date_str:
        return None
    if date_str in price_map:
        return price_map[date_str]
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
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
    """Force a fresh fetch from Alpha Vantage and update the cache."""
    ticker = ticker.upper()
    prices = await fetch_alpha_vantage(ticker)
    save_prices_to_cache(ticker, prices, db)
    return {"ticker": ticker, "cached": len(prices), "message": f"Refreshed {len(prices)} days of price data"}


@router.get("/{ticker}")
async def get_company(
    ticker: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticker = ticker.upper()

    # Try cache first
    prices = get_cached_prices(ticker, db)

    # If no cache or stale, fetch from Alpha Vantage
    if prices is None:
        try:
            raw = await fetch_alpha_vantage(ticker)
            save_prices_to_cache(ticker, raw, db)
            prices = [{"date": p["date"], "close": p["close"], "open": p.get("open"), "high": p.get("high"), "low": p.get("low")} for p in raw]
        except HTTPException as e:
            # Return with empty prices but don't crash — still show trade data
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

    purchases = [t for t in insider_trades if "Purchase" in (t.transaction_type or "")]
    sales     = [t for t in insider_trades if "Sale"     in (t.transaction_type or "")]

    return {
        "ticker":    ticker,
        "yahoo_url": f"https://finance.yahoo.com/quote/{ticker}",
        "prices":    prices,
        "price_count": len(prices),
        "insider_trades": [
            {
                "id":               t.id,
                "date":             t.trade_date.isoformat() if t.trade_date else None,
                "price_at_date":    nearest_price(price_map, t.trade_date.isoformat()) if t.trade_date else None,
                "insider_name":     t.insider_name,
                "insider_title":    t.insider_title,
                "transaction_type": t.transaction_type,
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
