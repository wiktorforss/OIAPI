from sqlalchemy import (
    BigInteger, UniqueConstraint,
    Column, Integer, String, Float, Date, DateTime,
    ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from .database import Base


class TradeType(str, enum.Enum):
    purchase = "P - Purchase"
    sale = "S - Sale"
    tax = "F - Tax"
    disposition = "D - Disposition"
    gift = "G - Gift"
    exercise = "X - Exercise"
    options_exercise = "M - Options Exercise"
    conversion = "C - Conversion"
    will = "W - Will/Inheritance"
    holdings = "H - Holdings"
    other = "O - Other"


class MyTradeType(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class InsiderTrade(Base):
    """
    Populated by the openinsiderData scraper.
    Mirrors the columns from openinsider.com SEC Form 4 data.
    """
    __tablename__ = "insider_trades"

    id = Column(Integer, primary_key=True, index=True)

    # Filing metadata
    filing_date = Column(Date, index=True)
    trade_date = Column(Date, index=True)

    # Company info
    ticker = Column(String(10), index=True, nullable=False)
    company_name = Column(String(255))

    # Insider info
    insider_name = Column(String(255))
    insider_title = Column(String(255))
    is_director = Column(String(1))
    is_officer = Column(String(1))
    is_ten_pct_owner = Column(String(1))
    is_other = Column(String(1))

    # Transaction details
    transaction_type = Column(String(50), index=True)
    price = Column(Float)
    qty = Column(Float)
    owned = Column(Float)
    delta_own = Column(String(20))
    value = Column(Float, index=True)

    scraped_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    my_trades = relationship("MyTrade", back_populates="related_insider_trade")


class MyTrade(Base):
    """
    Personal buy/sell log — scoped to the owning user.
    """
    __tablename__ = "my_trades"

    id = Column(Integer, primary_key=True, index=True)

    # ── Owner ──────────────────────────────────────────────────────────────────
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # ── Trade details ──────────────────────────────────────────────────────────
    ticker = Column(String(10), index=True, nullable=False)
    trade_type = Column(String(10), nullable=False)  # 'buy' or 'sell'
    trade_date = Column(Date, nullable=False, index=True)
    shares = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total_value = Column(Float)  # computed: shares * price

    notes = Column(Text)

    # Optionally link to an insider trade that inspired this trade
    related_insider_trade_id = Column(Integer, ForeignKey("insider_trades.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    owner = relationship("User", back_populates="trades")
    related_insider_trade = relationship("InsiderTrade", back_populates="my_trades")
    performance = relationship("Performance", back_populates="my_trade", uselist=False)


class Performance(Base):
    """
    Tracks how a stock performed after your trade.
    """
    __tablename__ = "performance"

    id = Column(Integer, primary_key=True, index=True)
    my_trade_id = Column(Integer, ForeignKey("my_trades.id"), unique=True, nullable=False)

    ticker = Column(String(10), index=True)
    price_at_trade = Column(Float)

    price_1w = Column(Float, nullable=True)
    price_2w = Column(Float, nullable=True)
    price_1m = Column(Float, nullable=True)
    price_3m = Column(Float, nullable=True)
    price_6m = Column(Float, nullable=True)
    price_1y = Column(Float, nullable=True)

    return_1w = Column(Float, nullable=True)
    return_2w = Column(Float, nullable=True)
    return_1m = Column(Float, nullable=True)
    return_3m = Column(Float, nullable=True)
    return_6m = Column(Float, nullable=True)
    return_1y = Column(Float, nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    my_trade = relationship("MyTrade", back_populates="performance")


class User(Base):
    """Login credentials."""
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, index=True)
    username        = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    trades = relationship("MyTrade", back_populates="owner")


class StockPrice(Base):
    """Cached daily close prices per ticker from Alpha Vantage."""
    __tablename__ = "stock_prices"

    id         = Column(Integer, primary_key=True, index=True)
    ticker     = Column(String(20), nullable=False, index=True)
    price_date = Column(Date, nullable=False)
    open       = Column(Float, nullable=True)
    high       = Column(Float, nullable=True)
    low        = Column(Float, nullable=True)
    close      = Column(Float, nullable=False)
    volume     = Column(BigInteger, nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "price_date", name="uq_stock_price"),
    )


class Watchlist(Base):
    """A named collection of tickers to watch."""
    __tablename__ = "watchlists"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items = relationship("WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base):
    """A single ticker within a watchlist."""
    __tablename__ = "watchlist_items"

    id           = Column(Integer, primary_key=True, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id"), nullable=False)
    ticker       = Column(String(20), nullable=False)
    notes        = Column(Text, nullable=True)
    added_at     = Column(DateTime(timezone=True), server_default=func.now())

    watchlist = relationship("Watchlist", back_populates="items")

    __table_args__ = (
        UniqueConstraint("watchlist_id", "ticker", name="uq_watchlist_ticker"),
    )