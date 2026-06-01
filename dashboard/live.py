#!/usr/bin/env python3
"""
Store Intelligence — Live Dashboard
=====================================

Rich-based terminal dashboard that polls the API every few seconds and
displays live metrics.  Designed to run alongside the detection pipeline
so the numbers visibly change as events arrive.

Usage::

    # Start the API first, then:
    python dashboard/live.py

    # Or with a custom API URL:
    python dashboard/live.py --api http://localhost:8010

    # Faster refresh for demo:
    python dashboard/live.py --interval 2
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import requests
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ── Configuration ────────────────────────────────────────────────────

DEFAULT_API = "http://localhost:8010"
DEFAULT_STORE = "purplle-001"
DEFAULT_INTERVAL = 3


# ── API Fetchers ─────────────────────────────────────────────────────

def fetch_json(url: str, timeout: float = 5.0) -> dict | None:
    """Fetch JSON from a URL, returning None on any error."""
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def fetch_health(api: str) -> dict | None:
    return fetch_json(f"{api}/health")


def fetch_metrics(api: str, store_id: str) -> dict | None:
    return fetch_json(f"{api}/stores/{store_id}/metrics")


def fetch_funnel(api: str, store_id: str) -> dict | None:
    return fetch_json(f"{api}/stores/{store_id}/funnel")


def fetch_active(api: str, store_id: str) -> dict | None:
    return fetch_json(f"{api}/visitors/active?store_id={store_id}")


def fetch_anomalies(api: str, store_id: str) -> dict | None:
    return fetch_json(f"{api}/stores/{store_id}/anomalies?dwell_multiplier=1.5")


# ── Panel Builders ───────────────────────────────────────────────────

def build_header(tick: int, api: str, store_id: str) -> Panel:
    """Title header with timestamp."""
    now = datetime.now().strftime("%H:%M:%S")
    title = Text()
    title.append("  🏪 STORE INTELLIGENCE ", style="bold white on blue")
    title.append(f"  │  {store_id}", style="bold cyan")
    title.append(f"  │  {now}", style="dim")
    title.append(f"  │  tick #{tick}", style="dim")
    return Panel(title, style="blue", height=3)


def build_health_panel(health: dict | None) -> Panel:
    """Service health status."""
    if health is None:
        return Panel(
            Text("  ⚠ API UNREACHABLE", style="bold red"),
            title="[bold]Health[/bold]",
            border_style="red",
            height=6,
        )

    status_icon = "✅" if health.get("status") == "ok" else "⚠️"
    db_icon = "✅" if health.get("database") == "connected" else "❌"

    content = Text()
    content.append(f"  {status_icon} Service: ", style="dim")
    content.append(f"{health.get('status', '?')}\n", style="bold green" if health.get("status") == "ok" else "bold red")
    content.append(f"  {db_icon} Database: ", style="dim")
    content.append(f"{health.get('database', '?')}\n", style="bold green" if health.get("database") == "connected" else "bold red")
    content.append(f"  📊 Total Events: ", style="dim")
    content.append(f"{health.get('total_events', 0)}\n", style="bold white")

    return Panel(content, title="[bold]Health[/bold]", border_style="green", height=6)


def build_metrics_panel(metrics: dict | None) -> Panel:
    """Main store KPIs."""
    if metrics is None:
        return Panel(Text("  Waiting for data...", style="dim"), title="[bold]Store KPIs[/bold]", border_style="yellow", height=14)

    table = Table(show_header=False, expand=True, box=None, padding=(0, 2))
    table.add_column("Metric", style="dim", width=22)
    table.add_column("Value", style="bold white", justify="right")

    table.add_row("👥 Unique Visitors", str(metrics.get("unique_visitors", 0)))
    table.add_row("🛍  Customers", str(metrics.get("unique_customers", 0)))
    table.add_row("👔 Staff", str(metrics.get("staff_count", 0)))
    table.add_row("───────────────────", "──────")
    table.add_row("🚪 Entries", str(metrics.get("entries", 0)))
    table.add_row("🚶 Exits", str(metrics.get("exits", 0)))
    table.add_row("🏠 Inside Now", str(metrics.get("current_inside", 0)))
    table.add_row("───────────────────", "──────")
    conv = metrics.get("conversion_pct")
    conv_str = f"{conv}%" if conv is not None else "—"
    table.add_row("💰 Conversion", conv_str)
    table.add_row("🧾 Queue Depth", str(metrics.get("queue_depth", 0)))

    return Panel(table, title="[bold]Store KPIs[/bold]", border_style="cyan", height=14)


def build_dwell_panel(metrics: dict | None) -> Panel:
    """Average dwell by zone."""
    if metrics is None:
        return Panel(Text("  Waiting...", style="dim"), title="[bold]Avg Dwell[/bold]", border_style="magenta", height=10)

    table = Table(show_header=True, expand=True, box=None, padding=(0, 1))
    table.add_column("Zone", style="cyan")
    table.add_column("Avg Dwell", justify="right", style="bold")
    table.add_column("Bar", width=16)

    zones = metrics.get("avg_dwell_by_zone", [])
    max_dwell = max((z["avg_dwell_ms"] for z in zones), default=1)

    for z in sorted(zones, key=lambda x: x["avg_dwell_ms"], reverse=True):
        ms = z["avg_dwell_ms"]
        secs = ms / 1000
        bar_len = int(ms / max_dwell * 14) if max_dwell > 0 else 0
        bar = "█" * bar_len + "░" * (14 - bar_len)
        color = "green" if secs < 10 else "yellow" if secs < 20 else "red"
        table.add_row(
            z["zone_id"],
            f"{secs:.1f}s",
            Text(bar, style=color),
        )

    return Panel(table, title="[bold]Avg Dwell by Zone[/bold]", border_style="magenta", height=10)


def build_funnel_panel(funnel: dict | None) -> Panel:
    """Conversion funnel."""
    if funnel is None:
        return Panel(Text("  Waiting...", style="dim"), title="[bold]Funnel[/bold]", border_style="green", height=12)

    table = Table(show_header=True, expand=True, box=None, padding=(0, 1))
    table.add_column("Stage", style="cyan")
    table.add_column("Count", justify="right", style="bold white")
    table.add_column("Drop", justify="right")
    table.add_column("Bar", width=14)

    entered = funnel.get("total_entered", 1) or 1

    for stage in funnel.get("stages", []):
        count = stage["count"]
        drop = stage.get("drop_off_pct", 0)
        bar_len = int(count / entered * 12)
        bar = "█" * bar_len + "░" * (12 - bar_len)

        drop_style = "green" if drop < 30 else "yellow" if drop < 60 else "red"
        drop_str = f"-{drop}%" if drop > 0 else "—"

        table.add_row(
            stage["stage"].replace("_", " ").title(),
            str(count),
            Text(drop_str, style=drop_style),
            Text(bar, style="blue"),
        )

    overall = funnel.get("overall_conversion_pct", 0)
    table.add_row("", "", "", "")
    table.add_row(
        "Overall Conv.",
        f"{overall}%",
        "",
        Text("█" * int(overall / 100 * 12) + "░" * (12 - int(overall / 100 * 12)), style="bold green"),
    )

    return Panel(table, title="[bold]Conversion Funnel[/bold]", border_style="green", height=12)


def build_active_panel(active: dict | None) -> Panel:
    """Active visitors by zone."""
    if active is None:
        return Panel(Text("  Waiting...", style="dim"), title="[bold]Active Visitors[/bold]", border_style="yellow", height=10)

    count = active.get("active_count", 0)
    visitors = active.get("visitors", [])

    table = Table(show_header=True, expand=True, box=None, padding=(0, 1))
    table.add_column("Visitor", style="white")
    table.add_column("Zone", style="cyan")
    table.add_column("Role", justify="center")

    for v in visitors[:8]:  # show max 8
        role = Text("👔", style="yellow") if v.get("is_staff") else Text("🛍", style="green")
        table.add_row(v["visitor_id"], v["zone_id"], role)

    if len(visitors) > 8:
        table.add_row(f"... +{len(visitors) - 8} more", "", "")

    header = Text(f"  {count} active", style="bold white")
    return Panel(
        Columns([header, table], expand=True),
        title="[bold]Active in Zones[/bold]",
        border_style="yellow",
        height=10,
    )


def build_anomaly_panel(anomalies: dict | None) -> Panel:
    """Anomaly alerts."""
    if anomalies is None:
        return Panel(Text("  Waiting...", style="dim"), title="[bold]Anomalies[/bold]", border_style="red", height=8)

    items = anomalies.get("anomalies", [])
    if not items:
        content = Text("  ✅ No anomalies detected", style="bold green")
        return Panel(content, title="[bold]Anomalies[/bold]", border_style="green", height=8)

    content = Text()
    severity_icons = {"high": "🔴", "medium": "🟡", "low": "🔵"}
    for a in items[:4]:
        icon = severity_icons.get(a.get("severity", "low"), "⚪")
        content.append(f"  {icon} ", style="bold")
        content.append(f"{a['type']}: ", style="bold white")
        content.append(f"{a.get('description', '')[:60]}\n", style="dim")

    return Panel(content, title=f"[bold]Anomalies ({len(items)})[/bold]", border_style="red", height=8)


def build_event_breakdown(metrics: dict | None) -> Panel:
    """Event type breakdown."""
    if metrics is None:
        return Panel(Text("  Waiting...", style="dim"), title="[bold]Events[/bold]", border_style="blue", height=12)

    breakdown = metrics.get("event_type_breakdown", {})
    total = metrics.get("total_events", 0)

    table = Table(show_header=True, expand=True, box=None, padding=(0, 1))
    table.add_column("Type", style="cyan")
    table.add_column("#", justify="right", style="bold white")
    table.add_column("Bar", width=10)

    max_val = max(breakdown.values(), default=1)

    type_icons = {
        "ENTRY": "🚪", "EXIT": "🚶", "ZONE_ENTER": "📍",
        "ZONE_EXIT": "📤", "ZONE_DWELL": "⏱ ",
        "BILLING_QUEUE_JOIN": "🧾", "BILLING_QUEUE_ABANDON": "❌",
        "REENTRY": "🔄",
    }

    for etype in ["ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                   "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"]:
        count = breakdown.get(etype, 0)
        if count == 0:
            continue
        icon = type_icons.get(etype, "•")
        bar_len = int(count / max_val * 8) if max_val > 0 else 0
        bar = "█" * bar_len + "░" * (8 - bar_len)
        table.add_row(f"{icon} {etype}", str(count), Text(bar, style="blue"))

    table.add_row("", "─────", "")
    table.add_row("TOTAL", str(total), "")

    return Panel(table, title="[bold]Event Breakdown[/bold]", border_style="blue", height=12)


# ── Main Dashboard ───────────────────────────────────────────────────

def build_dashboard(
    tick: int, api: str, store_id: str,
    health, metrics, funnel, active, anomalies,
) -> Layout:
    """Compose the full dashboard layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="upper", size=14),
        Layout(name="lower", size=12),
    )

    layout["header"].update(build_header(tick, api, store_id))

    layout["upper"].split_row(
        Layout(name="health_kpi", ratio=1),
        Layout(name="dwell_events", ratio=1),
    )

    # Stack health + KPIs vertically on the left
    layout["upper"]["health_kpi"].split_column(
        Layout(build_health_panel(health), size=6),
        Layout(build_metrics_panel(metrics)),
    )

    # Stack dwell + events on the right
    layout["upper"]["dwell_events"].split_column(
        Layout(build_dwell_panel(metrics)),
        Layout(build_event_breakdown(metrics)),
    )

    layout["lower"].split_row(
        Layout(build_funnel_panel(funnel), ratio=1),
        Layout(name="right_lower", ratio=1),
    )

    layout["lower"]["right_lower"].split_column(
        Layout(build_active_panel(active)),
        Layout(build_anomaly_panel(anomalies)),
    )

    return layout


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Store Intelligence — Live Dashboard",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL")
    parser.add_argument("--store", default=DEFAULT_STORE, help="Store ID to monitor")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Poll interval in seconds")
    args = parser.parse_args()

    console = Console()
    tick = 0

    console.print(f"\n[bold blue]  🏪 Store Intelligence Dashboard[/bold blue]")
    console.print(f"  Connecting to {args.api} ... (Ctrl+C to quit)\n")

    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                tick += 1

                # Fetch all data in parallel-ish (sequential but fast)
                health = fetch_health(args.api)
                metrics = fetch_metrics(args.api, args.store)
                funnel = fetch_funnel(args.api, args.store)
                active = fetch_active(args.api, args.store)
                anomalies = fetch_anomalies(args.api, args.store)

                dashboard = build_dashboard(
                    tick, args.api, args.store,
                    health, metrics, funnel, active, anomalies,
                )
                live.update(dashboard)

                time.sleep(args.interval)

    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


if __name__ == "__main__":
    main()
