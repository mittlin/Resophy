#!/usr/bin/env python3
"""Daily arXiv API trigger script.

Usage:
    python scripts/daily_arxiv_trigger.py fetch <category> [options]
    python scripts/daily_arxiv_trigger.py fetch-all [options]
    python scripts/daily_arxiv_trigger.py progress <category>
    python scripts/daily_arxiv_trigger.py check-llm
    python scripts/daily_arxiv_trigger.py settings
    python scripts/daily_arxiv_trigger.py scheduler-start
    python scripts/daily_arxiv_trigger.py cleanup [--retention-days N]

Options:
    --base-url URL     Server base URL (default: http://localhost:5000)
    --date YYYY-MM-DD  Target date (default: today)
    --force            Re-fetch even if already exists
    --wait             Poll progress until complete
    --timeout SECONDS  Max wait time in seconds (default: 300)
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse


def api_call(method, url, data=None, base_url="http://localhost:5000"):
    full_url = base_url.rstrip("/") + url
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(full_url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"success": False, "error": f"HTTP {e.code}: {error_body}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection failed: {e.reason}"}


def cmd_fetch(args):
    data = {"category": args.category, "force": args.force}
    if args.date:
        data["date"] = args.date
    result = api_call("POST", "/api/daily-arxiv/fetch", data, args.base_url)
    if result.get("success"):
        print(f"[OK] {result['message']}")
        if args.wait:
            poll_progress(args.category, args.base_url, args.timeout)
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_fetch_all(args):
    data = {"force": args.force}
    if args.date:
        data["date"] = args.date
    result = api_call("POST", "/api/daily-arxiv/fetch-all", data, args.base_url)
    if result.get("success"):
        print(f"[OK] {result['message']}")
        if args.wait and result.get("categories"):
            for cat in result["categories"]:
                poll_progress(cat, args.base_url, args.timeout)
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_progress(args):
    result = api_call("GET", f"/api/daily-arxiv/progress/{args.category}", base_url=args.base_url)
    if result.get("success"):
        progress = result.get("progress", {})
        total = progress.get("total", 0)
        processed = progress.get("processed", 0)
        status = progress.get("status", "unknown")
        print(f"Category: {args.category}")
        print(f"Status: {status}")
        print(f"Progress: {processed}/{total}")
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_check_llm(args):
    result = api_call("GET", "/api/daily-arxiv/check-llm-config", base_url=args.base_url)
    if result.get("success"):
        configured = result.get("is_configured", False)
        failed = result.get("llm_api_failed", False)
        error_msg = result.get("llm_api_error_message", "")
        print(f"LLM Configured: {configured}")
        print(f"LLM API Failed: {failed}")
        if error_msg:
            print(f"Error: {error_msg}")
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_settings(args):
    result = api_call("GET", "/api/settings/daily-arxiv", base_url=args.base_url)
    if isinstance(result, dict) and "categories" in result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[ERROR] Unexpected response: {result}", file=sys.stderr)
        sys.exit(1)


def cmd_scheduler_start(args):
    result = api_call("POST", "/api/daily-arxiv/scheduler/start", base_url=args.base_url)
    if result.get("success"):
        print(f"[OK] {result['message']}")
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def cmd_cleanup(args):
    data = {}
    if args.retention_days is not None:
        data["retention_days"] = args.retention_days
    result = api_call("POST", "/api/daily-arxiv/cleanup", data, args.base_url)
    if result.get("success"):
        print(f"[OK] {result['message']}")
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


def poll_progress(category, base_url, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        result = api_call("GET", f"/api/daily-arxiv/progress/{category}", base_url=base_url)
        if not result.get("success"):
            print(f"  [WARN] Progress poll failed: {result.get('error')}")
            time.sleep(3)
            continue
        progress = result.get("progress", {})
        status = progress.get("status", "")
        total = progress.get("total", 0)
        processed = progress.get("processed", 0)
        if total > 0:
            pct = processed / total * 100
            print(f"  [{category}] {processed}/{total} ({pct:.0f}%) - {status}")
        else:
            print(f"  [{category}] waiting for tasks... - {status}")
        if status == "completed" or status == "idle":
            print(f"  [{category}] Done.")
            return
        time.sleep(3)
    print(f"  [WARN] Timeout waiting for {category} after {timeout}s", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Daily arXiv API Trigger")
    parser.add_argument("--base-url", default="http://localhost:5000", help="Server base URL")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Force re-fetch")
    parser.add_argument("--wait", action="store_true", help="Poll progress until complete")
    parser.add_argument("--timeout", type=int, default=300, help="Max wait seconds (default: 300)")
    parser.add_argument("--retention-days", type=int, help="Retention days for cleanup")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_fetch = subparsers.add_parser("fetch", help="Fetch a single category")
    p_fetch.add_argument("category", help="arXiv category, e.g. cs.CV")

    p_fetch_all = subparsers.add_parser("fetch-all", help="Fetch all configured categories")

    p_progress = subparsers.add_parser("progress", help="Check fetch progress")
    p_progress.add_argument("category", help="arXiv category")

    subparsers.add_parser("check-llm", help="Check LLM configuration status")
    subparsers.add_parser("settings", help="Show current Daily arXiv settings")
    subparsers.add_parser("scheduler-start", help="Manually start the scheduler")
    subparsers.add_parser("cleanup", help="Clean up old papers")

    args = parser.parse_args()
    for opt in ("base_url", "date", "force", "wait", "timeout", "retention_days"):
        if not hasattr(args, opt):
            setattr(args, opt, None)

    commands = {
        "fetch": cmd_fetch,
        "fetch-all": cmd_fetch_all,
        "progress": cmd_progress,
        "check-llm": cmd_check_llm,
        "settings": cmd_settings,
        "scheduler-start": cmd_scheduler_start,
        "cleanup": cmd_cleanup,
    }

    cmd = commands.get(args.command)
    if cmd:
        cmd(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
