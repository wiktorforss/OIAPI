from sqlalchemy import (
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
    is_director = Column(String(1))   # 1 or blank
    is_officer = Column(String(1))
    is_ten_pct_owner = Column(String(1))
    is_other = Column(String(1))

    # Transaction details
    transaction_type = Column(String(50), index=True)
    price = Column(Float)
    qty = Column(Float)           # shares traded
    owned = Column(Float)         # shares owned after trade
    delta_own = Column(String(20)) # % change in ownership
    value = Column(Float, index=True)  # USD value of trade

    scraped_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    my_trades = relationship("MyTrade", back_populates="related_insider_trade")


class MyTrade(Base):
    """
    Your personal buy/sell log.
    """
    __tablename__ = "my_trades"

    id = Column(Integer, primary_key=True, index=True)

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
    related_insider_trade = relationship("InsiderTrade", back_populates="my_trades")
    performance = relationship("Performance", back_populates="my_trade", uselist=False)


class Performance(Base):
    """
    Tracks how a stock performed after your trade.
    Price snapshots are updated by a scheduled job (or manually).
    """
    __tablename__ = "performance"

    id = Column(Integer, primary_key=True, index=True)
    my_trade_id = Column(Integer, ForeignKey("my_trades.id"), unique=True, nullable=False)

    ticker = Column(String(10), index=True)
    price_at_trade = Column(Float)   # price when you made your trade

    # Price snapshots
    price_1w = Column(Float, nullable=True)
    price_2w = Column(Float, nullable=True)
    price_1m = Column(Float, nullable=True)
    price_3m = Column(Float, nullable=True)
    price_6m = Column(Float, nullable=True)
    price_1y = Column(Float, nullable=True)

    # Computed returns (%) — populated alongside price snapshots
    return_1w = Column(Float, nullable=True)
    return_2w = Column(Float, nullable=True)
    return_1m = Column(Float, nullable=True)
    return_3m = Column(Float, nullable=True)
    return_6m = Column(Float, nullable=True)
    return_1y = Column(Float, nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    my_trade = relationship("MyTrade", back_populates="performance")
