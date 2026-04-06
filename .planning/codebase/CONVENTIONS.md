# CONVENTIONS.md — Code Style & Patterns

## Language & Style
- Python 3.12, follows PEP 8
- 4-space indentation throughout
- Type hints used on function signatures (dataclasses, return types)
- Module-level docstrings and section comments using `# ====` dividers

## Naming Conventions
- **Functions/variables:** `snake_case`
- **Classes:** `PascalCase`
- **Private/internal helpers:** `_underscore_prefix`
- **Constants:** `UPPER_SNAKE_CASE` (rare; mostly config-driven)
- **Config keys:** `snake_case` in YAML matching Python attribute names

## Import Organization
1. Standard library (`os`, `re`, `datetime`, `logging`, `uuid`, `base64`)
2. Third-party (`streamlit`, `sqlalchemy`, `httpx`, `bcrypt`, `pydantic`, `yaml`)
3. Local (`app.core.*`, `app.db.*`)

## Error Handling
- Broad `except Exception as e` with `st.error(...)` display in UI layer
- `logger.error(...)` with exception info in service layer
- Graceful degradation: failed operations return `None` or empty list rather than raising
- No custom exception hierarchy — relies on stdlib/library exceptions

## Logging
- `logging.getLogger(__name__)` at module level
- Log levels: `info` for sync events, `error` for failures, `warning` for soft issues
- No structured logging — plain string messages

## Streamlit Patterns
- `st.session_state` for auth state (`authenticated`, `username`)
- `@st.cache_data` / `@st.cache_resource` for DB sessions and expensive fetches
- Callback functions for button interactions (avoids double-render)
- `st.rerun()` after state mutations
- Page layout: wide mode, custom orange theme via `.streamlit/config.toml`

## Data Modeling
- SQLAlchemy ORM with `DeclarativeBase`
- Dataclasses for parsed invoice/line DTOs (`@dataclass`)
- Config loaded into dataclasses via `config_loader.py`

## Configuration
- Single `config.yaml` is the source of truth for all settings
- No environment variable usage (credentials stored in YAML)
- DB URL, users, supplier/product mappings all in YAML

## Comments
- Section dividers: `# ===== SECTION NAME =====`
- Inline comments for non-obvious logic (Croatian OIB format handling, price formulas)
- No JSDoc/Sphinx-style docstrings in most places
