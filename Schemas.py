from pydantic import BaseModel, Field, computed_field
from typing import Optional, List
from datetime import date, datetime


# ─── Insider Trades ───────────────────────────────────────────────────────────

class InsiderTradeBase(BaseModel):
    filing_date: Optional[date]
    trade_date: Optional[date]
    ticker: str
    company_name: Optional[str]
    insider_name: Optional[str]
    insider_title: Optional[str]
    is_director: Optional[str]
    is_officer: Optional[str]
    is_ten_pct_owner: Optional[str]
    transaction_type: Optional[str]
    price: Optional[float]
    qty: Optional[float]
    owned: Optional[float]
    delta_own: Optional[str]
    value: Optional[float]


class InsiderTradeResponse(InsiderTradeBase):
    id: int
    scraped_at: Optional[datetime]

    class Config:
        from_attributes = True


# ─── My Trades ────────────────────────────────────────────────────────────────

class MyTradeCreate(BaseModel):
    ticker: str = Field(..., example="AAPL")
    trade_type: str = Field(..., example="buy", pattern="^(buy|sell)$")
    trade_date: date
    shares: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    notes: Optional[str] = None
    related_insider_trade_id: Optional[int] = None


class MyTradeUpdate(BaseModel):
    notes: Optional[str] = None
    shares: Optional[float] = Field(None, gt=0)
    price: Optional[float] = Field(None, gt=0)


class MyTradeResponse(BaseModel):
    id: int
    ticker: str
    trade_type: str
    trade_date: date
    shares: float
    price: float
    total_value: Optional[float]
    notes: Optional[str]
    related_insider_trade_id: Optional[int]
    created_at: Optional[datetime]
    performance: Optional["PerformanceResponse"] = None

    class Config:
        from_attributes = True


# ─── Performance ──────────────────────────────────────────────────────────────

class PerformanceUpdate(BaseModel):
    price_1w: Optional[float] = None
    price_2w: Optional[float] = None
    price_1m: Optional[float] = None
    price_3m: Optional[float] = None
    price_6m: Optional[float] = None
    price_1y: Optional[float] = None


class PerformanceResponse(BaseModel):
    id: int
    ticker: str
    price_at_trade: Optional[float]
    price_1w: Optional[float]
    price_2w: Optional[float]
    price_1m: Optional[float]
    price_3m: Optional[float]
    price_6m: Optional[float]
    price_1y: Optional[float]
    return_1w: Optional[float]
    return_2w: Optional[float]
    return_1m: Optional[float]
    return_3m: Optional[float]
    return_6m: Optional[float]
    return_1y: Optional[float]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


MyTradeResponse.model_rebuild()


# ─── Summary / Analytics ──────────────────────────────────────────────────────

class TickerSummary(BaseModel):
    ticker: str
    total_insider_purchases: int
    total_insider_sales: int
    total_insider_purchase_value: float
    total_insider_sale_value: float
    my_trade_count: int
    avg_return_1m: Optional[float]
    avg_return_3m: Optional[float]


class DashboardStats(BaseModel):
    total_insider_trades: int
    total_my_trades: int
    tickers_tracked: int
    best_performing_trade: Optional[str]
    avg_return_1m_all: Optional[float]