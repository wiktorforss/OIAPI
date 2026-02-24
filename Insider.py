from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import Optional, List
from datetime import date

from ..database import get_db
from ..models import InsiderTrade
from ..schemas import InsiderTradeResponse, TickerSummary

router = APIRouter(prefix="/insider", tags=["Insider Trades"])


@router.get("/", response_model=List[InsiderTradeResponse])
def get_insider_trades(
    ticker: Optional[str] = Query(None, description="Filter by ticker symbol"),
    insider_name: Optional[str] = Query(None, description="Filter by insider name"),
    transaction_type: Optional[str] = Query(None, description="Filter by transaction type (e.g. 'P - Purchase')"),
    date_from: Optional[date] = Query(None, description="Filter trades from this date"),
    date_to: Optional[date] = Query(None, description="Filter trades to this date"),
    min_value: Optional[float] = Query(None, description="Minimum trade value in USD"),
    max_value: Optional[float] = Query(None, description="Maximum trade value in USD"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Query insider trades with flexible filters.
    Defaults to returning the 50 most recent trades.
    """
    q = db.query(InsiderTrade)

    if ticker:
        q = q.filter(InsiderTrade.ticker == ticker.upper())
    if insider_name:
        q = q.filter(InsiderTrade.insider_name.ilike(f"%{insider_name}%"))
    if transaction_type:
        q = q.filter(InsiderTrade.transaction_type == transaction_type)
    if date_from:
        q = q.filter(InsiderTrade.trade_date >= date_from)
    if date_to:
        q = q.filter(InsiderTrade.trade_date <= date_to)
    if min_value is not None:
        q = q.filter(InsiderTrade.value >= min_value)
    if max_value is not None:
        q = q.filter(InsiderTrade.value <= max_value)

    return q.order_by(desc(InsiderTrade.trade_date)).offset(offset).limit(limit).all()


@router.get("/count")
def count_insider_trades(
    ticker: Optional[str] = None,
    transaction_type: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db)
):
    """Total count of insider trades (for pagination)."""
    q = db.query(func.count(InsiderTrade.id))
    if ticker:
        q = q.filter(InsiderTrade.ticker == ticker.upper())
    if transaction_type:
        q = q.filter(InsiderTrade.transaction_type == transaction_type)
    if date_from:
        q = q.filter(InsiderTrade.trade_date >= date_from)
    if date_to:
        q = q.filter(InsiderTrade.trade_date <= date_to)
    return {"count": q.scalar()}


@router.get("/tickers", response_model=List[str])
def get_tracked_tickers(db: Session = Depends(get_db)):
    """All unique tickers with insider trade activity."""
    results = db.query(InsiderTrade.ticker).distinct().order_by(InsiderTrade.ticker).all()
    return [r[0] for r in results]


@router.get("/ticker/{ticker}/summary", response_model=TickerSummary)
def get_ticker_summary(ticker: str, db: Session = Depends(get_db)):
    """Aggregate insider activity summary for a specific ticker."""
    ticker = ticker.upper()
    trades = db.query(InsiderTrade).filter(InsiderTrade.ticker == ticker).all()

    if not trades:
        raise HTTPException(status_code=404, detail=f"No insider trades found for {ticker}")

    purchases = [t for t in trades if "Purchase" in (t.transaction_type or "")]
    sales = [t for t in trades if "Sale" in (t.transaction_type or "")]

    from ..models import MyTrade, Performance
    from sqlalchemy import and_

    my_trades = db.query(MyTrade).filter(MyTrade.ticker == ticker).all()

    returns_1m = []
    returns_3m = []
    for mt in my_trades:
        if mt.performance:
            if mt.performance.return_1m is not None:
                returns_1m.append(mt.performance.return_1m)
            if mt.performance.return_3m is not None:
                returns_3m.append(mt.performance.return_3m)

    return TickerSummary(
        ticker=ticker,
        total_insider_purchases=len(purchases),
        total_insider_sales=len(sales),
        total_insider_purchase_value=sum(t.value or 0 for t in purchases),
        total_insider_sale_value=sum(t.value or 0 for t in sales),
        my_trade_count=len(my_trades),
        avg_return_1m=sum(returns_1m) / len(returns_1m) if returns_1m else None,
        avg_return_3m=sum(returns_3m) / len(returns_3m) if returns_3m else None,
    )


@router.get("/{trade_id}", response_model=InsiderTradeResponse)
def get_insider_trade(trade_id: int, db: Session = Depends(get_db)):
    """Get a single insider trade by ID."""
    trade = db.query(InsiderTrade).filter(InsiderTrade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade