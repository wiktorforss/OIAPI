"""
Signals — cluster buy detection + conviction scoring.

Endpoints:
  GET /signals/cluster-buys   — tickers with multiple insiders buying in a window
  GET /signals/conviction      — ranked tickers by conviction score
  GET /signals/screener        — combined filterable view
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import date, timedelta
from typing import Optional

from ..database import get_db
from ..models import InsiderTrade, StockPrice
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/signals", tags=["Signals"])

# ── Helpers ───────────────────────────────────────────────────────────────────

PURCHASE_TYPES = ["P", "P - Purchase", "Purchase"]
SALE_TYPES     = ["S", "S - Sale", "Sale"]

# Role weights for conviction scoring
ROLE_WEIGHTS = {
    "ceo":       3.0,
    "cfo":       2.5,
    "president": 2.5,
    "coo":       2.0,
    "director":  1.5,
    "officer":   1.2,
    "vp":        1.2,
    "other":     1.0,
}

def _role_weight(title: str) -> float:
    if not title:
        return 1.0
    t = title.lower()
    for key, weight in ROLE_WEIGHTS.items():
        if key in t:
            return weight
    return 1.0


def _conviction_score(trades: list, total_buys: int, total_sells: int) -> float:
    """
    Score based on:
    - Number of distinct buyers (cluster signal)
    - Total value bought
    - Role weights of buyers
    - Recency of trades
    - Ratio of buys to sells
    """
    if not trades:
        return 0.0

    today = date.today()
    score = 0.0

    distinct_buyers = set()
    for t in trades:
        distinct_buyers.add(t.insider_name)
        days_ago = (today - t.trade_date).days if t.trade_date else 365
        recency_factor = max(0.1, 1.0 - (days_ago / 365))
        value = abs(t.value or 0)
        role_w = _role_weight(t.insider_title or "")
        score += (value / 100_000) * role_w * recency_factor

    # Bonus for cluster (multiple buyers)
    n_buyers = len(distinct_buyers)
    if n_buyers >= 3:
        score *= 1.5
    elif n_buyers == 2:
        score *= 1.2

    # Penalty when sells outnumber buys significantly
    if total_sells > 0 and total_buys > 0:
        sell_ratio = total_sells / (total_buys + total_sells)
        score *= max(0.3, 1.0 - sell_ratio)

    return round(score, 2)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/cluster-buys")
def cluster_buys(
    days: int = Query(30, ge=7, le=365, description="Look-back window in days"),
    min_insiders: int = Query(2, ge=2, le=20, description="Minimum distinct insiders buying"),
    min_value: Optional[float] = Query(None, description="Min total purchase value"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return tickers where at least `min_insiders` distinct insiders
    bought within the past `days` days.
    """
    since = date.today() - timedelta(days=days)

    rows = (
        db.query(
            InsiderTrade.ticker,
            InsiderTrade.company_name,
            func.count(func.distinct(InsiderTrade.insider_name)).label("distinct_buyers"),
            func.count(InsiderTrade.id).label("total_trades"),
            func.sum(InsiderTrade.value).label("total_value"),
            func.min(InsiderTrade.trade_date).label("first_buy"),
            func.max(InsiderTrade.trade_date).label("last_buy"),
        )
        .filter(InsiderTrade.transaction_type.in_(PURCHASE_TYPES))
        .filter(InsiderTrade.trade_date >= since)
        .group_by(InsiderTrade.ticker, InsiderTrade.company_name)
        .having(func.count(func.distinct(InsiderTrade.insider_name)) >= min_insiders)
        .order_by(desc("distinct_buyers"), desc("total_value"))
        .all()
    )

    results = []
    for r in rows:
        total_value = r.total_value or 0
        if min_value and total_value < min_value:
            continue

        # Get latest price
        price_row = (
            db.query(StockPrice)
            .filter(StockPrice.ticker == r.ticker)
            .order_by(desc(StockPrice.price_date))
            .first()
        )

        # Get the actual trades for insider details
        trades = (
            db.query(InsiderTrade)
            .filter(InsiderTrade.ticker == r.ticker)
            .filter(InsiderTrade.transaction_type.in_(PURCHASE_TYPES))
            .filter(InsiderTrade.trade_date >= since)
            .order_by(desc(InsiderTrade.value))
            .all()
        )

        insiders = [
            {
                "name":  t.insider_name,
                "title": t.insider_title,
                "value": t.value,
                "date":  t.trade_date.isoformat() if t.trade_date else None,
            }
            for t in trades
        ]

        results.append({
            "ticker":          r.ticker,
            "company_name":    r.company_name,
            "distinct_buyers": r.distinct_buyers,
            "total_trades":    r.total_trades,
            "total_value":     total_value,
            "first_buy":       r.first_buy.isoformat() if r.first_buy else None,
            "last_buy":        r.last_buy.isoformat() if r.last_buy else None,
            "price":           price_row.close if price_row else None,
            "insiders":        insiders,
        })

    return results


@router.get("/conviction")
def conviction_scores(
    days: int = Query(90, ge=7, le=365, description="Look-back window in days"),
    min_score: float = Query(0.0, ge=0),
    limit: int = Query(50, le=200),
    roles: Optional[str] = Query(None, description="Comma-separated role filter e.g. CEO,CFO"),
    officer_only: bool = Query(False),
    min_value: Optional[float] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return tickers ranked by conviction score.
    Score weighs: trade value, role seniority, recency, cluster effect, buy/sell ratio.
    """
    since = date.today() - timedelta(days=days)

    buy_q = (
        db.query(InsiderTrade)
        .filter(InsiderTrade.transaction_type.in_(PURCHASE_TYPES))
        .filter(InsiderTrade.trade_date >= since)
    )
    if officer_only:
        buy_q = buy_q.filter(InsiderTrade.is_officer == "1")
    if min_value:
        buy_q = buy_q.filter(InsiderTrade.value >= min_value)

    all_buys = buy_q.order_by(desc(InsiderTrade.trade_date)).all()

    # Group by ticker
    by_ticker: dict[str, list] = {}
    for t in all_buys:
        by_ticker.setdefault(t.ticker, []).append(t)

    # Filter by role if requested
    role_filter = [r.strip().lower() for r in roles.split(",")] if roles else None

    results = []
    for ticker, trades in by_ticker.items():
        if role_filter:
            trades = [t for t in trades if any(r in (t.insider_title or "").lower() for r in role_filter)]
        if not trades:
            continue

        company_name = trades[0].company_name

        total_buys  = db.query(func.count(InsiderTrade.id)).filter(
            InsiderTrade.ticker == ticker,
            InsiderTrade.transaction_type.in_(PURCHASE_TYPES)
        ).scalar() or 0
        total_sells = db.query(func.count(InsiderTrade.id)).filter(
            InsiderTrade.ticker == ticker,
            InsiderTrade.transaction_type.in_(SALE_TYPES)
        ).scalar() or 0

        score = _conviction_score(trades, total_buys, total_sells)
        if score < min_score:
            continue

        price_row = (
            db.query(StockPrice)
            .filter(StockPrice.ticker == ticker)
            .order_by(desc(StockPrice.price_date))
            .first()
        )

        distinct_buyers = len(set(t.insider_name for t in trades))
        total_value     = sum(t.value or 0 for t in trades)
        latest_trade    = max(trades, key=lambda t: t.trade_date or date.min)

        results.append({
            "ticker":           ticker,
            "company_name":     company_name,
            "conviction_score": score,
            "distinct_buyers":  distinct_buyers,
            "total_trades":     len(trades),
            "total_value":      total_value,
            "total_buys_ever":  total_buys,
            "total_sells_ever": total_sells,
            "latest_trade_date": latest_trade.trade_date.isoformat() if latest_trade.trade_date else None,
            "latest_insider":   latest_trade.insider_name,
            "latest_title":     latest_trade.insider_title,
            "price":            price_row.close if price_row else None,
        })

    results.sort(key=lambda x: x["conviction_score"], reverse=True)
    return results[:limit]


@router.get("/screener")
def screener(
    days: int = Query(90, ge=7, le=365),
    min_buyers: int = Query(1, ge=1),
    min_value: Optional[float] = Query(None),
    officer_only: bool = Query(False),
    purchases_only: bool = Query(True),
    sort_by: str = Query("conviction", regex="^(conviction|value|buyers|date)$"),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Flexible screener combining conviction scoring and cluster filtering.
    This is the main entrypoint for the signals page.
    """
    since = date.today() - timedelta(days=days)

    tx_types = PURCHASE_TYPES if purchases_only else (PURCHASE_TYPES + SALE_TYPES)

    q = (
        db.query(InsiderTrade)
        .filter(InsiderTrade.transaction_type.in_(tx_types))
        .filter(InsiderTrade.trade_date >= since)
    )
    if officer_only:
        q = q.filter(InsiderTrade.is_officer == "1")
    if min_value:
        q = q.filter(InsiderTrade.value >= min_value)

    all_trades = q.order_by(desc(InsiderTrade.trade_date)).all()

    by_ticker: dict[str, list] = {}
    for t in all_trades:
        by_ticker.setdefault(t.ticker, []).append(t)

    results = []
    for ticker, trades in by_ticker.items():
        buys  = [t for t in trades if t.transaction_type in PURCHASE_TYPES]
        distinct_buyers = len(set(t.insider_name for t in buys))
        if distinct_buyers < min_buyers:
            continue

        total_buys  = db.query(func.count(InsiderTrade.id)).filter(
            InsiderTrade.ticker == ticker,
            InsiderTrade.transaction_type.in_(PURCHASE_TYPES)
        ).scalar() or 0
        total_sells = db.query(func.count(InsiderTrade.id)).filter(
            InsiderTrade.ticker == ticker,
            InsiderTrade.transaction_type.in_(SALE_TYPES)
        ).scalar() or 0

        score        = _conviction_score(buys, total_buys, total_sells)
        total_value  = sum(t.value or 0 for t in buys)
        company_name = trades[0].company_name
        latest       = max(buys, key=lambda t: t.trade_date or date.min) if buys else trades[0]

        price_row = (
            db.query(StockPrice)
            .filter(StockPrice.ticker == ticker)
            .order_by(desc(StockPrice.price_date))
            .first()
        )

        results.append({
            "ticker":            ticker,
            "company_name":      company_name,
            "conviction_score":  score,
            "distinct_buyers":   distinct_buyers,
            "total_trades":      len(buys),
            "total_value":       total_value,
            "total_buys_ever":   total_buys,
            "total_sells_ever":  total_sells,
            "latest_trade_date": latest.trade_date.isoformat() if latest.trade_date else None,
            "latest_insider":    latest.insider_name,
            "latest_title":      latest.insider_title,
            "price":             price_row.close if price_row else None,
            "is_cluster":        distinct_buyers >= 2,
        })

    sort_key = {
        "conviction": lambda x: x["conviction_score"],
        "value":      lambda x: x["total_value"],
        "buyers":     lambda x: x["distinct_buyers"],
        "date":       lambda x: x["latest_trade_date"] or "",
    }[sort_by]

    results.sort(key=sort_key, reverse=True)
    return results[:limit]