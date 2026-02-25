from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List, Optional

from ..database import get_db
from ..models import MyTrade, Performance
from ..schemas import PerformanceUpdate, PerformanceResponse, DashboardStats

router = APIRouter(prefix="/performance", tags=["Performance"])


def _calc_return(entry: float, current: float) -> Optional[float]:
    if entry and entry > 0 and current:
        return round(((current - entry) / entry) * 100, 2)
    return None


@router.get("/", response_model=List[PerformanceResponse])
def get_all_performance(
    ticker: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db)
):
    """Get performance records for all your trades."""
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
    """
    Update price snapshots for a trade and auto-compute returns.
    Call this manually or from a scheduled price-fetching job.
    """
    perf = db.query(Performance).filter(Performance.my_trade_id == my_trade_id).first()
    if not perf:
        raise HTTPException(status_code=404, detail="Performance record not found")

    entry = perf.price_at_trade

    if updates.price_1w is not None:
        perf.price_1w = updates.price_1w
        perf.return_1w = _calc_return(entry, updates.price_1w)
    if updates.price_2w is not None:
        perf.price_2w = updates.price_2w
        perf.return_2w = _calc_return(entry, updates.price_2w)
    if updates.price_1m is not None:
        perf.price_1m = updates.price_1m
        perf.return_1m = _calc_return(entry, updates.price_1m)
    if updates.price_3m is not None:
        perf.price_3m = updates.price_3m
        perf.return_3m = _calc_return(entry, updates.price_3m)
    if updates.price_6m is not None:
        perf.price_6m = updates.price_6m
        perf.return_6m = _calc_return(entry, updates.price_6m)
    if updates.price_1y is not None:
        perf.price_1y = updates.price_1y
        perf.return_1y = _calc_return(entry, updates.price_1y)

    db.commit()
    db.refresh(perf)
    return perf


@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db)):
    """High-level stats for your dashboard homepage."""
    from ..models import InsiderTrade

    total_insider = db.query(func.count(InsiderTrade.id)).scalar()
    total_my = db.query(func.count(MyTrade.id)).scalar()
    tickers = db.query(MyTrade.ticker).distinct().count()

    # Best performing trade by 1m return
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

    # Average 1m return across all trades
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
    """Get the performance record for a specific personal trade."""
    perf = db.query(Performance).filter(Performance.my_trade_id == my_trade_id).first()
    if not perf:
        raise HTTPException(status_code=404, detail="Performance record not found")
    return perf
