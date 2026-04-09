"""
Dump Dodois supplies (invoiceNumber + supplierName) for inspection.

Usage: python scripts/dump_supplies.py [--from DATE] [--to DATE] [--supplier NAME]
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="2025-01-01")
    parser.add_argument("--to", dest="to_date", default=str(date.today()))
    parser.add_argument("--supplier", default=None,
                        help="Filter by supplierName substring (case-insensitive)")
    args = parser.parse_args()

    from app.core.config_loader import load_config
    from app.core.dodois_auth import DodoisSession
    from app.core.dodois_client import DodoisClient

    cfg = load_config(os.environ.get("ERACUN_CONFIG", "config.yaml"))
    dodois_cfg = cfg.get("dodois", {})
    ds = DodoisSession(
        dodois_cfg["username"],
        dodois_cfg["password"],
        dodois_cfg.get("totp_secret", ""),
    )
    client = DodoisClient(ds)
    pizzerias = dodois_cfg.get("pizzerias", {})

    all_supplies = []
    for piz_key, piz in pizzerias.items():
        dept_id = piz.get("department_id", "")
        if not dept_id:
            continue
        supplies = client.get_all_supplies(dept_id, args.from_date, args.to_date)
        all_supplies.extend(supplies)

    if args.supplier:
        needle = args.supplier.upper()
        all_supplies = [s for s in all_supplies if needle in s.get("supplierName", "").upper()]

    by_supplier: dict[str, list[str]] = {}
    for s in all_supplies:
        name = s.get("supplierName", "?")
        inv = s.get("invoiceNumber", "")
        by_supplier.setdefault(name, []).append(inv)

    for name in sorted(by_supplier):
        invoices = by_supplier[name]
        print(f"\n=== {name} ({len(invoices)}) ===")
        for inv in invoices:
            print(f"  {inv}")


if __name__ == "__main__":
    main()
