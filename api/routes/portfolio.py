from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc
from collections import defaultdict

from ..database import get_db
from ..models import MyTrade, StockPrice
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


def _latest_price(ticker: str, db: Session) -> tuple[float | None, str | None]:
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

    positions = []
    total_portfolio_value = 0.0
    total_portfolio_cost  = 0.0
    total_realized_pnl    = 0.0

    for ticker, h in holdings.items():
        current_price, price_date = _latest_price(ticker, db)

        shares     = round(h["shares"], 6)
        cost_basis = round(h["cost_basis"], 2)
        avg_cost   = round(cost_basis / shares, 4) if shares > 0 else 0

        current_value  = round(shares * current_price, 2) if current_price and shares > 0 else None
        unrealized_pnl = round(current_value - cost_basis, 2) if current_value is not None else None
        unrealized_pct = (
            round((unrealized_pnl / cost_basis) * 100, 2)
            if unrealized_pnl is not None and cost_basis > 0 else None
        )
        total_pnl = round((unrealized_pnl or 0) + h["realized_pnl"], 2)

        if shares > 0:
            total_portfolio_value += current_value or 0
            total_portfolio_cost  += cost_basis

        total_realized_pnl += h["realized_pnl"]

        positions.append({
            "ticker":          ticker,
            "shares":          shares,
            "avg_cost":        avg_cost,
            "cost_basis":      cost_basis,
            "current_price":   current_price,
            "price_date":      price_date,
            "current_value":   current_value,
            "unrealized_pnl":  unrealized_pnl,
            "unrealized_pct":  unrealized_pct,
            "realized_pnl":    round(h["realized_pnl"], 2),
            "total_pnl":       total_pnl,
            "trade_count":     h["trade_count"],
            "first_buy_date":  h["first_buy_date"],
            "last_trade_date": h["last_trade_date"],
            "is_open":         shares > 0,
        })

    positions.sort(key=lambda p: (
        0 if p["is_open"] else 1,
        -(p["current_value"] or 0) if p["is_open"] else -p["realized_pnl"]
    ))

    total_unrealized     = round(total_portfolio_value - total_portfolio_cost, 2)
    total_unrealized_pct = (
        round((total_unrealized / total_portfolio_cost) * 100, 2)
        if total_portfolio_cost > 0 else 0
    )

    return {
        "positions": positions,
        "summary": {
            "total_portfolio_value": round(total_portfolio_value, 2),
            "total_cost_basis":      round(total_portfolio_cost, 2),
            "total_unrealized_pnl":  total_unrealized,
            "total_unrealized_pct":  total_unrealized_pct,
            "total_realized_pnl":    round(total_realized_pnl, 2),
            "total_pnl":             round(total_unrealized + total_realized_pnl, 2),
            "open_positions":        sum(1 for p in positions if p["is_open"]),
            "closed_positions":      sum(1 for p in positions if not p["is_open"]),
        },
    }