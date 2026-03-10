"""
dashboard.py

Local web dashboard on port 3010.
Shows scheduler status, next run times, and live log tail.

Usage:
    python3 dashboard.py
Then open: http://localhost:3010
"""

import subprocess
import os
import json
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytz

# Load config values directly (avoid importing full config which calls load_dotenv)
from dotenv import dotenv_values

_ENV = dotenv_values(Path(__file__).parent / ".env")
SCHEDULE_TIMES = _ENV.get("SCHEDULE_TIMES", "06:00,18:00")
TIMEZONE       = _ENV.get("TIMEZONE", "Asia/Jerusalem")
LOG_FILE       = Path(__file__).parent / "logs" / "agent.log"
ACTIVITY_FILE  = Path(__file__).parent / "logs" / "activity.json"
SOURCE_FILE    = Path(__file__).parent / "logs" / "source_status.json"
PORT           = 3010


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PID_FILE = Path(__file__).parent / "logs" / "agent.pid"


def is_scheduler_running() -> tuple[bool, str]:
    """Check if main.py is running via PID file written by agent.sh. Returns (running, pid)."""
    try:
        if PID_FILE.exists():
            pid = PID_FILE.read_text().strip()
            if pid and pid.isdigit():
                import os, signal
                try:
                    os.kill(int(pid), 0)   # signal 0 = existence check only
                    return True, pid
                except (ProcessLookupError, PermissionError):
                    PID_FILE.unlink(missing_ok=True)  # stale PID file
        return False, ""
    except Exception:
        return False, ""


def is_caffeinate_running() -> bool:
    """Check if caffeinate is active (preventing sleep)."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "caffeinate"],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def get_next_runs() -> list[dict]:
    """Calculate next two scheduled run times in local timezone."""
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        runs = []
        for entry in SCHEDULE_TIMES.split(","):
            h, m = map(int, entry.strip().split(":"))
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= now:
                # Already passed today — move to tomorrow
                from datetime import timedelta
                candidate = candidate + timedelta(days=1)
            delta = candidate - now
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            runs.append({
                "time": entry.strip(),
                "datetime": candidate.strftime("%a %b %d at %H:%M"),
                "in": f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m",
            })
        return sorted(runs, key=lambda r: r["datetime"])
    except Exception as e:
        return [{"time": "?", "datetime": str(e), "in": "?"}]


def get_log_lines(n: int = 80) -> list[str]:
    """Return last N lines from the log file."""
    if not LOG_FILE.exists():
        return ["Log file not found yet."]
    try:
        result = subprocess.run(
            ["tail", "-n", str(n), str(LOG_FILE)],
            capture_output=True, text=True
        )
        return result.stdout.splitlines()[::-1]   # newest first (desc)
    except Exception as e:
        return [f"Error reading log: {e}"]


def get_source_status() -> dict:
    """Return source status from logs/source_status.json."""
    if not SOURCE_FILE.exists():
        return {}
    try:
        return json.loads(SOURCE_FILE.read_text())
    except Exception:
        return {}


def get_activity() -> list[dict]:
    """Return recent activity events from logs/activity.json."""
    if not ACTIVITY_FILE.exists():
        return []
    try:
        return json.loads(ACTIVITY_FILE.read_text())[:15]
    except Exception:
        return []


def get_last_digest_time() -> str:
    """Find the last successful digest delivery in the logs."""
    if not LOG_FILE.exists():
        return "Never"
    try:
        result = subprocess.run(
            ["grep", "Digest delivered successfully", str(LOG_FILE)],
            capture_output=True, text=True
        )
        lines = result.stdout.strip().splitlines()
        if lines:
            # Extract timestamp from last match
            last = lines[-1]
            ts = last.split(" [")[0]
            return ts
        return "Not yet"
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

def _activity_row_html(ev: dict) -> str:
    """Render one activity event as an HTML row."""
    ts      = ev.get("ts", "")
    kind    = ev.get("type", "")
    trigger = ev.get("trigger", "")
    command = ev.get("command", "")
    status  = ev.get("status", "")

    if kind == "digest":
        scraped  = ev.get("items_scraped", 0)
        new      = ev.get("items_new", 0)
        icon     = "📰"
        trig_cls = "runnow" if trigger == "/runnow" else "scheduler"
        trig_lbl = trigger if trigger else "scheduler"
        details  = f"{scraped} scraped &nbsp;·&nbsp; <strong>{new} new</strong>"
        status_map = {
            "sent":            ("sent",     "✅ sent"),
            "all_seen":        ("all_seen", "⏭ all seen"),
            "no_items":        ("no_items", "⚠ no items"),
            "fetch_error":     ("error",    "❌ fetch error"),
            "summarize_error": ("error",    "❌ summarize error"),
            "send_error":      ("error",    "❌ send error"),
        }
        s_cls, s_lbl = status_map.get(status, ("", status))
        status_html = f'<span class="act-status {s_cls}">{s_lbl}</span>' if s_cls else ""
        return (
            f'<div class="activity-row digest">'
            f'<span class="act-ts">{ts}</span>'
            f'<span class="act-icon">{icon}</span>'
            f'<span class="act-trigger {trig_cls}">{trig_lbl}</span>'
            f'<span class="act-details">{details}</span>'
            f'{status_html}'
            f'</div>'
        )
    else:
        # command event (/links, /help, etc.)
        icon = "💬"
        return (
            f'<div class="activity-row command">'
            f'<span class="act-ts">{ts}</span>'
            f'<span class="act-icon">{icon}</span>'
            f'<span class="act-trigger command">{command}</span>'
            f'<span class="act-details" style="color:#64748b">command used</span>'
            f'</div>'
        )


def build_html() -> str:
    running, pids = is_scheduler_running()
    caffeinate    = is_caffeinate_running()
    next_runs     = get_next_runs()
    log_lines     = get_log_lines(80)
    last_digest   = get_last_digest_time()
    activity      = get_activity()
    src_data      = get_source_status()
    now_str       = datetime.now().strftime("%H:%M:%S")

    status_color  = "#22c55e" if running else "#ef4444"
    status_text   = f"Running (PID {pids})" if running else "Stopped"
    caff_color    = "#22c55e" if caffeinate else "#f59e0b"
    caff_text     = "Active (sleep prevented)" if caffeinate else "Not active (Mac may sleep)"

    next_runs_html = "".join(
        f"""<div class="run-card">
              <span class="run-time">{r['time']}</span>
              <span class="run-date">{r['datetime']}</span>
              <span class="run-in">in {r['in']}</span>
           </div>"""
        for r in next_runs
    )

    # Color-code log lines
    def color_line(line: str) -> str:
        if "[ERROR]" in line or "[CRITICAL]" in line:
            return f'<span class="log-error">{line}</span>'
        elif "[WARNING]" in line:
            return f'<span class="log-warn">{line}</span>'
        elif "Digest delivered" in line or "complete" in line.lower():
            return f'<span class="log-success">{line}</span>'
        elif "[INFO]" in line:
            return f'<span class="log-info">{line}</span>'
        return f'<span class="log-dim">{line}</span>'

    log_html = "\n".join(color_line(l) for l in log_lines)

    if activity:
        activity_html = "\n".join(_activity_row_html(ev) for ev in activity)
    else:
        activity_html = '<div style="padding:16px 20px;color:#475569;font-size:0.8rem">No activity yet — run a digest first.</div>'

    # Sources panel
    sources = src_data.get("sources", [])
    src_updated = src_data.get("updated", "")
    type_icons = {"rss": "📡", "reddit": "🟠", "hn": "🔶"}
    if sources:
        ok_count  = sum(1 for s in sources if s["status"] == "ok")
        err_count = sum(1 for s in sources if s["status"] == "error")
        dis_count = sum(1 for s in sources if s["status"] == "disabled")
        src_summary = f"{ok_count} active · {err_count} errors · {dis_count} disabled · last run {src_updated}"
        rows = []
        for s in sources:
            st = s["status"]
            cnt = s.get("count", 0)
            icon = type_icons.get(s.get("type", "rss"), "📡")
            badge_cls = st if st in ("ok", "error", "disabled") else "ok"
            if st == "ok" and cnt == 0:
                badge_cls = "empty"
                badge_lbl = "no items"
            elif st == "ok":
                badge_cls = "ok"
                badge_lbl = f"{cnt} items"
            elif st == "disabled":
                badge_lbl = "off"
            else:
                badge_lbl = "error"
            rows.append(
                f'<div class="src-row" title="{s.get("error") or s["url"]}">'
                f'<span class="src-icon">{icon}</span>'
                f'<span class="src-name">{s["name"]}</span>'
                f'<span class="src-badge {badge_cls}">{badge_lbl}</span>'
                f'</div>'
            )
        sources_inner = f'<div class="sources-grid">{"".join(rows)}</div>'
    else:
        src_summary = "no data yet"
        sources_inner = '<div style="padding:16px 20px;color:#475569;font-size:0.8rem">Run a digest to see source status.</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI News Agent Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 24px;
    }}
    h1 {{ font-size: 1.5rem; font-weight: 700; color: #f8fafc; margin-bottom: 4px; }}
    .subtitle {{ font-size: 0.85rem; color: #64748b; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{
      background: #1e293b; border-radius: 12px; padding: 20px;
      border: 1px solid #334155;
    }}
    .card-label {{ font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }}
    .card-value {{ font-size: 1rem; font-weight: 600; }}
    .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }}
    .dot.pulse {{ animation: pulse 2s infinite; }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }}
    }}
    .run-card {{
      display: flex; align-items: center; gap: 12px;
      background: #0f172a; border-radius: 8px; padding: 10px 14px; margin-top: 8px;
    }}
    .run-time {{ font-weight: 700; font-size: 1rem; color: #7dd3fc; min-width: 52px; }}
    .run-date {{ font-size: 0.85rem; color: #94a3b8; flex: 1; }}
    .run-in {{
      font-size: 0.75rem; background: #1d4ed8; color: #bfdbfe;
      padding: 2px 8px; border-radius: 99px;
    }}
    .log-section {{ background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; }}
    .log-header {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 14px 20px; border-bottom: 1px solid #334155;
    }}
    .log-header h2 {{ font-size: 0.95rem; font-weight: 600; color: #f1f5f9; }}
    .log-header span {{ font-size: 0.75rem; color: #64748b; }}
    pre {{
      font-family: "SF Mono", "Fira Code", monospace; font-size: 0.75rem;
      line-height: 1.6; padding: 16px 20px; overflow-x: auto;
      max-height: 520px; overflow-y: auto; white-space: pre-wrap; word-break: break-all;
    }}
    .log-error  {{ color: #f87171; }}
    .log-warn   {{ color: #fbbf24; }}
    .log-success {{ color: #4ade80; }}
    .log-info   {{ color: #cbd5e1; }}
    .log-dim    {{ color: #64748b; }}
    .refresh-bar {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 20px; background: #0f172a; font-size: 0.75rem; color: #475569;
    }}
    .badge {{
      display: inline-block; padding: 2px 10px; border-radius: 99px;
      font-size: 0.72rem; font-weight: 600;
    }}
    .activity-section {{
      background: #1e293b; border-radius: 12px; border: 1px solid #334155;
      overflow: hidden; margin-bottom: 24px;
    }}
    .activity-header {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 14px 20px; border-bottom: 1px solid #334155;
    }}
    .activity-header h2 {{ font-size: 0.95rem; font-weight: 600; color: #f1f5f9; }}
    .activity-header span {{ font-size: 0.75rem; color: #64748b; }}
    .activity-list {{ padding: 8px 0; }}
    .activity-row {{
      display: flex; align-items: center; gap: 12px;
      padding: 8px 20px; border-bottom: 1px solid #1e293b; font-size: 0.8rem;
    }}
    .activity-row:last-child {{ border-bottom: none; }}
    .activity-row.digest {{ background: #0f172a; }}
    .activity-row.command {{ background: #1e293b; }}
    .act-ts {{ color: #475569; min-width: 120px; font-family: "SF Mono", monospace; font-size: 0.72rem; }}
    .act-icon {{ font-size: 1rem; min-width: 22px; }}
    .act-trigger {{
      font-weight: 600; color: #7dd3fc; min-width: 90px;
    }}
    .act-trigger.scheduler {{ color: #a78bfa; }}
    .act-trigger.runnow {{ color: #7dd3fc; }}
    .act-trigger.command {{ color: #94a3b8; }}
    .act-details {{ color: #94a3b8; flex: 1; }}
    .act-status {{ font-size: 0.72rem; padding: 2px 8px; border-radius: 99px; font-weight: 600; }}
    .act-status.sent {{ background: #14532d; color: #4ade80; }}
    .act-status.error {{ background: #450a0a; color: #f87171; }}
    .act-status.all_seen {{ background: #1c1917; color: #78716c; }}
    .act-status.no_items {{ background: #1c1917; color: #78716c; }}
    .sources-section {{
      background: #1e293b; border-radius: 12px; border: 1px solid #334155;
      overflow: hidden; margin-bottom: 24px;
    }}
    .sources-header {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 14px 20px; border-bottom: 1px solid #334155; cursor: pointer;
    }}
    .sources-header h2 {{ font-size: 0.95rem; font-weight: 600; color: #f1f5f9; }}
    .sources-header span {{ font-size: 0.75rem; color: #64748b; }}
    .sources-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1px; background: #334155;
    }}
    .src-row {{
      display: flex; align-items: center; gap: 10px;
      padding: 7px 16px; background: #1e293b; font-size: 0.78rem;
    }}
    .src-icon {{ font-size: 0.85rem; min-width: 18px; }}
    .src-name {{ flex: 1; color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .src-count {{ font-size: 0.72rem; color: #64748b; min-width: 28px; text-align: right; }}
    .src-badge {{
      font-size: 0.65rem; padding: 1px 6px; border-radius: 99px; font-weight: 700; min-width: 50px; text-align: center;
    }}
    .src-badge.ok       {{ background: #14532d; color: #4ade80; }}
    .src-badge.error    {{ background: #450a0a; color: #f87171; }}
    .src-badge.disabled {{ background: #1c1917; color: #78716c; }}
    .src-badge.empty    {{ background: #1e3a5f; color: #7dd3fc; }}
  </style>
</head>
<body>
  <h1>🤖 AI News Agent</h1>
  <p class="subtitle">Dashboard · refreshes every 30s · last updated {now_str}</p>

  <div class="grid">
    <div class="card">
      <div class="card-label">Scheduler</div>
      <div class="card-value">
        <span class="dot {'pulse' if running else ''}" style="background:{status_color}"></span>
        {status_text}
      </div>
    </div>

    <div class="card">
      <div class="card-label">Sleep Prevention (caffeinate)</div>
      <div class="card-value">
        <span class="dot" style="background:{caff_color}"></span>
        {caff_text}
      </div>
    </div>

    <div class="card">
      <div class="card-label">Last Digest Sent</div>
      <div class="card-value" style="color:#7dd3fc">{last_digest}</div>
    </div>

    <div class="card">
      <div class="card-label">Next Scheduled Runs ({TIMEZONE})</div>
      {next_runs_html}
    </div>
  </div>

  <div class="sources-section">
    <div class="sources-header">
      <h2>📡 Sources</h2>
      <span>{src_summary} &nbsp;·&nbsp; <a href="/sources" style="color:#7dd3fc;text-decoration:none">⚙️ Manage sources</a></span>
    </div>
    {sources_inner}
  </div>

  <div class="activity-section">
    <div class="activity-header">
      <h2>📊 Activity</h2>
      <span>last 15 events · digest runs &amp; commands</span>
    </div>
    <div class="activity-list">
      {activity_html}
    </div>
  </div>

  <div class="log-section">
    <div class="log-header">
      <h2>📋 Live Log</h2>
      <span>last 80 lines · {LOG_FILE}</span>
    </div>
    <pre id="log">{log_html}</pre>
    <div class="refresh-bar">
      <span>Auto-refresh in <span id="countdown">30</span>s</span>
      <span><a href="/" style="color:#7dd3fc;text-decoration:none">↻ Refresh now</a></span>
    </div>
  </div>

  <script>
    // Countdown + auto-refresh
    let t = 30;
    const cd = document.getElementById('countdown');
    setInterval(() => {{
      t--;
      cd.textContent = t;
      if (t <= 0) location.reload();
    }}, 1000);
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Sources management page + POST handler
# ---------------------------------------------------------------------------

SOURCES_FILE = Path(__file__).parent / "sources.json"
CATEGORY_ORDER = ["AI Labs", "Cloud AI", "Newsletter", "Tech Media", "Research", "AI Safety", "Reddit", "Community"]


def _load_sources_file() -> list[dict]:
    if SOURCES_FILE.exists():
        try:
            return json.loads(SOURCES_FILE.read_text())
        except Exception:
            pass
    return []


def _save_sources_file(sources: list[dict]) -> None:
    SOURCES_FILE.write_text(json.dumps(sources, indent=2))


def build_sources_html(saved: bool = False) -> str:
    sources = _load_sources_file()
    if not sources:
        return "<p>sources.json not found.</p>"

    # Group by category, preserving CATEGORY_ORDER
    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in sources:
        groups[s.get("category", "Other")].append(s)

    type_labels = {"rss": "RSS", "reddit": "Reddit", "hn": "HN"}
    type_colors = {"rss": "#1d4ed8", "reddit": "#c2410c", "hn": "#b45309"}

    sections = ""
    ordered_cats = [c for c in CATEGORY_ORDER if c in groups] + \
                   [c for c in groups if c not in CATEGORY_ORDER]

    for cat in ordered_cats:
        rows = ""
        for s in groups[cat]:
            checked   = "checked" if s.get("enabled", True) else ""
            new_badge = '<span class="new-badge">NEW</span>' if s.get("new") else ""
            t         = s.get("type", "rss")
            t_color   = type_colors.get(t, "#334155")
            t_label   = type_labels.get(t, t.upper())
            short_url = s["url"].replace("https://", "").replace("http://", "").split("?")[0][:55]
            note = s.get("note", "")
            note_html = f'<span class="src-toggle-note">⚠️ {note}</span>' if note and not s.get("enabled", True) else ""
            rows += f"""
            <label class="src-toggle {'disabled-row' if not s.get('enabled', True) else ''}">
              <input type="checkbox" name="enabled" value="{s['name']}" {checked}>
              <div class="src-toggle-body">
                <span class="src-toggle-name">{s['name']} {new_badge}</span>
                <span class="src-toggle-url">{short_url}</span>
                {note_html}
              </div>
              <span class="src-type-badge" style="background:{t_color}20;color:{t_color};border:1px solid {t_color}40">{t_label}</span>
            </label>"""
        sections += f"""
        <div class="cat-section">
          <div class="cat-title">{cat}</div>
          <div class="cat-rows">{rows}</div>
        </div>"""

    total   = len(sources)
    enabled = sum(1 for s in sources if s.get("enabled", True))
    saved_banner = '<div class="saved-banner">✅ Sources saved — takes effect on next digest run</div>' if saved else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Manage Sources · AI News Agent</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 24px;
    }}
    .topbar {{ display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }}
    .topbar h1 {{ font-size: 1.4rem; font-weight: 700; color: #f8fafc; flex: 1; }}
    .back-link {{ color: #7dd3fc; text-decoration: none; font-size: 0.85rem; }}
    .summary {{ font-size: 0.82rem; color: #64748b; }}
    .saved-banner {{
      background: #14532d; color: #4ade80; border-radius: 8px;
      padding: 12px 20px; margin-bottom: 20px; font-weight: 600;
    }}
    .cat-section {{ margin-bottom: 20px; }}
    .cat-title {{
      font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;
      color: #64748b; padding: 6px 0; border-bottom: 1px solid #1e293b; margin-bottom: 2px;
    }}
    .cat-rows {{ display: flex; flex-direction: column; gap: 1px; }}
    .src-toggle {{
      display: flex; align-items: center; gap: 12px;
      background: #1e293b; padding: 10px 16px; cursor: pointer;
      border-left: 3px solid transparent; transition: border-color 0.15s;
    }}
    .src-toggle:hover {{ background: #243347; border-left-color: #7dd3fc; }}
    .src-toggle input[type=checkbox] {{ accent-color: #7dd3fc; width: 16px; height: 16px; cursor: pointer; flex-shrink: 0; }}
    .src-toggle-body {{ flex: 1; min-width: 0; }}
    .src-toggle-name {{ font-size: 0.85rem; font-weight: 600; color: #e2e8f0; display: flex; align-items: center; gap: 6px; }}
    .src-toggle-url {{ font-size: 0.72rem; color: #475569; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .src-type-badge {{ font-size: 0.65rem; padding: 2px 7px; border-radius: 4px; font-weight: 700; white-space: nowrap; }}
    .new-badge {{
      font-size: 0.6rem; background: #7c3aed; color: #ede9fe;
      padding: 1px 5px; border-radius: 3px; font-weight: 700;
    }}
    .disabled-row .src-toggle-name {{ color: #475569; }}
    .disabled-row .src-toggle-url  {{ color: #334155; }}
    .src-toggle-note {{ font-size: 0.7rem; color: #92400e; margin-top: 2px; }}
    .submit-bar {{
      position: sticky; bottom: 0; background: #0f172a; border-top: 1px solid #1e293b;
      padding: 16px 0; margin-top: 24px; display: flex; gap: 12px; align-items: center;
    }}
    .btn-save {{
      background: #2563eb; color: #fff; border: none; border-radius: 8px;
      padding: 10px 28px; font-size: 0.9rem; font-weight: 600; cursor: pointer;
    }}
    .btn-save:hover {{ background: #1d4ed8; }}
    .btn-all, .btn-none {{
      background: transparent; border: 1px solid #334155; color: #94a3b8;
      border-radius: 6px; padding: 6px 14px; font-size: 0.78rem; cursor: pointer;
    }}
    .btn-all:hover, .btn-none:hover {{ border-color: #7dd3fc; color: #7dd3fc; }}
  </style>
</head>
<body>
  <div class="topbar">
    <h1>📡 Manage Sources</h1>
    <a class="back-link" href="/">← Back to Dashboard</a>
  </div>
  <p class="summary" style="margin-bottom:16px">{enabled} of {total} sources enabled · check/uncheck and click Save</p>
  {saved_banner}

  <form method="POST" action="/sources" id="srcForm">
    {sections}
    <div class="submit-bar">
      <button type="submit" class="btn-save">💾 Save Changes</button>
      <button type="button" class="btn-all" onclick="document.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=true)">Enable all</button>
      <button type="button" class="btn-none" onclick="document.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=false)">Disable all</button>
    </div>
  </form>

  <script>
    // Dim/undim row when toggled
    document.querySelectorAll('.src-toggle input').forEach(cb => {{
      cb.addEventListener('change', () => {{
        cb.closest('.src-toggle').classList.toggle('disabled-row', !cb.checked);
      }});
    }});
  </script>
</body>
</html>"""


def handle_sources_post(body: bytes) -> None:
    """Parse form POST and update sources.json enabled flags."""
    import urllib.parse
    params = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
    enabled_names = set(params.get("enabled", []))
    sources = _load_sources_file()
    for s in sources:
        s["enabled"] = s["name"] in enabled_names
    _save_sources_file(sources)


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return

        if self.path in ("/sources", "/sources?saved=1"):
            saved = "saved=1" in self.path
            html = build_sources_html(saved=saved).encode("utf-8")
        elif self.path == "/":
            html = build_html().encode("utf-8")
        else:
            self.send_response(404); self.end_headers(); return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        if self.path != "/sources":
            self.send_response(405); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        handle_sources_post(body)
        # Redirect back to /sources with saved flag
        self.send_response(303)
        self.send_header("Location", "/sources?saved=1")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default request logging


if __name__ == "__main__":
    server = HTTPServer(("localhost", PORT), DashboardHandler)
    print(f"Dashboard running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
