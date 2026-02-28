from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import date

from ..database import get_db
from ..models import MyTrade, Performance, InsiderTrade, User
from ..routes.auth import get_current_user
from ..schemas import MyTradeCreate, MyTradeUpdate, MyTradeResponse

router = APIRouter(prefix="/my-trades", tags=["My Trades"])


def _compute_total(shares: float, price: float) -> float:
    return round(shares * price, 2)


def _compute_return(price_at_trade: float, current_price: float) -> Optional[float]:
    if price_at_trade and price_at_trade > 0:
        return round(((current_price - price_at_trade) / price_at_trade) * 100, 2)
    return None


@router.get("/", response_model=List[MyTradeResponse])
def get_my_trades(
    ticker: Optional[str] = Query(None),
    trade_type: Optional[str] = Query(None, pattern="^(buy|sell)$"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get your personal trade log with optional filters."""
    q = db.query(MyTrade).filter(MyTrade.user_id == current_user.id)
    if ticker:
        q = q.filter(MyTrade.ticker == ticker.upper())
    if trade_type:
        q = q.filter(MyTrade.trade_type == trade_type)
    if date_from:
        q = q.filter(MyTrade.trade_date >= date_from)
    if date_to:
        q = q.filter(MyTrade.trade_date <= date_to)
    return q.order_by(desc(MyTrade.trade_date)).offset(offset).limit(limit).all()


@router.post("/", response_model=MyTradeResponse, status_code=201)
def create_my_trade(
    trade: MyTradeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Log a new personal trade."""
    if trade.related_insider_trade_id:
        insider = db.query(InsiderTrade).filter(
            InsiderTrade.id == trade.related_insider_trade_id
        ).first()
        if not insider:
            raise HTTPException(
                status_code=404,
                detail=f"Insider trade {trade.related_insider_trade_id} not found"
            )

    db_trade = MyTrade(
        user_id=current_user.id,
        ticker=trade.ticker.upper(),
        trade_type=trade.trade_type,
        trade_date=trade.trade_date,
        shares=trade.shares,
        price=trade.price,
        total_value=_compute_total(trade.shares, trade.price),
        notes=trade.notes,
        related_insider_trade_id=trade.related_insider_trade_id,
    )
    db.add(db_trade)
    db.flush()

    perf = Performance(
        my_trade_id=db_trade.id,
        ticker=db_trade.ticker,
        price_at_trade=trade.price,
    )
    db.add(perf)
    db.commit()
    db.refresh(db_trade)
    return db_trade


@router.get("/{trade_id}", response_model=MyTradeResponse)
def get_my_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single personal trade by ID."""
    trade = db.query(MyTrade).filter(
        MyTrade.id == trade_id,
        MyTrade.user_id == current_user.id,
    ).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.patch("/{trade_id}", response_model=MyTradeResponse)
def update_my_trade(
    trade_id: int,
    updates: MyTradeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update notes or correct a trade entry."""
    trade = db.query(MyTrade).filter(
        MyTrade.id == trade_id,
        MyTrade.user_id == current_user.id,
    ).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if updates.notes is not None:
        trade.notes = updates.notes
    if updates.shares is not None:
        trade.shares = updates.shares
        trade.total_value = _compute_total(updates.shares, trade.price)
    if updates.price is not None:
        trade.price = updates.price
        trade.total_value = _compute_total(trade.shares, updates.price)
        if trade.performance:
            trade.performance.price_at_trade = updates.price

    db.commit()
    db.refresh(trade)
    return trade


@router.delete("/{trade_id}", status_code=204)
def delete_my_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a personal trade record."""
    trade = db.query(MyTrade).filter(
        MyTrade.id == trade_id,
        MyTrade.user_id == current_user.id,
    ).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    db.delete(trade)
    db.commit()