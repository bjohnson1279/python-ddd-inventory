# DDD Inventory API

Python Domain-Driven Design (DDD) Inventory and ERP API built with FastAPI, SQLAlchemy, and AsyncIO.

## Overview

This project provides a multi-tenant Inventory Management & ERP system supporting product catalogs, stock management, serial/kit tracking, transfers, UOM conversions, costing (FIFO/LIFO/Average), procurement, physical counts, workforce management, financial accounting, event delivery, and integrations.

## Documentation Index

The following guides provide detailed specifications for each subsystem:

- [Identity & Catalog Setup](IDENTITY_CATALOG.md)
- [Serials & Kits](SERIALS_KITS.md)
- [Costing & Valuation](COSTING.md)
- [Transfers & UOM Conversions](TRANSFERS_UOM.md)
- [Event Delivery Architecture](EVENT_DELIVERY.md)
- [Financial Accounting](FINANCIAL_ACCOUNTING.md)
- [Integrations (Shopify, WooCommerce, Amazon)](INTEGRATIONS.md)
- [Procurement & Replenishment](PROCUREMENT.md)
- [Physical Counts & Reconciliation](COUNTS.md)
- [Returns & Quarantine](RETURNS.md)
- [Reporting & Alerts](REPORTING_ALERTS.md)
- [Inventory Aging & Dead Stock Analysis](AGING.md)
- [Workforce Management](WORKFORCE.md)
- [Security & RBAC](SECURITY.md)

## Quick Start & Local Setup

### Prerequisites

- Python 3.11 or higher
- Docker & Docker Compose (optional, for PostgreSQL/Kafka setup)

### Environment Variables

Set the following environment variables before running the application:

```bash
export DATABASE_URL="sqlite+aiosqlite:///./inventory.db"  # or postgresql+asyncpg://postgres:postgres@localhost:5432/inventory
export JWT_SECRET="your-secret-key"
export CORS_ORIGINS="http://localhost:3000,http://localhost:5173"
```

### Installation

1. Install required Python packages:

```bash
pip install -r requirements.txt
```

2. Initialize / Migrate Database:

```bash
python -m scripts.migrate_identity_catalog
```

### Running the API Server

Run the development server using `uvicorn`:

```bash
uvicorn src.presentation.main:app --host 0.0.0.0 --port 8000 --reload
```

Or using Docker Compose:

```bash
docker-compose up --build
```

Access Swagger API documentation at `http://localhost:8000/docs` or OpenAPI spec at `http://localhost:8000/openapi.json`.

### Running Tests

Execute the test suite using `pytest`:

```bash
PYTHONPATH=. python -m pytest tests/presentation/test_auth_catalog.py
```

To run all presentation tests:

```bash
PYTHONPATH=. python -m pytest tests/presentation/
```
