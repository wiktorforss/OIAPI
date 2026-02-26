from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
import httpx
from datetime import datetime, timedelta

from ..database import get_db
from ..models import InsiderTrade, MyTrade
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/company", tags=["Company"])


async def fetch_yahoo_prices(ticker: str, period: str = "2y") -> list[dict]:
    """
    Fetch historical OHLC price data from Yahoo Finance.
    Returns list of {date, close} dicts.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1": int((datetime.now() - timedelta(days=730)).timestamp()),
        "period2": int(datetime.now().timestamp()),
        "interval": "1d",
        "range": period,
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Yahoo Finance returned {resp.status_code}")

    data = resp.json()
    try:
        result    = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes     = result["indicators"]["quote"][0]["close"]
        return [
            {"date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), "close": round(c, 2)}
            for ts, c in zip(timestamps, closes)
            if c is not None
        ]
    except (KeyError, IndexError, TypeError):
        return []


@router.get("/{ticker}")
async def get_company(
    ticker: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns everything needed for the company detail page:
    - Historical price data from Yahoo Finance
    - All insider trades for this ticker
    - Your personal trades for this ticker
    - Summary stats
    """
    ticker = ticker.upper()

    # Fetch price history from Yahoo
    try:
        prices = await fetch_yahoo_prices(ticker)
    except Exception:
        prices = []

    # Insider trades
    insider_trades = (
        db.query(InsiderTrade)
        .filter(InsiderTrade.ticker == ticker)
        .order_by(InsiderTrade.trade_date)
        .all()
    )

    # My trades
    my_trades = (
        db.query(MyTrade)
        .filter(MyTrade.ticker == ticker)
        .order_by(MyTrade.trade_date)
        .all()
    )

    # Summary stats
    purchases = [t for t in insider_trades if "Purchase" in (t.transaction_type or "")]
    sales     = [t for t in insider_trades if "Sale"     in (t.transaction_type or "")]

    return {
        "ticker": ticker,
        "yahoo_url": f"https://finance.yahoo.com/quote/{ticker}",
        "prices": prices,
        "insider_trades": [
            {
                "id":               t.id,
                "date":             t.trade_date.isoformat() if t.trade_date else None,
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
                "id":         t.id,
                "date":       t.trade_date.isoformat() if t.trade_date else None,
                "trade_type": t.trade_type,
                "shares":     t.shares,
                "price":      t.price,
                "total_value": t.total_value,
                "notes":      t.notes,
                "return_1m":  t.performance.return_1m if t.performance else None,
                "return_3m":  t.performance.return_3m if t.performance else None,
            }
            for t in my_trades
        ],
        "summary": {
            "total_insider_purchases":       len(purchases),
            "total_insider_sales":           len(sales),
            "total_insider_purchase_value":  sum(t.value or 0 for t in purchases),
            "total_insider_sale_value":      sum(t.value or 0 for t in sales),
            "my_trade_count":                len(my_trades),
            "my_buy_count":                  sum(1 for t in my_trades if t.trade_type == "buy"),
            "my_sell_count":                 sum(1 for t in my_trades if t.trade_type == "sell"),
        },
    }
