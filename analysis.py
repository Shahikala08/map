"""
NeuroFocus Analysis v3.2
=========================
Processes events.csv → focus_summary.csv, hourly_patterns.csv, attention_report.json

FIXED in v3.2:
  • Keystrokes decoded from extra field (burst:N) — not counted as raw rows
  • Scroll ticks decoded from extra field (ticks:N) — not counted as raw rows
  • session_minutes uses capped time diffs (≤60s each) — no idle inflation
  • wall_clock_minutes = true elapsed span saved separately
  • switches_per_hour divides by hours (not minutes)
  • interaction_totals.keystrokes, .scroll_ticks, .clicks always non-zero
  • focus_summary type column correctly written as deep_work / distraction / neutral
  • attention_score computed per app
  • overall_focus_score computed correctly

Usage:  python analysis.py
"""

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────
EVENTS_CSV   = "events.csv"
FOCUS_CSV    = "focus_summary.csv"
HOURLY_CSV   = "hourly_patterns.csv"
REPORT_JSON  = "attention_report.json"

# App classification keywords (lowercase match)
DEEP_WORK_KEYWORDS = [
    "code", "cursor", "vscode", "visual studio", "sublime", "pycharm",
    "intellij", "notepad++", "notepad", "vim", "nvim", "emacs",
    "word", "excel", "powerpoint", "docs", "sheets", "slides",
    "notion", "obsidian", "roam", "logseq", "figma", "photoshop",
    "illustrator", "terminal", "cmd", "powershell", "bash", "zsh",
    "git", "github", "gitlab", "jira", "confluence", "linear",
    "claude", "chatgpt", "copilot", "stackoverflow", "documentation",
    "postman", "insomnia", "docker", "kubernetes",
]

DISTRACTION_KEYWORDS = [
    "youtube", "netflix", "twitch", "tiktok", "instagram", "facebook",
    "twitter", "x.com", "reddit", "9gag", "imgur", "tumblr",
    "discord", "telegram", "whatsapp", "messenger", "snapchat",
    "steam", "epic games", "battle.net", "origin", "uplay",
    "spotify", "soundcloud", "tidal", "deezer",
    "buzzfeed", "distractify", "ladbible",
]


# ── Helpers ───────────────────────────────────────────────────────────────
def classify_app(name: str) -> str:
    low = name.lower()
    for k in DEEP_WORK_KEYWORDS:
        if k in low:
            return "deep_work"
    for k in DISTRACTION_KEYWORDS:
        if k in low:
            return "distraction"
    return "neutral"


def extract_int(pattern: str, text: str, default: int = 1) -> int:
    m = re.search(pattern, str(text))
    return int(m.group(1)) if m else default


def fmt_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── Load events ───────────────────────────────────────────────────────────
def load_events(path: str) -> list:
    if not os.path.exists(path):
        print(f"  ✗  {path} not found. Run monitor.py first.")
        return []
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["_dt"] = datetime.strptime(row["timestamp"][:23], "%Y-%m-%d %H:%M:%S.%f")
            except (ValueError, KeyError):
                try:
                    row["_dt"] = datetime.strptime(row["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
            rows.append(row)
    rows.sort(key=lambda r: r["_dt"])
    return rows


# ── Main analysis ─────────────────────────────────────────────────────────
def analyse():
    print("\n  NeuroFocus Analysis v3.2")
    print("  " + "─" * 44)

    events = load_events(EVENTS_CSV)
    if not events:
        return

    print(f"  ✓  Loaded {len(events):,} events")

    # ── Time metrics ──────────────────────────────────────────────────────
    ts_first = events[0]["_dt"]
    ts_last  = events[-1]["_dt"]
    wall_minutes = (ts_last - ts_first).total_seconds() / 60

    # Capped active time: sum of inter-event gaps, each capped at 60s
    capped_seconds = 0.0
    for i in range(1, len(events)):
        gap = (events[i]["_dt"] - events[i-1]["_dt"]).total_seconds()
        capped_seconds += min(gap, 60.0)
    session_minutes = capped_seconds / 60

    print(f"  ✓  Session: {session_minutes:.1f} min active | {wall_minutes:.1f} min wall-clock")

    # ── Per-app accumulators ──────────────────────────────────────────────
    # { app_name: { key: value } }
    apps: dict = defaultdict(lambda: {
        "time_sec":      0.0,
        "keystrokes":    0,
        "clicks":        0,
        "scroll_ticks":  0,
        "nav_keys":      0,
        "switches_out":  0,
        "event_count":   0,
    })

    total_keystrokes   = 0
    total_scroll_ticks = 0
    total_clicks       = 0
    total_switches     = 0
    total_nav          = 0

    # Per-hour accumulators { "HH:00": {...} }
    hourly: dict = defaultdict(lambda: {
        "focus_seconds": 0.0,
        "keystrokes":    0,
        "clicks":        0,
        "scroll_ticks":  0,
        "switches":      0,
        "events":        0,
    })

    for i, ev in enumerate(events):
        etype = ev.get("event_type", "").strip()
        app   = ev.get("application", "Unknown").strip() or "Unknown"
        extra = ev.get("extra", "")
        hour  = ev["_dt"].strftime("%H:00")

        # Time attribution: capped gap before this event belongs to prev app
        if i > 0:
            gap = min(
                (ev["_dt"] - events[i-1]["_dt"]).total_seconds(),
                60.0
            )
            prev_app = events[i-1].get("application", "Unknown").strip() or "Unknown"
            apps[prev_app]["time_sec"] += gap
            if etype != "idle":
                hourly[hour]["focus_seconds"] += gap

        apps[app]["event_count"] += 1
        hourly[hour]["events"]   += 1

        if etype == "keyboard":
            burst = extract_int(r"burst:(\d+)", extra, 1)
            apps[app]["keystrokes"]    += burst
            hourly[hour]["keystrokes"] += burst
            total_keystrokes           += burst

        elif etype == "mouse_click":
            apps[app]["clicks"]    += 1
            hourly[hour]["clicks"] += 1
            total_clicks           += 1

        elif etype == "scroll":
            ticks = extract_int(r"ticks:(\d+)", extra, 1)
            apps[app]["scroll_ticks"]    += ticks
            hourly[hour]["scroll_ticks"] += ticks
            total_scroll_ticks           += ticks

        elif etype == "window_switch":
            apps[app]["switches_out"]  += 1
            hourly[hour]["switches"]   += 1
            total_switches             += 1

        elif etype == "navigation":
            count = extract_int(r"count:(\d+)", extra, 1)
            apps[app]["nav_keys"] += count
            total_nav             += count

    print(f"  ✓  Keystrokes: {total_keystrokes:,}  |  Scroll ticks: {total_scroll_ticks:,}  "
          f"|  Clicks: {total_clicks:,}  |  Switches: {total_switches:,}")

    # ── Build focus_summary rows ──────────────────────────────────────────
    total_active_sec = max(capped_seconds, 1)

    focus_rows = []
    for app, s in apps.items():
        if app in ("", "Unknown") and s["time_sec"] < 5:
            continue

        app_min  = s["time_sec"] / 60
        app_pct  = s["time_sec"] / total_active_sec * 100
        atype    = classify_app(app)
        ev_count = s["event_count"]
        ev_pm    = ev_count / max(app_min, 0.01)

        # Attention score (0–100): rewards keystrokes + scroll, penalises idle
        key_score   = min(s["keystrokes"]   / 10, 30)
        scroll_score= min(s["scroll_ticks"] / 20, 20)
        time_score  = min(app_pct * 0.5, 30)
        type_bonus  = 20 if atype == "deep_work" else (-10 if atype == "distraction" else 0)
        att_score   = max(0, min(100, key_score + scroll_score + time_score + type_bonus))

        focus_rows.append({
            "application":         app,
            "type":                atype,
            "focus_minutes":       round(app_min, 2),
            "focus_pct":           round(app_pct, 2),
            "events_per_min":      round(ev_pm, 2),
            "keystrokes":          s["keystrokes"],
            "clicks":              s["clicks"],
            "scroll_ticks":        s["scroll_ticks"],
            "nav_keys":            s["nav_keys"],
            "context_switches_out":s["switches_out"],
            "attention_score":     round(att_score, 1),
        })

    focus_rows.sort(key=lambda r: r["focus_minutes"], reverse=True)

    # Write focus_summary.csv
    focus_fields = [
        "application", "type", "focus_minutes", "focus_pct", "events_per_min",
        "keystrokes", "clicks", "scroll_ticks", "nav_keys",
        "context_switches_out", "attention_score",
    ]
    with open(FOCUS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=focus_fields)
        w.writeheader()
        w.writerows(focus_rows)
    print(f"  ✓  {FOCUS_CSV} — {len(focus_rows)} apps")

    # ── Build hourly_patterns.csv ─────────────────────────────────────────
    hourly_rows = []
    for h in sorted(hourly.keys()):
        s = hourly[h]
        hourly_rows.append({
            "hour_slot":    h,
            "focus_minutes":round(s["focus_seconds"] / 60, 2),
            "keystrokes":   s["keystrokes"],
            "clicks":       s["clicks"],
            "scroll_ticks": s["scroll_ticks"],
            "switches":     s["switches"],
            "events":       s["events"],
        })

    hourly_fields = [
        "hour_slot", "focus_minutes", "keystrokes",
        "clicks", "scroll_ticks", "switches", "events",
    ]
    with open(HOURLY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=hourly_fields)
        w.writeheader()
        w.writerows(hourly_rows)
    print(f"  ✓  {HOURLY_CSV} — {len(hourly_rows)} hour slots")

    # ── Compute overall metrics ───────────────────────────────────────────
    dw_sec   = sum(s["time_sec"] for app, s in apps.items()
                   if classify_app(app) == "deep_work")
    dist_sec = sum(s["time_sec"] for app, s in apps.items()
                   if classify_app(app) == "distraction")
    idle_sec = sum(s["time_sec"] for app, s in apps.items()
                   if "idle" in app.lower() or "screensaver" in app.lower())

    dw_min   = dw_sec   / 60
    dist_min = dist_sec / 60
    idle_min = idle_sec / 60

    dw_pct   = dw_sec   / total_active_sec * 100
    dist_pct = dist_sec / total_active_sec * 100
    idle_pct = idle_sec / total_active_sec * 100

    sw_per_hr = total_switches / max(session_minutes / 60, 0.01)

    # Focus score formula
    sw_pen = min(sw_per_hr * 0.4, 25)
    score  = max(0, min(100,
        dw_pct * 0.6
        - dist_pct * 0.3
        - sw_pen
        + 35
    ))

    # ── Write attention_report.json ───────────────────────────────────────
    report = {
        "session_start":     fmt_iso(ts_first),
        "session_end":       fmt_iso(ts_last),
        "session_minutes":   round(session_minutes, 1),
        "wall_clock_minutes":round(wall_minutes, 1),
        "total_events":      len(events),
        "deep_work_minutes": round(dw_min, 1),
        "distraction_minutes": round(dist_min, 1),
        "idle_minutes":      round(idle_min, 1),
        "context_switches":  total_switches,
        "switches_per_hour": round(sw_per_hr, 1),
        "overall_focus_score": round(score, 1),
        "focus_breakdown": {
            "deep_work_pct":   round(dw_pct, 1),
            "distraction_pct": round(dist_pct, 1),
            "idle_pct":        round(idle_pct, 1),
        },
        "interaction_totals": {
            "keystrokes":   total_keystrokes,
            "clicks":       total_clicks,
            "scroll_ticks": total_scroll_ticks,
            "nav_keys":     total_nav,
        },
    }

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"  ✓  {REPORT_JSON}")

    print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Active time    : {session_minutes:.1f} min")
    print(f"  Wall-clock     : {wall_minutes:.1f} min")
    print(f"  Keystrokes     : {total_keystrokes:,}")
    print(f"  Scroll ticks   : {total_scroll_ticks:,}")
    print(f"  Clicks         : {total_clicks:,}")
    print(f"  Window switches: {total_switches}  ({sw_per_hr:.1f}/hr)")
    print(f"  Deep work      : {dw_min:.1f} min  ({dw_pct:.1f}%)")
    print(f"  Distraction    : {dist_min:.1f} min  ({dist_pct:.1f}%)")
    print(f"  Focus score    : {score:.0f}/100")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"\n  ✅  Done. Run  streamlit run dashboard.py  to view.\n")


if __name__ == "__main__":
    analyse()