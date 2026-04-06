# TESTING.md — Test Structure & Practices

## Current State
**No test infrastructure exists.** There are no test files, no `pytest.ini`, no `conftest.py`, and no test dependencies in `requirements.txt`.

## Test Framework Recommendation
- **pytest** — standard for Python projects of this size
- **pytest-asyncio** — if async patterns are added
- **httpx** mock transport for `eracun_client.py` (already uses `httpx`)
- SQLAlchemy in-memory SQLite for DB layer tests

## What Should Be Tested

### Unit Tests (pure logic)
- `app/core/ubl_parser.py` — XML parsing, OIB extraction, line aggregation
  - Test multiple OIB formats: `HR38016445738`, `9934:38016445738`, plain
  - Test base64 PDF extraction
  - Test duplicate line aggregation logic
- `app/core/config_loader.py` — YAML loading, supplier/product mapping lookups
- Price calculation formulas (unit=1, unit=5, unit=8, Vindi Sok edge case)

### Integration Tests
- `app/core/invoice_sync.py` — mock `eracun_client`, assert DB writes
- `app/db/models.py` — CRUD operations against in-memory SQLite

### Mocking Approach
```python
# Mock httpx for eracun_client
import httpx
from unittest.mock import patch

with patch.object(httpx.Client, 'post') as mock_post:
    mock_post.return_value = httpx.Response(200, json={...})
    ...

# In-memory DB for models
from sqlalchemy import create_engine
engine = create_engine("sqlite:///:memory:")
```

## Coverage Gaps (Priority Order)
1. UBL XML parser — complex logic, multiple edge cases, no tests
2. Price calculation — critical for correctness, formula has known gotchas
3. OIB normalization — multiple format variants
4. Invoice sync deduplication logic
5. Auth (bcrypt verification) — low risk but untested

## CI
No CI pipeline configured. No GitHub Actions or similar.
