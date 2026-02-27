from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List, Optional
from datetime import datetime, timedelta, timezone, date

from ..database import get_db
from ..models import MyTrade, Performance, StockPrice
from ..schemas import PerformanceUpdate, PerformanceResponse, DashboardStats
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/performance", tags=["Performance"])


def _calc_return(entry: float, current: float) -> Optional[float]:
    if entry and entry > 0 and current:
        return round(((current - entry) / entry) * 100, 2)
    return None


def _get_price_on_date(ticker: str, target_date: date, db: Session) -> float | None:
    """
    Look up closing price for a ticker on or near a target date
    using cached Polygon data in stock_prices table.
    """
    # Try exact date first, then search ±5 trading days
    for delta in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
        check_date = target_date + timedelta(days=delta)
        row = db.query(StockPrice).filter(
            StockPrice.ticker     == ticker.upper(),
            StockPrice.price_date == check_date,
        ).first()
        if row:
            return row.close
    return None


def _auto_update_performance(perf: Performance, trade: MyTrade, db: Session) -> int:
    """
    Fill in any missing price snapshots for a performance record
    using cached Polygon data. Returns number of snapshots updated.
    """
    if not trade.trade_date or not perf.price_at_trade:
        return 0

    entry_date  = trade.trade_date
    entry_price = perf.price_at_trade
    updated     = 0

    snapshots = [
        ("price_1w",  "return_1w",  7),
        ("price_2w",  "return_2w",  14),
        ("price_1m",  "return_1m",  30),
        ("price_3m",  "return_3m",  90),
        ("price_6m",  "return_6m",  180),
        ("price_1y",  "return_1y",  365),
    ]

    today = datetime.now(timezone.utc).date()

    for price_field, return_field, days in snapshots:
        target_date = entry_date + timedelta(days=days)

        # Skip future dates
        if target_date > today:
            continue

        # Skip if already filled
        if getattr(perf, price_field) is not None:
            continue

        price = _get_price_on_date(trade.ticker, target_date, db)
        if price is not None:
            setattr(perf, price_field,  price)
            setattr(perf, return_field, _calc_return(entry_price, price))
            updated += 1

    return updated


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/update-all")
def update_all_performance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Auto-fill performance snapshots for all trades using cached Polygon prices.
    Safe to run repeatedly — only fills missing slots, never overwrites existing data.
    """
    trades = db.query(MyTrade).all()
    total_updated  = 0
    trades_touched = 0

    for trade in trades:
        perf = db.query(Performance).filter(
            Performance.my_trade_id == trade.id
        ).first()
        if not perf:
            continue

        n = _auto_update_performance(perf, trade, db)
        if n > 0:
            trades_touched += 1
            total_updated  += n

    db.commit()
    return {
        "trades_checked": len(trades),
        "trades_updated": trades_touched,
        "snapshots_filled": total_updated,
        "message": f"Updated {total_updated} price snapshots across {trades_touched} trades",
    }


@router.get("/", response_model=List[PerformanceResponse])
def get_all_performance(
    ticker: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db)
):
    q = db.query(Performance)
    if ticker:
        q = q.filter(Performance.ticker == ticker.upper())
    return q.order_by(desc(Performance.updated_at)).offset(offset).limit(limit).all()


@router.patch("/{my_trade_id}", response_model=PerformanceResponse)
def update_performance(
    my_trade_id: int,
    updates: PerformanceUpdate,
    db: Session = Depends(get_db)
):
    perf = db.query(Performance).filter(Performance.my_trade_id == my_trade_id).first()
    if not perf:
        raise HTTPException(status_code=404, detail="Performance record not found")

    entry = perf.price_at_trade

    if updates.price_1w  is not None: perf.price_1w  = updates.price_1w;  perf.return_1w  = _calc_return(entry, updates.price_1w)
    if updates.price_2w  is not None: perf.price_2w  = updates.price_2w;  perf.return_2w  = _calc_return(entry, updates.price_2w)
    if updates.price_1m  is not None: perf.price_1m  = updates.price_1m;  perf.return_1m  = _calc_return(entry, updates.price_1m)
    if updates.price_3m  is not None: perf.price_3m  = updates.price_3m;  perf.return_3m  = _calc_return(entry, updates.price_3m)
    if updates.price_6m  is not None: perf.price_6m  = updates.price_6m;  perf.return_6m  = _calc_return(entry, updates.price_6m)
    if updates.price_1y  is not None: perf.price_1y  = updates.price_1y;  perf.return_1y  = _calc_return(entry, updates.price_1y)

    db.commit()
    db.refresh(perf)
    return perf


@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db)):
    from ..models import InsiderTrade

    total_insider = db.query(func.count(InsiderTrade.id)).scalar()
    total_my      = db.query(func.count(MyTrade.id)).scalar()
    tickers       = db.query(MyTrade.ticker).distinct().count()

    best_perf = (
        db.query(Performance, MyTrade)
        .join(MyTrade, Performance.my_trade_id == MyTrade.id)
        .filter(Performance.return_1m.isnot(None))
        .order_by(desc(Performance.return_1m))
        .first()
    )

    best_ticker = None
    if best_perf:
        perf_record, trade_record = best_perf
        best_ticker = f"{trade_record.ticker} (+{perf_record.return_1m}% 1m)"

    avg_1m = db.query(func.avg(Performance.return_1m)).scalar()

    return DashboardStats(
        total_insider_trades=total_insider or 0,
        total_my_trades=total_my or 0,
        tickers_tracked=tickers or 0,
        best_performing_trade=best_ticker,
        avg_return_1m_all=round(avg_1m, 2) if avg_1m else None,
    )


@router.get("/{my_trade_id}", response_model=PerformanceResponse)
def get_performance(my_trade_id: int, db: Session = Depends(get_db)):
    perf = db.query(Performance).filter(Performance.my_trade_id == my_trade_id).first()
    if not perf:
        raise HTTPException(status_code=404, detail="Performance record not found")
    return perf
