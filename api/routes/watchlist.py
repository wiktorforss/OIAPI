from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Optional

from ..database import get_db
from ..models import Watchlist, WatchlistItem, InsiderTrade, StockPrice
from ..routes.auth import get_current_user
from ..models import User

router = APIRouter(prefix="/watchlists", tags=["Watchlists"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    name: str

class WatchlistRename(BaseModel):
    name: str

class ItemAdd(BaseModel):
    ticker: str
    notes: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enrich_item(item: WatchlistItem, db: Session) -> dict:
    ticker = item.ticker.upper()

    # Latest cached price
    price_row = (
        db.query(StockPrice)
        .filter(StockPrice.ticker == ticker)
        .order_by(desc(StockPrice.price_date))
        .first()
    )

    # Recent insider activity (last 90 days)
    recent_buys = (
        db.query(InsiderTrade)
        .filter(InsiderTrade.ticker == ticker)
        .filter(InsiderTrade.transaction_type.in_(["P", "P - Purchase", "Purchase"]))
        .order_by(desc(InsiderTrade.trade_date))
        .limit(3)
        .all()
    )
    recent_sells = (
        db.query(InsiderTrade)
        .filter(InsiderTrade.ticker == ticker)
        .filter(InsiderTrade.transaction_type.in_(["S", "S - Sale", "Sale"]))
        .order_by(desc(InsiderTrade.trade_date))
        .limit(3)
        .all()
    )

    total_buys  = db.query(InsiderTrade).filter(
        InsiderTrade.ticker == ticker,
        InsiderTrade.transaction_type.in_(["P", "P - Purchase", "Purchase"])
    ).count()
    total_sells = db.query(InsiderTrade).filter(
        InsiderTrade.ticker == ticker,
        InsiderTrade.transaction_type.in_(["S", "S - Sale", "Sale"])
    ).count()

    latest_buy  = recent_buys[0]  if recent_buys  else None
    latest_sell = recent_sells[0] if recent_sells else None

    return {
        "id":           item.id,
        "ticker":       ticker,
        "notes":        item.notes,
        "added_at":     item.added_at.isoformat() if item.added_at else None,
        "price":        price_row.close            if price_row else None,
        "price_date":   price_row.price_date.isoformat() if price_row else None,
        "total_insider_buys":  total_buys,
        "total_insider_sells": total_sells,
        "latest_buy_date":  latest_buy.trade_date.isoformat()  if latest_buy  and latest_buy.trade_date  else None,
        "latest_buy_value": latest_buy.value                   if latest_buy  else None,
        "latest_sell_date": latest_sell.trade_date.isoformat() if latest_sell and latest_sell.trade_date else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
def list_watchlists(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all watchlists with item count."""
    wls = db.query(Watchlist).order_by(Watchlist.created_at).all()
    return [
        {
            "id":         w.id,
            "name":       w.name,
            "item_count": len(w.items),
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in wls
    ]


@router.post("/", status_code=201)
def create_watchlist(
    body: WatchlistCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wl = Watchlist(name=body.name.strip())
    db.add(wl)
    db.commit()
    db.refresh(wl)
    return {"id": wl.id, "name": wl.name, "item_count": 0}


@router.patch("/{watchlist_id}")
def rename_watchlist(
    watchlist_id: int,
    body: WatchlistRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wl = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    wl.name = body.name.strip()
    db.commit()
    return {"id": wl.id, "name": wl.name}


@router.delete("/{watchlist_id}", status_code=204)
def delete_watchlist(
    watchlist_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wl = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    db.delete(wl)
    db.commit()


@router.get("/{watchlist_id}")
def get_watchlist(
    watchlist_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single watchlist with all enriched items."""
    wl = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {
        "id":         wl.id,
        "name":       wl.name,
        "created_at": wl.created_at.isoformat() if wl.created_at else None,
        "items":      [_enrich_item(item, db) for item in wl.items],
    }


@router.post("/{watchlist_id}/items", status_code=201)
def add_item(
    watchlist_id: int,
    body: ItemAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wl = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    ticker = body.ticker.upper().strip()

    existing = db.query(WatchlistItem).filter(
        WatchlistItem.watchlist_id == watchlist_id,
        WatchlistItem.ticker       == ticker,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"{ticker} is already in this watchlist")

    item = WatchlistItem(watchlist_id=watchlist_id, ticker=ticker, notes=body.notes)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _enrich_item(item, db)


@router.delete("/{watchlist_id}/items/{item_id}", status_code=204)
def remove_item(
    watchlist_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.query(WatchlistItem).filter(
        WatchlistItem.id           == item_id,
        WatchlistItem.watchlist_id == watchlist_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()


@router.patch("/{watchlist_id}/items/{item_id}")
def update_item_notes(
    watchlist_id: int,
    item_id: int,
    body: ItemAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.query(WatchlistItem).filter(
        WatchlistItem.id           == item_id,
        WatchlistItem.watchlist_id == watchlist_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.notes = body.notes
    db.commit()
    return _enrich_item(item, db)
