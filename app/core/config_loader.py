"""
Configuration loader.
Supports config overlay: loads config.yaml, then merges config.local.yaml on top.
"""

import copy
import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

CONFIG_PATH = os.environ.get("ERACUN_CONFIG", "/app/config.yaml")


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override values win."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str = None) -> dict:
    """Load YAML config, then overlay config.local.yaml if it exists."""
    p = Path(path or CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    local_path = p.parent / "config.local.yaml"
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            local_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local_cfg)

    return cfg


def get_eracun_config(cfg: dict) -> dict:
    return cfg.get("eracun", {})


def get_users(cfg: dict) -> dict:
    return cfg.get("users", {})


def get_database_url(cfg: dict) -> str:
    return cfg.get("database", {}).get(
        "url", "postgresql://eracun:eracun_secret@postgres:5432/e_rachun_dodois"
    )


def get_dodois_config(cfg: dict) -> dict:
    return cfg.get("dodois", {})


def get_dodois_suppliers(cfg: dict) -> dict:
    """Return dodois_suppliers section as-is."""
    return cfg.get("dodois_suppliers", {})


def get_dodois_supplier_by_oib(cfg: dict, oib: str) -> Optional[dict]:
    """Find a dodois supplier config entry by eRačun OIB. Returns None if not found."""
    for key, supplier in get_dodois_suppliers(cfg).items():
        # Support both old 'oib' field and new 'eracun_oib'
        if supplier.get("eracun_oib") == oib or supplier.get("oib") == oib:
            return supplier
    return None


def get_dodois_supplier_by_name(cfg: dict, sender_name: str) -> Optional[dict]:
    """Find a dodois supplier config entry by eRačun sender name (substring match).
    Falls back to None if not found."""
    sender_lower = sender_name.lower()
    for key, supplier in get_dodois_suppliers(cfg).items():
        eracun_name = supplier.get("eracun_name", "")
        if eracun_name and (
            eracun_name.lower() in sender_lower or sender_lower in eracun_name.lower()
        ):
            return supplier
    return None


def is_dodois_supplier(cfg: dict, oib: str) -> bool:
    """Check if supplier with given OIB is configured AND enabled for Dodois upload."""
    supplier = get_dodois_supplier_by_oib(cfg, oib)
    return supplier is not None and supplier.get("enabled", False)


def get_storage_config(cfg: dict) -> dict:
    return cfg.get("storage", {
        "pdf_dir": "/app/data/pdfs",
        "xml_dir": "/app/data/xmls",
    })
