"""
Configuration loader.
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

CONFIG_PATH = os.environ.get("ERACUN_CONFIG", "/app/config.yaml")


def load_config(path: str = None) -> dict:
    """Load YAML config file."""
    p = Path(path or CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_eracun_config(cfg: dict) -> dict:
    return cfg.get("eracun", {})


def get_users(cfg: dict) -> dict:
    return cfg.get("users", {})


def get_database_url(cfg: dict) -> str:
    return cfg.get("database", {}).get(
        "url", "postgresql://eracun:eracun_secret@postgres:5432/eracun_portal"
    )


def get_dodois_config(cfg: dict) -> dict:
    return cfg.get("dodois", {})


def get_storage_config(cfg: dict) -> dict:
    return cfg.get("storage", {
        "pdf_dir": "/app/data/pdfs",
        "xml_dir": "/app/data/xmls",
    })
