"""
MarketScope CLI Management Utility
Commands: init-owner, reset-mfa, status, backup, candle-stats, doctor,
check-ollama, import-stock-master.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import getpass
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Add current folder to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from env_loader import load_dotenv
load_dotenv()

from security import (
    DatabaseManager,
    generate_totp_secret,
    get_totp_token,
    hash_password,
    verify_password,
    verify_totp,
)

DEFAULT_DB_PATH = os.environ.get(
    "MS_DB_PATH",
    str(Path(__file__).parent.parent / "work" / "runtime" / "marketscope.db"),
)


def get_db() -> DatabaseManager:
    return DatabaseManager(DEFAULT_DB_PATH)


def cmd_init_owner(args: argparse.Namespace) -> None:
    db = get_db()
    if db.has_owner():
        print("Error: An owner account already exists. For a lost authenticator, use reset-mfa with the existing owner password.", file=sys.stderr)
        sys.exit(1)

    email = getattr(args, "email", None)
    if not email:
        email = input("Enter Owner Email: ").strip()
    if not email or "@" not in email:
        print("Error: Invalid email address.", file=sys.stderr)
        sys.exit(1)

    password = getattr(args, "password", None)
    if not password:
        import getpass
        password = getpass.getpass("Enter Owner Password (min 8 chars): ")
        confirm = getpass.getpass("Confirm Password: ")
        if password != confirm:
            print("Error: Passwords do not match.", file=sys.stderr)
            sys.exit(1)

    if len(password) < 8:
        print("Error: Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    totp_secret = generate_totp_secret()
    uid = db.create_user(email=email, password=password, role="owner", totp_secret=totp_secret, plan="owner")

    sample_token, _ = get_totp_token(totp_secret)
    print("\n" + "=" * 60)
    print("MarketScope Owner Account Created Successfully!")
    print(f"Owner ID:    {uid}")
    print(f"Email:       {email}")
    print(f"TOTP Secret: {totp_secret}")
    print(f"Sample 2FA Code (valid now): {sample_token}")
    print("=" * 60)
    print("Save this TOTP secret in your authenticator app (Google Authenticator, Authy, etc.).")


def cmd_status(args: argparse.Namespace) -> None:
    db = get_db()
    print(f"Database Path: {DEFAULT_DB_PATH}")
    if not os.path.exists(DEFAULT_DB_PATH):
        print("Database status: Not initialized.")
        return

    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM users")
        user_count = cur.fetchone()[0]
        cur.execute("SELECT email, role, created_at FROM users WHERE role = 'owner'")
        owner = cur.fetchone()
        cur.execute("SELECT count(*) FROM sessions")
        session_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM config_versions")
        config_versions = cur.fetchone()[0]

    print(f"Users Count:     {user_count}")
    print(f"Owner:           {owner['email'] if owner else 'None configured'}")
    print(f"Active Sessions: {session_count}")


def cmd_reset_mfa(args: argparse.Namespace) -> None:
    """Password-verified local recovery; secrets stay in the user's terminal."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Error: reset-mfa requires an interactive local terminal. No authenticator was changed.", file=sys.stderr)
        raise SystemExit(1)
    db_path = Path(getattr(args, "db", None) or DEFAULT_DB_PATH).resolve()
    if not db_path.is_file():
        print("Error: Owner database does not exist. No account was changed.", file=sys.stderr)
        raise SystemExit(1)
    db = DatabaseManager(str(db_path))
    with db.get_connection() as conn:
        owner = conn.execute("SELECT id,email,password_hash FROM users WHERE role='owner' AND disabled=0").fetchone()
    if not owner or not verify_password(getpass.getpass("Current owner password: "), owner["password_hash"]):
        print("Error: Owner credentials were not verified. No authenticator was changed.", file=sys.stderr)
        raise SystemExit(1)
    print("This replaces the owner authenticator and signs out all existing sessions.")
    if input("Type RESET to continue, or press Enter to cancel: ").strip() != "RESET":
        print("Cancelled. No authenticator was changed.")
        return
    secret = generate_totp_secret()
    label = urllib.parse.quote("Zien Quant:" + owner["email"], safe="")
    uri = f"otpauth://totp/{label}?secret={secret}&issuer=Zien%20Quant&digits=6&period=30"
    print("Add this new account to your authenticator app before continuing.")
    print(f"Account: {owner['email']}")
    print(f"Setup key: {secret}")
    print(f"Enrollment URI: {uri}")
    accepted_step = None
    for _ in range(3):
        code = getpass.getpass("New authenticator's six-digit code (hidden): ")
        valid, step = verify_totp(secret, code)
        if valid:
            accepted_step = step
            break
        print("Code did not match. Check the new authenticator account and device time.")
    if accepted_step is None:
        print("Error: New authenticator was not verified. Existing enrollment remains unchanged.", file=sys.stderr)
        raise SystemExit(1)
    with db.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT password_hash,disabled FROM users WHERE id=?", (owner["id"],)).fetchone()
        if not row or row["disabled"] or row["password_hash"] != owner["password_hash"]:
            print("Error: Account changed during enrollment. No authenticator was changed.", file=sys.stderr)
            raise SystemExit(1)
        conn.execute("UPDATE users SET totp_secret=?,totp_last_step=?,updated_at=? WHERE id=?",
                     (secret,accepted_step,datetime.now(timezone.utc).isoformat(),owner["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (owner["id"],))
        conn.execute("DELETE FROM recovery_codes WHERE user_id=?", (owner["id"],))
        db.log_audit(conn,"owner_mfa_reenrolled",owner["email"],"Password-verified local re-enrollment; previous sessions revoked")
    print("Authenticator replaced successfully. Wait for a fresh code, then sign in to the dashboard.")


def cmd_backup(args: argparse.Namespace) -> None:
    if not os.path.exists(DEFAULT_DB_PATH):
        print("Database file does not exist yet.", file=sys.stderr)
        sys.exit(1)
    backup_dir = Path(__file__).parent.parent / "work" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"marketscope_backup_{ts}.db"

    # Use SQLite online backup API for ACID safety
    src_conn = sqlite3.connect(DEFAULT_DB_PATH)
    dst_conn = sqlite3.connect(str(backup_file))
    with dst_conn:
        src_conn.backup(dst_conn)
    src_conn.close()
    dst_conn.close()

    print(f"Backup created successfully: {backup_file}")


def cmd_candle_stats(args: argparse.Namespace) -> None:
    try:
        from marketscope.candle_archive import get_candle_archive
    except ImportError:
        from candle_archive import get_candle_archive
    archive = get_candle_archive()
    stats = archive.get_stats()
    if not stats:
        print("No archived candles found.")
        return

    print(f"{'SYMBOL':<12} {'TIMEFRAME':<10} {'ROWS':<10} {'FROM (UTC)':<22} {'TO (UTC)':<22}")
    print("-" * 80)
    total_rows = 0
    for s in stats:
        total_rows += s["count"]
        from_str = datetime.fromtimestamp(s["min_ts"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if s["min_ts"] else "N/A"
        to_str = datetime.fromtimestamp(s["max_ts"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if s["max_ts"] else "N/A"
        print(f"{s['symbol']:<12} {s['timeframe']:<10} {s['count']:<10} {from_str:<22} {to_str:<22}")
    print("-" * 80)
    print(f"Total archived candles: {total_rows} across {len(stats)} series.")


def cmd_doctor(args: argparse.Namespace) -> None:
    import platform
    import urllib.request
    import json

    print("================================================================")
    print("             ZIEN QUANT TERMINAL HEALTH DOCTOR                  ")
    print("================================================================")

    # 1. Environment & Platform
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 12)
    os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
    print(f"[{'PASS' if py_ok else 'WARN'}] Python Version:       {py_ver} {'(Requires >= 3.12)' if not py_ok else '(Supported)'}")
    print(f"[INFO] Operating System:     {os_name}")

    # 2. Disk Space
    try:
        usage = shutil.disk_usage(os.getcwd())
        free_gb = usage.free / (1024**3)
        disk_ok = free_gb > 1.0
        print(f"[{'PASS' if disk_ok else 'WARN'}] Free Disk Space:      {free_gb:.2f} GB available")
    except Exception as e:
        print(f"[WARN] Disk Check:           Failed ({e})")

    # 3. Database Files
    db_path = Path(DEFAULT_DB_PATH)
    if db_path.is_file():
        db_size_kb = db_path.stat().st_size / 1024
        print(f"[PASS] App Database:         {db_path.name} ({db_size_kb:.1f} KB)")
    else:
        print(f"[WARN] App Database:         {db_path.name} not created yet (run migrations or init-owner)")

    stock_db_path = Path(os.environ.get("MS_STOCK_DB", str(Path(__file__).parent.parent / "work" / "runtime" / "stock_master.db")))
    if stock_db_path.is_file():
        try:
            from stock_database import get_stock_database
            sdb = get_stock_database()
            scnt = sdb.get_stock_count()
            print(f"[PASS] Stock Master DB:      {stock_db_path.name} ({scnt} symbols loaded)")
        except Exception:
            print(f"[PASS] Stock Master DB:      {stock_db_path.name} (file exists)")
    else:
        print(f"[WARN] Stock Master DB:      {stock_db_path.name} not initialized (run: manage.py import-stock-master)")

    candles_db_path = Path(os.environ.get("MS_CANDLES_DB", str(Path(__file__).parent.parent / "work" / "runtime" / "candles.db")))
    if candles_db_path.is_file():
        try:
            from candle_archive import get_candle_archive
            stats = get_candle_archive().get_stats()
            total_c = sum(s["count"] for s in stats)
            print(f"[PASS] Candle Archive:       {candles_db_path.name} ({total_c} candles across {len(stats)} series)")
        except Exception:
            print(f"[PASS] Candle Archive:       {candles_db_path.name} (file exists)")
    else:
        print(f"[INFO] Candle Archive:       {candles_db_path.name} (will populate on first feed fetch)")

    # 4. Migration Level
    if db_path.is_file():
        try:
            db = get_db()
            with db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM config_versions")
                cv_count = cur.fetchone()[0]
                print(f"[PASS] Migration Level:      Initialized (config version: {cv_count})")
        except Exception as e:
            print(f"[WARN] Migration Check:      Error reading schema: {e}")

    # 5. Owner Account
    if db_path.is_file():
        try:
            db = get_db()
            if db.has_owner():
                with db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT email, created_at FROM users WHERE role = 'owner'")
                    row = cur.fetchone()
                    print(f"[PASS] Owner Account:        Configured ({row['email']})")
            else:
                print(f"[WARN] Owner Account:        Not configured (run: manage.py init-owner)")
        except Exception:
            print(f"[WARN] Owner Account:        Unable to inspect owner account")
    else:
        print(f"[WARN] Owner Account:        Database not initialized (run: manage.py init-owner)")

    # 6. Ollama Reachability & Model
    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not ollama_host.startswith("http://") and not ollama_host.startswith("https://"):
        ollama_host = f"http://{ollama_host}"
    target_model = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
    ollama_ok = False
    model_found = False
    try:
        req = urllib.request.Request(f"{ollama_host}/api/tags", headers={"User-Agent": "ZienQuant/0.1.0"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ollama_ok = True
            models = [m.get("name", "") for m in data.get("models", [])]
            for m in models:
                if target_model in m or m.startswith(target_model.split(":")[0]):
                    model_found = True
                    break
        if ollama_ok:
            print(f"[PASS] Ollama Service:       Reachable at {ollama_host}")
            if model_found:
                print(f"[PASS] Ollama Model:         '{target_model}' is pulled and ready")
            else:
                print(f"[WARN] Ollama Model:         '{target_model}' not found in Ollama (run: ollama pull {target_model})")
            if any(m == "laya" or m.startswith("laya:") for m in models):
                print(f"[PASS] Laya Decision Model:  'laya' (421M System 1) is active")
    except Exception:
        print(f"[INFO] Ollama Service:       Not reachable at {ollama_host} (optional: terminal falls back to rules engine)")

    # 7. Market Data Source Reachability
    try:
        test_url = "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?interval=5m&range=1d"
        req = urllib.request.Request(test_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status in (200, 203):
                print(f"[PASS] Market Data Source:   Reachable (HTTP {resp.status})")
            else:
                print(f"[WARN] Market Data Source:   HTTP status {resp.status}")
    except Exception as e:
        print(f"[WARN] Market Data Source:   Network unreachable ({e}) (terminal will use Previous Session view)")

    # 8. Session State
    try:
        from market_schedule import get_market_schedule_info
        sched = get_market_schedule_info()
        st = sched.get("state", "UNKNOWN")
        lbl = sched.get("label", "")
        print(f"[INFO] Current Session:      {st} ({lbl})")
    except Exception as e:
        print(f"[INFO] Current Session:      Schedule engine check error: {e}")

    print("================================================================")


def cmd_check_ollama(args: argparse.Namespace) -> None:
    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not ollama_host.startswith("http://") and not ollama_host.startswith("https://"):
        ollama_host = f"http://{ollama_host}"
    target_model = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
    try:
        import urllib.request
        req = urllib.request.Request(f"{ollama_host}/api/tags", headers={"User-Agent": "ZienQuant/0.1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            if any(target_model in m or m.startswith(target_model.split(":")[0]) for m in models):
                print(f"[OK] Ollama is active and model '{target_model}' is pulled.")
            else:
                print(f"[WARN] Ollama is running, but model '{target_model}' is not pulled.")
                print(f"       Run: ollama pull {target_model}")
            if any(m == "laya" or m.startswith("laya:") for m in models):
                print("[OK] Laya decision model ('laya') is active.")
    except Exception:
        print(f"[INFO] Ollama not detected at {ollama_host} (Optional: terminal falls back to rules engine).")


def cmd_import_stock_master(args: argparse.Namespace) -> None:
    from stock_database import get_stock_database
    sdb = get_stock_database()
    csv_path = getattr(args, "csv", None)
    if csv_path and os.path.isfile(csv_path):
        print(f"Importing stock master data from: {csv_path}...")
        try:
            with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            count = sdb.populate_from_nse_master(content)
            print(f"Successfully imported {count} stocks from {csv_path}.")
        except Exception as e:
            print(f"Error importing from {csv_path}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("No CSV file provided or file not found. Attempting download from official NSE master...")
        count = sdb.populate_from_nse_master()
        if count > 0:
            print(f"Successfully downloaded and loaded {count} equities from official NSE master.")
        else:
            print("Download unsuccessful or offline. Seeding default top-100 liquid universe...")
            count = sdb.seed_default_universe()
            print(f"Seeded {count} liquid equities into stock master database.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MarketScope CLI Manager")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init-owner", help="Initialize the single owner account")
    init_parser.add_argument("--email", help="Owner email address")
    init_parser.add_argument("--password", help="Owner password")
    recovery_parser = subparsers.add_parser("reset-mfa", help="Interactively replace a lost owner authenticator using the current password")
    recovery_parser.add_argument("--db", help="Optional path to the existing owner database")
    subparsers.add_parser("status", help="Show database and account status")
    subparsers.add_parser("backup", help="Create an ACID-safe SQLite database backup")
    subparsers.add_parser("candle-stats", help="Show candle archive row counts and date ranges")
    subparsers.add_parser("doctor", help="Run comprehensive health and diagnostic check")
    subparsers.add_parser("check-ollama", help="Check Ollama reachability and model availability")
    subparsers.add_parser("results-status", help="Show financial-results collection and history coverage")
    subparsers.add_parser("results-refresh", help="Collect one bounded batch of official results and market history")
    subparsers.add_parser("margins-status", help="Show Zerodha MIS reference freshness and strict-above-3x coverage")
    subparsers.add_parser("margins-refresh", help="Refresh Zerodha's published equity intraday margin list")
    filings_import = subparsers.add_parser("results-import-filings", help="Import an official NSE financial-filings CSV or JSON export")
    filings_import.add_argument("file", help="Downloaded official filing metadata export")
    filings_import.add_argument("--source", default="https://www.nseindia.com/companies-listing/corporate-filings-financial-results", help="Official NSE source page URL")
    results_backfill = subparsers.add_parser("results-backfill", help="Collect resumable historical announcement batches independently of today's feed")
    results_backfill.add_argument("--from", dest="date_from", help="First publication date, YYYY-MM-DD")
    results_backfill.add_argument("--to", dest="date_to", help="Last publication date, YYYY-MM-DD")
    results_backfill.add_argument("--batches", type=int, default=12, help="Maximum 30-day batches this run")
    results_backfill.add_argument("--enrich", action="store_true", help="Also process one bounded batch of financial documents")
    results_import = subparsers.add_parser("results-import-candles", help="Import a real, sourced historical candle CSV for earnings research")
    results_import.add_argument("csv", help="CSV with symbol,t,o,h,l,c,v headers")
    results_import.add_argument("--source", required=True, help="Origin of the observations, such as your historical data provider")
    results_import.add_argument("--timeframe", type=int, choices=(5, 1440), default=5)

    import_parser = subparsers.add_parser("import-stock-master", help="Import equity master database from CSV or seed defaults")
    import_parser.add_argument("csv", nargs="?", default=None, help="Path to NSE/BSE master CSV (optional)")

    args = parser.parse_args()
    if args.command == "init-owner":
        cmd_init_owner(args)
    elif args.command == "reset-mfa":
        cmd_reset_mfa(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "backup":
        cmd_backup(args)
    elif args.command == "candle-stats":
        cmd_candle_stats(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "check-ollama":
        cmd_check_ollama(args)
    elif args.command in ("results-status", "results-refresh"):
        from results_service import get_results_service
        service = get_results_service()
        if args.command == "results-refresh":
            service.refresh()
        print(json.dumps(service.status(), indent=2, default=str))
    elif args.command in ("margins-status", "margins-refresh"):
        from intraday_margins import get_intraday_margins
        service = get_intraday_margins()
        payload = service.refresh() if args.command == "margins-refresh" else service.payload()
        print(json.dumps({key: value for key, value in payload.items() if key != "records"}, indent=2, default=str))
    elif args.command == "results-import-filings":
        from results_service import get_results_service
        try:
            source_file = Path(args.file)
            if source_file.stat().st_size > 100_000_000:
                raise ValueError("The export exceeds 100 MB.")
            with source_file.open(encoding="utf-8-sig", newline="") as stream:
                if source_file.suffix.lower() == ".csv":
                    import csv
                    rows = list(csv.DictReader(stream))
                else:
                    rows = json.load(stream)
                    if isinstance(rows, dict):
                        rows = rows.get("data")
            result = get_results_service().import_filings(rows, source_url=args.source)
        except (ValueError, OSError) as exc:
            print(f"Import rejected: {exc}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "results-backfill":
        from results_service import get_results_service
        try:
            result = get_results_service().backfill(max_batches=args.batches, date_from=args.date_from, date_to=args.date_to, enrich=args.enrich)
        except (ValueError, OSError) as exc:
            print(f"Backfill could not start: {exc}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "results-import-candles":
        from candle_archive import get_candle_archive
        from results_history import import_candle_csv
        try:
            result = import_candle_csv(args.csv, get_candle_archive(), source=args.source, timeframe=args.timeframe)
        except (ValueError, OSError) as exc:
            print(f"Import rejected: {exc}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))
    elif args.command == "import-stock-master":
        cmd_import_stock_master(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
