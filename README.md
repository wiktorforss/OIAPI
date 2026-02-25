# Insider Trading Tracker — API

FastAPI backend that serves insider trade data (scraped via openinsiderData) alongside
your personal trade log and performance tracking.

---

## Project Structure

```
insider-api/
├── api/
│   ├── main.py          # App entry point, CORS, router registration
│   ├── database.py      # SQLAlchemy engine + session
│   ├── models.py        # DB table definitions
│   ├── schemas.py       # Pydantic request/response models
│   └── routes/
│       ├── insider.py   # GET /insider/* — query scraped insider trades
│       ├── my_trades.py # CRUD /my-trades/* — your personal trade log
│       └── performance.py # GET/PATCH /performance/* — return tracking
├── requirements.txt
├── alembic.ini
└── .env.example
```

---

## Quick Start

### 1. PostgreSQL setup (on your VPS)

```bash
sudo -u postgres psql
CREATE DATABASE insider_db;
CREATE USER insider_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE insider_db TO insider_user;
\q
```

### 2. Clone and install

```bash
git clone <your-repo>
cd insider-api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual DB credentials and frontend domain
```

### 4. Run the API

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Tables are auto-created on first startup. Visit `http://localhost:8000/docs` for the
interactive Swagger UI.

---

## API Endpoints

### Insider Trades (read-only, populated by scraper)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/insider/` | List insider trades (filterable) |
| GET | `/insider/count` | Count with same filters |
| GET | `/insider/tickers` | All tracked tickers |
| GET | `/insider/ticker/{ticker}/summary` | Aggregate stats per ticker |
| GET | `/insider/{id}` | Single trade by ID |

**Filter params:** `ticker`, `insider_name`, `transaction_type`, `date_from`, `date_to`, `min_value`, `max_value`, `limit`, `offset`

---

### My Trades (your personal log)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/my-trades/` | List your trades |
| POST | `/my-trades/` | Log a new trade |
| GET | `/my-trades/{id}` | Get single trade |
| PATCH | `/my-trades/{id}` | Edit notes/price/shares |
| DELETE | `/my-trades/{id}` | Delete a trade |

**POST body example:**
```json
{
  "ticker": "AAPL",
  "trade_type": "buy",
  "trade_date": "2024-06-01",
  "shares": 10,
  "price": 189.50,
  "notes": "Following Tim Cook purchase of $2M",
  "related_insider_trade_id": 42
}
```

---

### Performance Tracking

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/performance/dashboard` | High-level stats for homepage |
| GET | `/performance/` | All performance records |
| GET | `/performance/{my_trade_id}` | Single trade performance |
| PATCH | `/performance/{my_trade_id}` | Update price snapshots |

**PATCH body example (call from a price-update cron job):**
```json
{
  "price_1w": 192.10,
  "price_1m": 197.30
}
```
Returns auto-computed `return_1w`, `return_1m`, etc.

---

## Connecting the Scraper

After running the openinsiderData scraper (which outputs a CSV), load it into PostgreSQL:

```bash
# Quick load via psql COPY
psql -U insider_user -d insider_db -c \
  "\COPY insider_trades(filing_date, trade_date, ticker, company_name, insider_name, \
   insider_title, transaction_type, price, qty, owned, delta_own, value) \
   FROM 'data/insider.csv' CSV HEADER;"
```

Or use the scraper integration script (coming next) to automate this fully.

---

## Systemd Service (production VPS)

```ini
# /etc/systemd/system/insider-api.service
[Unit]
Description=Insider Trading Tracker API
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/insider-api
ExecStart=/home/ubuntu/insider-api/venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
Restart=always
EnvironmentFile=/home/ubuntu/insider-api/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable insider-api
sudo systemctl start insider-api
```

---

## Next Steps

- [ ] Add Nginx reverse proxy config
- [ ] Add scraper integration script (auto-loads CSV → PostgreSQL)
- [ ] Add price-update cron job (pulls stock prices for performance tracking)
- [ ] Lock down the API with a simple API key header for your frontend
