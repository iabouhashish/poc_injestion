"""
Crestview Capital Group — AI-Powered Client Onboarding Pipeline

Two-phase operation:
  Phase 1 (Agent 1):  python main.py --phase=1
  Phase 2 (Agent 2):  python main.py --phase=2

  Demo reset:         python main.py --clear-db
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crestview.pipeline")

_api_key = os.getenv("ANTHROPIC_API_KEY", "")
if _api_key.startswith("sk-ant-") and "xxx" not in _api_key:
    logger.warning("Real ANTHROPIC_API_KEY detected — ensure .env is NOT committed to version control")

from rich.console import Console

from persistence import create_all_tables
from src.run_phase1 import run_phase1
from src.run_phase2 import run_phase2

console = Console()


def clear_database() -> None:
    """Delete the SQLite database file and recreate empty tables."""
    db_path = Path(os.getenv("DATABASE_PATH", "data/crestview_pipeline.db"))
    if db_path.exists():
        db_path.unlink()
        console.print(f"[green]Deleted:[/green] {db_path}")
    else:
        console.print(f"[dim]No database found at {db_path} — nothing to delete.[/dim]")
    create_all_tables()
    console.print("[bold green]Database cleared. Ready for a fresh demo run.[/bold green]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crestview Capital Group — AI Onboarding Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --phase=1\n"
            "  python main.py --phase=2\n"
            "  python main.py --clear-db\n"
        ),
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2],
        default=1,
        help="Pipeline phase: 1 = Agent 1 (extraction), 2 = Agent 2 (compliance). Default: 1",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Process only the application with this ID (works for both phases)",
    )
    parser.add_argument(
        "--clear-db",
        action="store_true",
        help="Delete the database and recreate empty tables (demo reset)",
    )
    args = parser.parse_args()

    if args.clear_db:
        clear_database()
    elif args.phase == 1:
        run_phase1(client_id=args.client_id)
    elif args.phase == 2:
        run_phase2(client_id=args.client_id)


if __name__ == "__main__":
    main()
