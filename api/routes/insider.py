from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import Optional, List
from datetime import date, datetime, timedelta
import requests
from bs4 import BeautifulSoup

from ..database import get_db
from ..models import InsiderTrade
from ..schemas import InsiderTradeResponse, TickerSummary

router = APIRouter(prefix="/insider", tags=["Insider Trades"])

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_price(val: str) -> float | None:
    if not val:
        return None
    try:
        return float(val.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _clean_qty(val: str) -> float | None:
    if not val:
        return None
    try:
        return float(val.replace("+", "").replace(",", "").strip())
    except ValueError:
        return None


def _clean_value(val: str) -> float | None:
    if not val:
        return None
    try:
        return float(val.replace("+", "").replace("-", "").replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _parse_date(val: str) -> date | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _scrape_ticker(ticker: str, years: int = 5) -> list[dict]:
    """Scrape openinsider.com for a ticker going back `years` years."""
    ticker = ticker.upper()
    date_from = (datetime.now() - timedelta(days=365 * years)).strftime("%m/%d/%Y")
    date_to   = datetime.now().strftime("%m/%d/%Y")

    url = (
        f"https://openinsider.com/screener"
        f"?s={ticker}"
        f"&fd=-1&fdr={date_from}+-+{date_to}"
        f"&cnt=500&action=6"
    )

    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"openinsider.com returned {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"class": "tinytable"})
    if not table:
        return []

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(dict(zip(headers, cells)))

    return rows


def _rows_to_trades(rows: list[dict], ticker: str) -> list[dict]:
    """Map scraped table rows to insider_trades column dicts."""
    trades = []
    for row in rows:
        trades.append({
            "filing_date":      _parse_date(row.get("Filing Date") or row.get("FilingDate") or ""),
            "trade_date":       _parse_date(row.get("Trade Date") or row.get("TradeDate") or ""),
            "ticker":           ticker,
            "company_name":     row.get("Issuer", "").strip(),
            "insider_name":     row.get("Insider Name", row.get("InsiderName", "")).strip(),
            "insider_title":    row.get("Title", "").strip(),
            "transaction_type": row.get("Trade Type", row.get("TradeType", "")).strip(),
            "price":            _clean_price(row.get("Price", "")),
            "qty":              _clean_qty(row.get("Qty", "")),
            "owned":            _clean_qty(row.get("Owned", "")),
            "delta_own":        row.get("\u0394Own", row.get("DeltaOwn", "")).strip(),
            "value":            _clean_value(row.get("Value", "")),
        })
    return [t for t in trades if t["trade_date"] is not None]


def _upsert_trades(trades: list[dict], db: Session) -> int:
    """Insert trades, skipping duplicates. Returns count of new rows."""
    inserted = 0
    for t in trades:
        exists = db.query(InsiderTrade).filter(
            InsiderTrade.ticker           == t["ticker"],
            InsiderTrade.trade_date       == t["trade_date"],
            InsiderTrade.insider_name     == t["insider_name"],
            InsiderTrade.transaction_type == t["transaction_type"],
        ).first()
        if not exists:
            db.add(InsiderTrade(**t))
            inserted += 1
    db.commit()
    return inserted


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[InsiderTradeResponse])
def get_insider_trades(
    ticker: Optional[str] = Query(None),
    insider_name: Optional[str] = Query(None),
    transaction_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    min_value: Optional[float] = Query(None),
    max_value: Optional[float] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Query insider trades with flexible filters."""
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
    results = db.query(InsiderTrade.ticker).distinct().order_by(InsiderTrade.ticker).all()
    return [r[0] for r in results]


@router.post("/fetch/{ticker}")
def fetch_ticker(
    ticker: str,
    years: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db)
):
    """
    Scrape openinsider.com for a specific ticker and load into the database.
    Skips duplicates automatically.
    """
    ticker = ticker.upper()
    try:
        rows = _scrape_ticker(ticker, years)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to scrape openinsider.com: {e}")

    if not rows:
        return {
            "ticker": ticker,
            "scraped": 0,
            "inserted": 0,
            "message": f"No insider trades found for {ticker} on openinsider.com"
        }

    trades = _rows_to_trades(rows, ticker)
    inserted = _upsert_trades(trades, db)

    return {
        "ticker": ticker,
        "scraped": len(trades),
        "inserted": inserted,
        "skipped": len(trades) - inserted,
        "message": f"Fetched {ticker}: {inserted} new trades added, {len(trades) - inserted} duplicates skipped."
    }


@router.get("/ticker/{ticker}/summary", response_model=TickerSummary)
def get_ticker_summary(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    trades = db.query(InsiderTrade).filter(InsiderTrade.ticker == ticker).all()
    if not trades:
        raise HTTPException(status_code=404, detail=f"No insider trades found for {ticker}")

    purchases = [t for t in trades if "Purchase" in (t.transaction_type or "")]
    sales = [t for t in trades if "Sale" in (t.transaction_type or "")]

    from ..models import MyTrade
    my_trades = db.query(MyTrade).filter(MyTrade.ticker == ticker).all()

    returns_1m = [mt.performance.return_1m for mt in my_trades if mt.performance and mt.performance.return_1m is not None]
    returns_3m = [mt.performance.return_3m for mt in my_trades if mt.performance and mt.performance.return_3m is not None]

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
    trade = db.query(InsiderTrade).filter(InsiderTrade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade
