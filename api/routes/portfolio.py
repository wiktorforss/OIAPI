from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from ..database import get_db
from ..models import MyTrade, StockPrice
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


def _latest_price(ticker: str, db: Session) -> tuple[float | None, str | None]:
    """Get the most recent cached close price and its date for a ticker."""
    row = (
        db.query(StockPrice)
        .filter(StockPrice.ticker == ticker.upper())
        .order_by(desc(StockPrice.price_date))
        .first()
    )
    if row:
        return row.close, row.price_date.isoformat()
    return None, None


@router.get("/")
def get_portfolio(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Compute current holdings from the logged-in user's buy/sell trades.
    Returns one row per ticker showing net position, avg cost, current value and P&L.
    """
    trades = (
        db.query(MyTrade)
        .filter(MyTrade.user_id == current_user.id)
        .order_by(MyTrade.trade_date)
        .all()
    )

    holdings: dict[str, dict] = defaultdict(lambda: {
        "shares":          0.0,
        "cost_basis":      0.0,
        "realized_pnl":    0.0,
        "trade_count":     0,
        "first_buy_date":  None,
        "last_trade_date": None,
    })

    for t in trades:
        h = holdings[t.ticker]
        h["trade_count"]     += 1
        h["last_trade_date"]  = t.trade_date.isoformat() if t.trade_date else None

        if t.trade_type == "buy":
            h["shares"]     += t.shares
            h["cost_basis"] += t.shares * t.price
            if h["first_buy_date"] is None and t.trade_date:
                h["first_buy_date"] = t.trade_date.isoformat()

        elif t.trade_type == "sell":
            if h["shares"] > 0:
                avg_cost    = h["cost_basis"] / h["shares"]
                sold_shares = min(t.shares, h["shares"])
                h["realized_pnl"] += sold_shares * (t.price - avg_cost)
                h["cost_basis"]   -= sold_shares * avg_cost
                h["shares"]       -= sold_shares
                h["shares"]        = max(h["shares"], 0)
                h["cost_basis"]    = max(h["cost_basis"], 0)

    result = []
    for ticker, h in holdings.items():
        if h["shares"] <= 0 and h["trade_count"] == 0:
            continue

        current_price, price_date = _latest_price(ticker, db)
        avg_cost = h["cost_basis"] / h["shares"] if h["shares"] > 0 else 0
        market_value = (current_price * h["shares"]) if current_price and h["shares"] > 0 else None
        unrealized_pnl = (market_value - h["cost_basis"]) if market_value is not None else None
        unrealized_pct = (
            round((unrealized_pnl / h["cost_basis"]) * 100, 2)
            if unrealized_pnl is not None and h["cost_basis"] > 0
            else None
        )

        result.append({
            "ticker":          ticker,
            "shares":          round(h["shares"], 6),
            "avg_cost":        round(avg_cost, 4),
            "cost_basis":      round(h["cost_basis"], 2),
            "current_price":   current_price,
            "price_date":      price_date,
            "market_value":    round(market_value, 2) if market_value is not None else None,
            "unrealized_pnl":  round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
            "unrealized_pct":  unrealized_pct,
            "realized_pnl":    round(h["realized_pnl"], 2),
            "trade_count":     h["trade_count"],
            "first_buy_date":  h["first_buy_date"],
            "last_trade_date": h["last_trade_date"],
        })

    result.sort(key=lambda x: (x["market_value"] or 0), reverse=True)
    return result