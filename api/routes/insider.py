from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import Optional, List
from datetime import date, datetime, timedelta
import subprocess
import csv
import os
import yaml

from ..database import get_db
from ..models import InsiderTrade
from ..schemas import InsiderTradeResponse, TickerSummary

router = APIRouter(prefix="/insider", tags=["Insider Trades"])

SCRAPER_DIR = os.getenv("SCRAPER_DIR", "/root/openinsiderData")
SCRAPER_CONFIG = os.path.join(SCRAPER_DIR, "config.yaml")
SCRAPER_CSV = os.path.join(SCRAPER_DIR, "data", "insider_trades.csv")
SCRAPER_VENV_PYTHON = os.path.join(SCRAPER_DIR, "venv", "bin", "python3")


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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _run_scraper_for_ticker(ticker: str, years: int = 5):
    """
    Temporarily updates config.yaml to scrape only the requested ticker
    going back `years` years, runs the scraper, then restores the config.
    """
    # Read current config
    with open(SCRAPER_CONFIG, "r") as f:
        config = yaml.safe_load(f)

    # Save original values to restore later
    original_include  = config["filters"].get("include_companies", [])
    original_year     = config["scraping"]["start_year"]
    original_month    = config["scraping"]["start_month"]

    # Calculate start year/month
    start_date = datetime.now() - timedelta(days=365 * years)

    # Patch config
    config["filters"]["include_companies"] = [ticker]
    config["scraping"]["start_year"]  = start_date.year
    config["scraping"]["start_month"] = start_date.month

    try:
        with open(SCRAPER_CONFIG, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        # Run the scraper
        result = subprocess.run(
            [SCRAPER_VENV_PYTHON, "openinsider_scraper.py"],
            cwd=SCRAPER_DIR,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode != 0:
            raise RuntimeError(f"Scraper failed: {result.stderr}")

    finally:
        # Always restore original config even if scraper fails
        config["filters"]["include_companies"] = original_include
        config["scraping"]["start_year"]  = original_year
        config["scraping"]["start_month"] = original_month
        with open(SCRAPER_CONFIG, "w") as f:
            yaml.dump(config, f, default_flow_style=False)


def _load_csv_for_ticker(ticker: str, db: Session) -> tuple[int, int]:
    """
    Reads the scraper CSV and upserts rows matching the ticker.
    Returns (inserted, skipped).
    """
    if not os.path.exists(SCRAPER_CSV):
        raise FileNotFoundError(f"CSV not found at {SCRAPER_CSV}")

    inserted = 0
    skipped  = 0

    with open(SCRAPER_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_ticker = row.get("ticker", "").strip().upper()
            if row_ticker != ticker:
                continue

            trade_date    = _parse_date(row.get("trade_date", ""))
            insider_name  = row.get("owner_name", "").strip()
            tx_type       = row.get("transaction_type", "").strip()

            if not trade_date:
                continue

            # Check for duplicate
            exists = db.query(InsiderTrade).filter(
                InsiderTrade.ticker           == ticker,
                InsiderTrade.trade_date       == trade_date,
                InsiderTrade.insider_name     == insider_name,
                InsiderTrade.transaction_type == tx_type,
            ).first()

            if exists:
                skipped += 1
                continue

            db.add(InsiderTrade(
                filing_date      = _parse_date(row.get("transaction_date", "")),
                trade_date       = trade_date,
                ticker           = ticker,
                company_name     = row.get("company_name", "").strip(),
                insider_name     = insider_name,
                insider_title    = row.get("Title", "").strip(),
                transaction_type = tx_type,
                price            = _clean_price(row.get("last_price", "")),
                qty              = _clean_qty(row.get("Qty", "")),
                owned            = _clean_qty(row.get("shares_held", "")),
                delta_own        = row.get("Owned", "").strip(),
                value            = _clean_value(row.get("Value", "")),
            ))
            inserted += 1

    db.commit()
    return inserted, skipped


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
    years: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db)
):
    """
    Runs the openinsiderData scraper filtered to a single ticker,
    then loads the results into the database. Skips duplicates.
    """
    ticker = ticker.upper().strip()

    if not os.path.exists(SCRAPER_CONFIG):
        raise HTTPException(status_code=500, detail=f"Scraper config not found at {SCRAPER_CONFIG}")

    try:
        _run_scraper_for_ticker(ticker, years)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Scraper timed out after 5 minutes")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraper error: {e}")

    try:
        inserted, skipped = _load_csv_for_ticker(ticker, db)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load CSV: {e}")

    return {
        "ticker":   ticker,
        "inserted": inserted,
        "skipped":  skipped,
        "message":  f"Fetched {ticker}: {inserted} new trades added, {skipped} duplicates skipped."
    }


@router.get("/ticker/{ticker}/summary", response_model=TickerSummary)
def get_ticker_summary(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    trades = db.query(InsiderTrade).filter(InsiderTrade.ticker == ticker).all()
    if not trades:
        raise HTTPException(status_code=404, detail=f"No insider trades found for {ticker}")

    purchases = [t for t in trades if "Purchase" in (t.transaction_type or "")]
    sales     = [t for t in trades if "Sale"     in (t.transaction_type or "")]

    from ..models import MyTrade
    my_trades  = db.query(MyTrade).filter(MyTrade.ticker == ticker).all()
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
