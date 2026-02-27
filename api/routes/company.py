from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import httpx
from datetime import datetime, timedelta, timezone

from ..database import get_db
from ..models import InsiderTrade, MyTrade
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/company", tags=["Company"])


async def fetch_yahoo_prices(ticker: str) -> list[dict]:
    """
    Fetch 5 years of daily close prices from Yahoo Finance.
    Returns list of {date: "YYYY-MM-DD", close: float}.
    """
    now    = int(datetime.now(timezone.utc).timestamp())
    start  = int((datetime.now(timezone.utc) - timedelta(days=365 * 5)).timestamp())

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1":  start,
        "period2":  now,
        "interval": "1d",
        "events":   "history",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Yahoo Finance returned {resp.status_code}")

    data = resp.json()
    try:
        result     = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes     = result["indicators"]["quote"][0]["close"]

        prices = []
        for ts, c in zip(timestamps, closes):
            if c is None:
                continue
            # Use UTC date to avoid timezone day-shift issues
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            prices.append({"date": date_str, "close": round(c, 2)})

        return prices

    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse Yahoo response: {e}")


@router.get("/{ticker}")
async def get_company(
    ticker: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticker = ticker.upper()

    try:
        prices = await fetch_yahoo_prices(ticker)
    except HTTPException:
        prices = []
    except Exception as e:
        prices = []

    # Build a fast lookup: date -> close price
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

    def nearest_price(date_str: str) -> float | None:
        """Find price on date or nearest trading day within ±3 days."""
        if not date_str:
            return None
        if date_str in price_map:
            return price_map[date_str]
        # Search nearby dates (weekends, holidays)
        from datetime import date
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None
        for delta in [1, -1, 2, -2, 3, -3]:
            candidate = (d + timedelta(days=delta)).strftime("%Y-%m-%d")
            if candidate in price_map:
                return price_map[candidate]
        return None

    return {
        "ticker":    ticker,
        "yahoo_url": f"https://finance.yahoo.com/quote/{ticker}",
        "prices":    prices,
        "insider_trades": [
            {
                "id":               t.id,
                "date":             t.trade_date.isoformat() if t.trade_date else None,
                "price_at_date":    nearest_price(t.trade_date.isoformat()) if t.trade_date else None,
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
                "id":          t.id,
                "date":        t.trade_date.isoformat() if t.trade_date else None,
                "price_at_date": nearest_price(t.trade_date.isoformat()) if t.trade_date else None,
                "trade_type":  t.trade_type,
                "shares":      t.shares,
                "price":       t.price,
                "total_value": t.total_value,
                "notes":       t.notes,
                "return_1m":   t.performance.return_1m if t.performance else None,
                "return_3m":   t.performance.return_3m if t.performance else None,
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
