"""
NeuroFocus Report Generator v2.0
=================================
Generates downloadable HTML and PDF attention reports.
100% offline — no cloud dependencies.

Usage:
    python report_generator.py              # generates both HTML + PDF attempt
    python report_generator.py --html-only  # HTML only (no wkhtmltopdf needed)

FIXED in v2.0:
  • generate_pdf() now correctly accepts (html_path, pdf_path) — was missing pdf_path arg
  • build_html_report() is the single source of truth — dashboard.py imports it directly
    so the downloaded report and the dashboard report are always identical
  • fmt_min() handles None/zero safely
  • Charts embedded as interactive Plotly (CDN on first, reuse after)
  • bargap=0.4 on all horizontal bar charts to prevent giant single-bar rendering
"""

import os
import re
import sys
import json
import argparse
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from datetime import datetime


# ── Config ─────────────────────────────────────────────────────────────────
FOCUS_CSV   = "focus_summary.csv"
HOURLY_CSV  = "hourly_patterns.csv"
REPORT_JSON = "attention_report.json"
EVENTS_CSV  = "events.csv"

OUTPUT_DIR  = "reports"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────
def fmt_min(m) -> str:
    try:
        m = float(m or 0)
    except (TypeError, ValueError):
        return "0m"
    h = int(m // 60)
    mn = int(m % 60)
    return f"{h}h {mn}m" if h else f"{mn}m"


def grade(s) -> tuple:
    try:
        s = float(s)
    except Exception:
        s = 0
    if s >= 75: return "#16a34a", "Excellent"
    if s >= 55: return "#2563eb", "Good"
    if s >= 35: return "#d97706", "Fair"
    return "#dc2626", "Needs Work"


def clean_app_name(name: str) -> str:
    for suffix in [
        " - Google Chrome", " - Microsoft Edge", " - Mozilla Firefox",
        " - Opera", " - Brave", " - Safari",
        " - Visual Studio Code", " - Sublime Text", " - PyCharm",
        " - IntelliJ IDEA", " - Notepad++",
    ]:
        if suffix in name:
            name = name[:name.index(suffix)]
    name = re.sub(r"^\(\d+\)\s*", "", name)
    return name.strip(" -·") or name


def extract_ticks(extra_val) -> int:
    try:
        m = re.search(r"ticks:(\d+)", str(extra_val))
        return int(m.group(1)) if m else 1
    except Exception:
        return 1


def extract_burst(extra_val) -> int:
    try:
        m = re.search(r"burst:(\d+)", str(extra_val))
        return int(m.group(1)) if m else 1
    except Exception:
        return 1


def extract_destination(extra_val) -> str:
    try:
        s = str(extra_val).strip()
        if "→" in s:
            return s.split("→", 1)[1].strip()
        return s.lstrip(">").strip()
    except Exception:
        return str(extra_val)


def bar_chart_height(n: int, px_per_bar: int = 52, min_h: int = 240, max_h: int = 900) -> int:
    """Compute sensible chart height — prevents giant bars when n=1."""
    return max(min_h, min(n * px_per_bar + 120, max_h))


# ── Data Loading ───────────────────────────────────────────────────────────
def load_data() -> dict:
    d = {}
    if os.path.exists(REPORT_JSON):
        with open(REPORT_JSON) as f:
            d["report"] = json.load(f)
    if os.path.exists(FOCUS_CSV):
        d["focus"] = pd.read_csv(FOCUS_CSV)
    if os.path.exists(HOURLY_CSV):
        d["hourly"] = pd.read_csv(HOURLY_CSV)
    if os.path.exists(EVENTS_CSV):
        ev = pd.read_csv(EVENTS_CSV, dtype=str, nrows=80_000)
        ev["timestamp"]   = pd.to_datetime(ev["timestamp"], errors="coerce")
        ev = ev.dropna(subset=["timestamp"])
        ev["extra"]       = ev["extra"].fillna("")
        ev["event_type"]  = ev["event_type"].fillna("unknown")
        ev["application"] = ev["application"].fillna("Unknown")

        sw = ev["event_type"] == "scroll"
        ev.loc[sw, "scroll_ticks"] = ev.loc[sw, "extra"].apply(extract_ticks)
        ev["scroll_ticks"] = pd.to_numeric(ev.get("scroll_ticks", 0), errors="coerce").fillna(0).astype(int)

        kb = ev["event_type"] == "keyboard"
        ev.loc[kb, "key_burst"] = ev.loc[kb, "extra"].apply(extract_burst)
        ev["key_burst"] = pd.to_numeric(ev.get("key_burst", 0), errors="coerce").fillna(0).astype(int)

        wm = ev["event_type"] == "window_switch"
        ev.loc[wm, "switch_dest"] = ev.loc[wm, "extra"].apply(extract_destination)
        ev["switch_dest"] = ev["switch_dest"].fillna("")

        # nav_count guard for older files
        def extract_nav_count(v):
            try:
                m = re.search(r"count:(\d+)", str(v))
                return int(m.group(1)) if m else 1
            except Exception:
                return 1

        if "nav_count" not in ev.columns:
            ev["nav_count"] = 0
        nav = ev["event_type"] == "navigation"
        if nav.any():
            ev.loc[nav, "nav_count"] = ev.loc[nav, "extra"].apply(extract_nav_count)
        ev["nav_count"] = pd.to_numeric(ev["nav_count"], errors="coerce").fillna(0).astype(int)

        d["events"] = ev
    return d


# ── Chart Builders ─────────────────────────────────────────────────────────
_LIGHT_AX = dict(gridcolor="#e8edf5", linecolor="#d0d8e8", zeroline=False,
                 tickfont=dict(size=12, color="#6b7a99"),
                 title_font=dict(size=13, color="#344563"))
_LIGHT_BASE = dict(
    paper_bgcolor="white", plot_bgcolor="#f8f9fa",
    font=dict(family="'Segoe UI',system-ui,sans-serif", color="#344563", size=12),
    hoverlabel=dict(bgcolor="#1a2035", bordercolor="rgba(79,156,249,.4)",
                    font=dict(size=13, color="#e8edf5")),
    margin=dict(l=50, r=30, t=50, b=50),
)


def _lt(fig, h=300, **kw):
    fig.update_layout(**{**_LIGHT_BASE, "height": h, **kw})
    return fig


def build_activity_density_chart(ev: pd.DataFrame) -> go.Figure:
    ev_active = ev[ev["event_type"] != "idle"].copy()
    ev_active["bucket"] = ev_active["timestamp"].dt.floor("5min")
    density = ev_active.groupby("bucket").size().reset_index(name="count")
    fig = go.Figure(go.Scatter(
        x=density["bucket"], y=density["count"],
        mode="lines", fill="tozeroy",
        line=dict(color="#4f9cf9", width=2.5, shape="spline", smoothing=0.7),
        fillcolor="rgba(79,156,249,0.12)",
        hovertemplate="<b>%{x|%H:%M}</b><br>Events: %{y}<extra></extra>",
        name="Activity Density",
    ))
    _lt(fig, h=300,
        title=dict(text="Activity Density Over Time", font=dict(size=15, color="#1a2035")),
        xaxis={**_LIGHT_AX, "title": "Time"},
        yaxis={**_LIGHT_AX, "title": "Events / 5 min"})
    return fig


def build_interaction_distribution_chart(ev: pd.DataFrame) -> go.Figure:
    counts = {
        "Keystrokes":    int(ev["key_burst"].sum()),
        "Mouse Clicks":  int((ev["event_type"] == "mouse_click").sum()),
        "Scroll Ticks":  int(ev["scroll_ticks"].sum()),
        "Arrow Nav":     int(ev["nav_count"].sum()),
        "Window Switch": int((ev["event_type"] == "window_switch").sum()),
        "Idle Events":   int((ev["event_type"] == "idle").sum()),
    }
    counts = {k: v for k, v in counts.items() if v > 0}
    sorted_c = dict(sorted(counts.items(), key=lambda x: x[1]))
    clrs = {"Keystrokes": "#4f9cf9", "Mouse Clicks": "#e8650a",
            "Scroll Ticks": "#3dd68c", "Arrow Nav": "#f0c040",
            "Window Switch": "#9b8ef4", "Idle Events": "#94a3b8"}
    n = len(sorted_c)
    fig = go.Figure(go.Bar(
        x=list(sorted_c.values()), y=list(sorted_c.keys()),
        orientation="h",
        marker=dict(color=[clrs.get(k, "#8a9bb8") for k in sorted_c], line=dict(width=0)),
        hovertemplate="<b>%{y}</b>: %{x:,}<extra></extra>",
    ))
    _lt(fig, h=bar_chart_height(n, px_per_bar=46),
        title=dict(text="Interaction Type Distribution", font=dict(size=15, color="#1a2035")),
        xaxis={**_LIGHT_AX, "title": "Count"},
        yaxis={**_LIGHT_AX, "autorange": "reversed"},
        bargap=0.4,
        margin=dict(l=110, r=60, t=50, b=50))
    return fig


def build_keystrokes_by_app_chart(ev: pd.DataFrame) -> go.Figure:
    keys_by_app = (ev[ev["event_type"] == "keyboard"]
                   .groupby("application")["key_burst"].sum()
                   .sort_values(ascending=True)
                   .tail(12))
    if keys_by_app.empty:
        return go.Figure()
    apps  = [clean_app_name(a) for a in keys_by_app.index]
    vals  = keys_by_app.values
    lm    = min(max(int(max(len(a) for a in apps) * 7 + 20), 160), 480)
    n     = len(apps)
    fig = go.Figure(go.Bar(
        x=vals, y=apps, orientation="h",
        marker=dict(
            color=vals,
            colorscale=[[0.0, "#2a0e00"], [0.35, "#e8650a"], [1.0, "#f5a623"]],
            line=dict(width=0),
        ),
        text=[f"  {v:,}" for v in vals],
        textposition="outside",
        textfont=dict(size=13, color="#e8650a"),
        hovertemplate="<b>%{y}</b><br>%{x:,} keystrokes<extra></extra>",
    ))
    _lt(fig, h=bar_chart_height(n),
        title=dict(text="Keystrokes by App", font=dict(size=15, color="#1a2035")),
        xaxis={**_LIGHT_AX, "title": "Keystrokes"},
        yaxis=_LIGHT_AX,
        bargap=0.4,
        margin=dict(l=lm, r=100, t=50, b=50),
        showlegend=False)
    return fig


def build_scroll_by_app_chart(ev: pd.DataFrame) -> go.Figure:
    scroll_by_app = (ev[ev["event_type"] == "scroll"]
                     .groupby("application")
                     .agg(scroll_ticks=("scroll_ticks", "sum"))
                     .sort_values("scroll_ticks", ascending=True)
                     .tail(12))
    if scroll_by_app.empty:
        return go.Figure()
    apps  = [clean_app_name(a) for a in scroll_by_app.index]
    ticks = scroll_by_app["scroll_ticks"].values
    dist  = (ticks * 120 / 1000).round(1)
    lm    = min(max(int(max(len(a) for a in apps) * 7 + 20), 160), 480)
    n     = len(apps)
    fig = go.Figure(go.Bar(
        x=ticks, y=apps, orientation="h",
        marker=dict(
            color=ticks,
            colorscale=[[0.0, "#1a0830"], [0.45, "#6a2fbf"], [1.0, "#9b8ef4"]],
            line=dict(width=0),
        ),
        text=[f"  {v:,}" for v in ticks],
        textposition="outside",
        textfont=dict(size=13, color="#9b8ef4"),
        hovertemplate="<b>%{y}</b><br>Ticks: %{x:,}<br>~%{customdata:.1f} m<extra></extra>",
        customdata=dist,
    ))
    _lt(fig, h=bar_chart_height(n),
        title=dict(text="Scroll Volume by App", font=dict(size=15, color="#1a2035")),
        xaxis={**_LIGHT_AX, "title": "Scroll Ticks"},
        yaxis=_LIGHT_AX,
        bargap=0.4,
        margin=dict(l=lm, r=100, t=50, b=50),
        showlegend=False)
    return fig


def build_clicks_by_app_chart(ev: pd.DataFrame) -> go.Figure:
    clicks_by_app = (ev[ev["event_type"] == "mouse_click"]
                     .groupby("application").size()
                     .sort_values(ascending=True)
                     .tail(12))
    if clicks_by_app.empty:
        return go.Figure()
    apps  = [clean_app_name(a) for a in clicks_by_app.index]
    vals  = clicks_by_app.values
    lm    = min(max(int(max(len(a) for a in apps) * 7 + 20), 160), 480)
    n     = len(apps)
    fig = go.Figure(go.Bar(
        x=vals, y=apps, orientation="h",
        marker=dict(
            color=vals,
            colorscale=[[0.0, "#052a1a"], [0.35, "#1e8a50"], [1.0, "#3dd68c"]],
            line=dict(width=0),
        ),
        text=[f"  {v:,}" for v in vals],
        textposition="outside",
        textfont=dict(size=13, color="#16a34a"),
        hovertemplate="<b>%{y}</b><br>%{x:,} clicks<extra></extra>",
    ))
    _lt(fig, h=bar_chart_height(n),
        title=dict(text="Mouse Clicks by App", font=dict(size=15, color="#1a2035")),
        xaxis={**_LIGHT_AX, "title": "Clicks"},
        yaxis=_LIGHT_AX,
        bargap=0.4,
        margin=dict(l=lm, r=100, t=50, b=50),
        showlegend=False)
    return fig


def build_context_switch_timeline(ev: pd.DataFrame) -> go.Figure:
    sw = ev[ev["event_type"] == "window_switch"].copy()
    if sw.empty:
        return go.Figure()
    sw["bucket"] = sw["timestamp"].dt.floor("5min")
    sw_bin = sw.groupby("bucket").size().reset_index(name="switches")
    fig = go.Figure(go.Bar(
        x=sw_bin["bucket"], y=sw_bin["switches"],
        marker=dict(
            color=sw_bin["switches"],
            colorscale=[[0, "#fff7ed"], [0.5, "#fb923c"], [1, "#dc2626"]],
            line=dict(width=0)),
        hovertemplate="<b>%{x|%H:%M}</b><br>%{y} switches<extra></extra>",
    ))
    _lt(fig, h=260,
        title=dict(text="Context Switch Frequency (5-min buckets)", font=dict(size=15, color="#1a2035")),
        xaxis={**_LIGHT_AX, "title": "Time"},
        yaxis={**_LIGHT_AX, "title": "Switches"})
    return fig


def fig_to_html(fig: go.Figure, first: bool = True) -> str:
    try:
        include_plotlyjs = "cdn" if first else False
        return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs,
                           config={"displayModeBar": True, "responsive": True})
    except Exception:
        return "<p><em>Chart could not be rendered.</em></p>"


# ── THE SINGLE SOURCE-OF-TRUTH HTML BUILDER ────────────────────────────────
def build_html_report(data: dict, title: str = "NeuroFocus Attention Report") -> str:
    """
    Builds the canonical HTML report.
    This is the ONLY report builder — both report_generator.py and dashboard.py
    call this function so the downloaded file is always identical to what is shown.
    """
    r   = data.get("report", {})
    it  = r.get("interaction_totals", {})
    sc  = r.get("overall_focus_score", 0)
    clr, gl = grade(sc)
    fb  = r.get("focus_breakdown", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Build all charts ──────────────────────────────────────────────────
    chart_first = True
    charts_html = ""

    def add_chart(fig):
        nonlocal chart_first, charts_html
        if fig and fig.data:
            charts_html += f'<div class="chart-box">{fig_to_html(fig, first=chart_first)}</div>\n'
            chart_first = False

    if "events" in data:
        ev = data["events"]
        add_chart(build_activity_density_chart(ev))
        add_chart(build_interaction_distribution_chart(ev))
        add_chart(build_keystrokes_by_app_chart(ev))
        add_chart(build_scroll_by_app_chart(ev))
        add_chart(build_clicks_by_app_chart(ev))
        add_chart(build_context_switch_timeline(ev))

    # ── App focus table ───────────────────────────────────────────────────
    focus_table_html = ""
    if "focus" in data:
        rows_html = ""
        for _, row in data["focus"].iterrows():
            app_type  = str(row.get("type", "neutral"))
            badge_cls = {"deep_work": "badge-green", "distraction": "badge-red"}.get(app_type, "badge-blue")
            label     = app_type.replace("_", " ").title()
            app_name  = clean_app_name(str(row.get("application", "")))
            score     = float(row.get("attention_score", 0))
            bar_w     = int(min(score, 100))
            bar_clr   = "#16a34a" if score >= 60 else "#d97706" if score >= 35 else "#dc2626"
            rows_html += f"""
            <tr>
              <td>{app_name}</td>
              <td><span class="badge {badge_cls}">{label}</span></td>
              <td>{float(row.get('focus_minutes', 0)):.1f} min</td>
              <td>{float(row.get('focus_pct', 0)):.1f}%</td>
              <td>{int(row.get('clicks', 0)):,}</td>
              <td>{int(row.get('keystrokes', 0)):,}</td>
              <td>{int(row.get('scroll_ticks', 0)):,}</td>
              <td>
                <div class="score-bar-wrap">
                  <div class="score-bar" style="width:{bar_w}%;background:{bar_clr}"></div>
                  <span class="score-bar-lbl">{score:.0f}</span>
                </div>
              </td>
            </tr>"""
        focus_table_html = f"""
        <table class="data-table">
          <thead><tr>
            <th>Application</th><th>Type</th><th>Time</th><th>Share</th>
            <th>Clicks</th><th>Keys</th><th>Scrolls</th><th>Score /100</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>"""

    # ── Context switching table ───────────────────────────────────────────
    sw_table_html = "<p class='no-data'>No window switch events recorded.</p>"
    if "events" in data:
        sw = data["events"][data["events"]["event_type"] == "window_switch"].copy()
        if not sw.empty:
            sw["from_a"] = sw["application"].apply(clean_app_name)
            sw["to_a"]   = sw["switch_dest"].apply(clean_app_name)
            pairs = (sw.groupby(["from_a", "to_a"]).size()
                       .reset_index(name="count")
                       .sort_values("count", ascending=False)
                       .head(25))
            pairs = pairs[(pairs["from_a"] != pairs["to_a"]) & (pairs["to_a"] != "")]
            if not pairs.empty:
                rows_html = "".join(
                    f"<tr><td>{row['from_a']}</td><td>→</td>"
                    f"<td>{row['to_a']}</td>"
                    f"<td style='text-align:center;font-weight:700;color:#4f9cf9'>{row['count']}</td></tr>"
                    for _, row in pairs.iterrows()
                )
                sw_table_html = f"""
                <table class="data-table">
                  <thead><tr><th>From</th><th></th><th>To</th><th>Count</th></tr></thead>
                  <tbody>{rows_html}</tbody>
                </table>"""

    # ── Raw event log (last 150) ──────────────────────────────────────────
    raw_html = "<p class='no-data'>No event data available.</p>"
    if "events" in data:
        ev_sample = data["events"][["timestamp", "event_type", "application"]].tail(150).copy()
        ev_sample["timestamp"] = ev_sample["timestamp"].dt.strftime("%H:%M:%S")
        type_badge = {
            "keyboard": "badge-blue", "mouse_click": "badge-green",
            "scroll": "badge-purple", "window_switch": "badge-orange",
            "idle": "badge-gray", "navigation": "badge-teal",
        }
        rows_html = "".join(
            f"<tr>"
            f"<td class='mono'>{row['timestamp']}</td>"
            f"<td><span class='badge {type_badge.get(row['event_type'], 'badge-gray')}'>{row['event_type']}</span></td>"
            f"<td>{clean_app_name(str(row['application']))}</td>"
            f"</tr>"
            for _, row in ev_sample.iterrows()
        )
        raw_html = f"""
        <table class="data-table">
          <thead><tr><th>Timestamp</th><th>Event Type</th><th>Application</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>"""

    # ── Insight classes ───────────────────────────────────────────────────
    dw_pct   = fb.get("deep_work_pct", 0)
    dist_pct = fb.get("distraction_pct", 0)
    sw_hr    = r.get("switches_per_hour", 0)
    dw_cls   = "green" if dw_pct >= 60 else "amber" if dw_pct >= 35 else "red"
    dist_cls = "red" if dist_pct > 25 else "green"
    sw_cls   = "red" if sw_hr > 30 else "green"
    sw_msg   = "⚠️ High fragmentation — consider time-blocking." if sw_hr > 30 else "✅ Good continuity. Healthy context management."

    session_start_str = str(r.get("session_start", ""))[:19]
    session_end_str   = str(r.get("session_end",   ""))[:19]

    # ── Hourly heatmap data (if available) ────────────────────────────────
    hourly_section = ""
    if "hourly" in data:
        hdf = data["hourly"]
        if not hdf.empty and "total_events" in hdf.columns:
            hourly_fig = go.Figure(go.Bar(
                x=hdf.get("hour_label", hdf["hour"].astype(str) + ":00"),
                y=hdf["total_events"],
                marker=dict(
                    color=hdf["total_events"],
                    colorscale=[[0, "#e8f4fd"], [0.5, "#4f9cf9"], [1, "#1d4ed8"]],
                    line=dict(width=0),
                ),
                hovertemplate="<b>%{x}</b><br>Events: %{y:,}<extra></extra>",
            ))
            _lt(hourly_fig, h=260,
                title=dict(text="Activity by Hour of Day", font=dict(size=15, color="#1a2035")),
                xaxis={**_LIGHT_AX, "title": "Hour"},
                yaxis={**_LIGHT_AX, "title": "Total Events"})
            hourly_section = f"""
            <div class="section">
              <div class="section-header">
                <span class="section-icon">🕐</span>
                <div class="section-title">Hourly Activity Patterns</div>
              </div>
              <div class="chart-box">{fig_to_html(hourly_fig, first=chart_first)}</div>
            </div>"""
            chart_first = False

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
      background: #f0f4f8;
      color: #1a2035;
      line-height: 1.6;
    }}

    /* ── Header ── */
    .header {{
      background: linear-gradient(135deg, #03050a 0%, #0d1425 55%, #1a2540 100%);
      color: #e8edf5;
      padding: 3rem 4rem 2.5rem;
      position: relative;
      overflow: hidden;
    }}
    .header::before {{
      content: '';
      position: absolute; top: -60px; right: -60px;
      width: 350px; height: 350px;
      background: radial-gradient(circle, rgba(79,156,249,0.12) 0%, transparent 70%);
    }}
    .header::after {{
      content: '';
      position: absolute; bottom: -40px; left: 30%;
      width: 250px; height: 250px;
      background: radial-gradient(circle, rgba(155,142,244,0.08) 0%, transparent 70%);
    }}
    .header-badge {{
      display: inline-block;
      background: rgba(79,156,249,0.15);
      border: 1px solid rgba(79,156,249,0.35);
      color: #7ab8fb;
      font-size: .7rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: .2em;
      padding: .25rem .9rem; border-radius: 99px;
      margin-bottom: .9rem;
    }}
    .header-title {{
      font-size: 2.6rem; font-weight: 800;
      letter-spacing: -.04em; line-height: 1.1;
      margin-bottom: .7rem;
      background: linear-gradient(135deg, #e8edf5 30%, #8a9bb8 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .header-meta {{
      font-size: .88rem; color: #8a9bb8;
      display: flex; flex-wrap: wrap; gap: 1.2rem; align-items: center;
    }}
    .header-meta strong {{ color: #c8d4e8; }}
    .score-pill {{
      display: inline-flex; align-items: center; gap: .4rem;
      background: {clr}22;
      border: 1px solid {clr}55;
      color: {clr};
      font-size: .82rem; font-weight: 700;
      padding: .22rem .8rem; border-radius: 99px;
    }}
    .score-dot {{
      width: 8px; height: 8px;
      background: {clr};
      border-radius: 50%;
    }}

    /* ── Layout ── */
    .container {{ max-width: 1140px; margin: 0 auto; padding: 2.5rem 2.5rem; }}

    /* ── Section ── */
    .section {{ margin-bottom: 2.8rem; }}
    .section-header {{
      display: flex; align-items: center; gap: .8rem;
      margin-bottom: 1.4rem;
      padding-bottom: .65rem;
      border-bottom: 2px solid #e2e8f0;
    }}
    .section-icon {{ font-size: 1.25rem; }}
    .section-title {{
      font-size: .82rem; font-weight: 800;
      text-transform: uppercase; letter-spacing: .14em;
      color: #64748b;
    }}

    /* ── KPI grid ── */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: .9rem;
      margin-bottom: 1.4rem;
    }}
    .kpi-card {{
      background: white;
      border: 1px solid #e2e8f0;
      border-radius: 16px;
      padding: 1.25rem 1.3rem;
      position: relative; overflow: hidden;
      transition: box-shadow .2s;
    }}
    .kpi-card:hover {{ box-shadow: 0 4px 20px rgba(0,0,0,.08); }}
    .kpi-card::before {{
      content: '';
      position: absolute; top: 0; left: 0; right: 0; height: 3px;
      background: linear-gradient(90deg, #4f9cf9, #9b8ef4);
      border-radius: 16px 16px 0 0;
    }}
    .kpi-card.green::before  {{ background: linear-gradient(90deg, #16a34a, #4ade80); }}
    .kpi-card.red::before    {{ background: linear-gradient(90deg, #dc2626, #f87171); }}
    .kpi-card.purple::before {{ background: linear-gradient(90deg, #7c3aed, #a78bfa); }}
    .kpi-card.amber::before  {{ background: linear-gradient(90deg, #d97706, #fbbf24); }}
    .kpi-card.cyan::before   {{ background: linear-gradient(90deg, #0891b2, #22d3ee); }}
    .kpi-label {{
      font-size: .63rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: .14em;
      color: #94a3b8; margin-bottom: .4rem;
    }}
    .kpi-value {{
      font-size: 2rem; font-weight: 800;
      color: #1a2035; letter-spacing: -.03em; line-height: 1.1;
    }}
    .kpi-card.green  .kpi-value {{ color: #16a34a; }}
    .kpi-card.red    .kpi-value {{ color: #dc2626; }}
    .kpi-card.purple .kpi-value {{ color: #7c3aed; }}
    .kpi-card.amber  .kpi-value {{ color: #d97706; }}
    .kpi-card.cyan   .kpi-value {{ color: #0891b2; }}
    .kpi-foot {{
      font-size: .7rem; color: #94a3b8;
      margin-top: .35rem; font-weight: 500;
    }}

    /* ── 8-col grid (2 rows of 4) ── */
    .kpi-grid-8 {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: .9rem;
      margin-bottom: 1.4rem;
    }}

    /* ── Insight ── */
    .insight {{
      background: #eff6ff;
      border-left: 3px solid #3b82f6;
      border-radius: 0 10px 10px 0;
      padding: .85rem 1.1rem;
      margin-bottom: .7rem;
      font-size: .88rem; color: #1e3a5f;
      font-weight: 500;
    }}
    .insight.green {{ background: #f0fdf4; border-left-color: #16a34a; color: #14532d; }}
    .insight.red   {{ background: #fef2f2; border-left-color: #dc2626; color: #7f1d1d; }}
    .insight.amber {{ background: #fffbeb; border-left-color: #d97706; color: #78350f; }}
    .insight strong {{ font-weight: 800; }}

    /* ── Charts ── */
    .chart-box {{
      background: white;
      border: 1px solid #e2e8f0;
      border-radius: 14px;
      padding: 1rem;
      margin-bottom: 1.2rem;
      overflow: hidden;
    }}
    .chart-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
    }}

    /* ── Tables ── */
    .table-wrap {{
      background: white;
      border: 1px solid #e2e8f0;
      border-radius: 14px;
      overflow: hidden;
    }}
    .data-table {{
      width: 100%; border-collapse: collapse;
      font-size: .84rem;
    }}
    .data-table thead th {{
      background: #1a2035; color: #e8edf5;
      padding: .75rem 1rem;
      text-align: left;
      font-size: .68rem; text-transform: uppercase;
      letter-spacing: .1em; font-weight: 700;
    }}
    .data-table tbody td {{
      padding: .6rem 1rem;
      border-bottom: 1px solid #f1f5f9;
      color: #334155; vertical-align: middle;
    }}
    .data-table tbody tr:last-child td {{ border-bottom: none; }}
    .data-table tbody tr:hover td {{ background: #f8fafc; }}

    /* ── Badges ── */
    .badge {{
      display: inline-block;
      padding: .18rem .62rem; border-radius: 99px;
      font-size: .7rem; font-weight: 700;
    }}
    .badge-green  {{ background: #dcfce7; color: #16a34a; }}
    .badge-red    {{ background: #fee2e2; color: #dc2626; }}
    .badge-blue   {{ background: #dbeafe; color: #2563eb; }}
    .badge-purple {{ background: #ede9fe; color: #7c3aed; }}
    .badge-orange {{ background: #ffedd5; color: #c2410c; }}
    .badge-gray   {{ background: #f1f5f9; color: #64748b; }}
    .badge-teal   {{ background: #ccfbf1; color: #0f766e; }}

    /* ── Score bar ── */
    .score-bar-wrap {{
      display: flex; align-items: center; gap: .5rem;
    }}
    .score-bar {{
      height: 6px; border-radius: 99px;
      flex: 1; max-width: 80px;
      background: #16a34a;
    }}
    .score-bar-lbl {{
      font-size: .78rem; font-weight: 700;
      color: #64748b; min-width: 24px;
    }}

    /* ── Mono ── */
    .mono {{ font-family: 'Consolas','Courier New',monospace; font-size: .82rem; }}

    /* ── No data ── */
    .no-data {{ color: #94a3b8; font-size: .88rem; padding: 1rem 0; font-style: italic; }}

    /* ── Session timeline ── */
    .timeline {{
      display: flex; align-items: center; gap: 0;
      background: white;
      border: 1px solid #e2e8f0;
      border-radius: 12px; overflow: hidden;
      margin-bottom: 1.4rem;
      height: 36px;
    }}
    .tl-seg {{
      height: 100%;
      display: flex; align-items: center; justify-content: center;
      font-size: .7rem; font-weight: 700; color: white;
      min-width: 4px;
      transition: opacity .2s;
    }}
    .tl-legend {{
      display: flex; gap: 1.2rem; flex-wrap: wrap;
      font-size: .78rem; color: #64748b;
      margin-bottom: 1rem;
    }}
    .tl-dot {{
      display: inline-block; width: 10px; height: 10px;
      border-radius: 3px; margin-right: .35rem;
      vertical-align: middle;
    }}

    /* ── Footer ── */
    .footer {{
      background: #1a2035;
      color: #64748b;
      padding: 1.5rem 3rem;
      text-align: center;
      font-size: .78rem;
      margin-top: .5rem;
    }}
    .footer strong {{ color: #94a3b8; }}

    /* ── Print ── */
    @media print {{
      body {{ background: white; }}
      .header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .kpi-card, .chart-box, .table-wrap {{ break-inside: avoid; }}
    }}

    @media (max-width: 900px) {{
      .kpi-grid, .kpi-grid-8 {{ grid-template-columns: repeat(2, 1fr); }}
      .chart-grid {{ grid-template-columns: 1fr; }}
      .header {{ padding: 2rem 1.5rem; }}
      .container {{ padding: 1.5rem; }}
    }}
  </style>
</head>
<body>

<!-- ── HEADER ── -->
<div class="header">
  <div class="header-badge">🧠 NeuroFocus · Attention Intelligence</div>
  <div class="header-title">{title}</div>
  <div class="header-meta">
    <span><strong>Date:</strong> {session_start_str[:10]}</span>
    <span><strong>Start:</strong> {session_start_str[11:]}</span>
    <span><strong>End:</strong> {session_end_str[11:]}</span>
    <span><strong>Active:</strong> {fmt_min(r.get("session_minutes", 0))}</span>
    <span><strong>Wall-clock:</strong> {fmt_min(r.get("wall_clock_minutes", 0))}</span>
    <span><strong>Generated:</strong> {now}</span>
    <span>
      <div class="score-pill">
        <div class="score-dot"></div>
        Focus Score: {sc:.0f}/100 — {gl}
      </div>
    </span>
  </div>
</div>

<div class="container">

  <!-- ── SESSION TIMELINE ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">📊</span>
      <div class="section-title">Session Overview</div>
    </div>

    <!-- Visual timeline bar -->
    {_build_timeline_bar(r)}

    <!-- 8 KPI cards -->
    <div class="kpi-grid-8">
      <div class="kpi-card">
        <div class="kpi-label">Active Time</div>
        <div class="kpi-value">{fmt_min(r.get("session_minutes", 0))}</div>
        <div class="kpi-foot">Capped (idle gaps excluded)</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Wall-Clock Span</div>
        <div class="kpi-value">{fmt_min(r.get("wall_clock_minutes", 0))}</div>
        <div class="kpi-foot">Total elapsed time</div>
      </div>
      <div class="kpi-card green">
        <div class="kpi-label">Deep Work</div>
        <div class="kpi-value">{fmt_min(r.get("deep_work_minutes", 0))}</div>
        <div class="kpi-foot">{dw_pct:.1f}% of session</div>
      </div>
      <div class="kpi-card red">
        <div class="kpi-label">Distraction</div>
        <div class="kpi-value">{fmt_min(r.get("distraction_minutes", 0))}</div>
        <div class="kpi-foot">{dist_pct:.1f}% of session</div>
      </div>
      <div class="kpi-card purple">
        <div class="kpi-label">Focus Score</div>
        <div class="kpi-value">{sc:.0f}<span style="font-size:1rem;color:#94a3b8">/100</span></div>
        <div class="kpi-foot">{gl}</div>
      </div>
      <div class="kpi-card cyan">
        <div class="kpi-label">Context Switches</div>
        <div class="kpi-value">{r.get("context_switches", 0)}</div>
        <div class="kpi-foot">{r.get("switches_per_hour", 0):.1f} per hour</div>
      </div>
      <div class="kpi-card amber">
        <div class="kpi-label">Total Events</div>
        <div class="kpi-value">{r.get("total_events", 0):,}</div>
        <div class="kpi-foot">Recorded interactions</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Apps Used</div>
        <div class="kpi-value">{r.get("total_apps_used", 0)}</div>
        <div class="kpi-foot">Unique applications</div>
      </div>
    </div>
  </div>

  <!-- ── INPUT TOTALS ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">⌨️</span>
      <div class="section-title">Input Totals</div>
    </div>
    <div class="kpi-grid">
      <div class="kpi-card amber">
        <div class="kpi-label">Keystrokes</div>
        <div class="kpi-value">{it.get("keystrokes", 0):,}</div>
        <div class="kpi-foot">Burst-decoded</div>
      </div>
      <div class="kpi-card green">
        <div class="kpi-label">Mouse Clicks</div>
        <div class="kpi-value">{it.get("clicks", 0):,}</div>
        <div class="kpi-foot">Individual clicks</div>
      </div>
      <div class="kpi-card purple">
        <div class="kpi-label">Scroll Ticks</div>
        <div class="kpi-value">{it.get("scroll_ticks", 0):,}</div>
        <div class="kpi-foot">Real scroll distance</div>
      </div>
      <div class="kpi-card cyan">
        <div class="kpi-label">Idle Minutes</div>
        <div class="kpi-value">{fmt_min(r.get("idle_minutes", 0))}</div>
        <div class="kpi-foot">{fb.get("idle_pct", 0):.1f}% of wall-clock</div>
      </div>
    </div>
  </div>

  <!-- ── FOCUS INSIGHTS ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">🎯</span>
      <div class="section-title">Focus Analysis & Insights</div>
    </div>
    <div class="insight {dw_cls}">
      <strong>Deep Work Ratio:</strong> {dw_pct:.1f}% of session on productive applications —
      {fmt_min(r.get("deep_work_minutes", 0))} of focused work time recorded.
    </div>
    <div class="insight {dist_cls}">
      <strong>Distraction Level:</strong> {dist_pct:.1f}% on off-task apps —
      {fmt_min(r.get("distraction_minutes", 0))} total.
      {"Consider blocking distracting sites during work hours." if dist_pct > 25 else "Great job keeping distractions low."}
    </div>
    <div class="insight">
      <strong>Idle Time:</strong> {fb.get("idle_pct", 0):.1f}% of wall-clock inactive —
      {fmt_min(r.get("idle_minutes", 0))} in idle/away periods.
    </div>
    <div class="insight {sw_cls}">
      <strong>Context Switching:</strong> {r.get("switches_per_hour", 0):.1f} switches/hr — {sw_msg}
    </div>
  </div>

  <!-- ── CHARTS ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">📈</span>
      <div class="section-title">Interaction Charts</div>
    </div>
    {charts_html}
  </div>

  <!-- ── HOURLY PATTERNS ── -->
  {hourly_section}

  <!-- ── APP BREAKDOWN TABLE ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">🖥</span>
      <div class="section-title">Application Breakdown</div>
    </div>
    <div class="table-wrap">
      {focus_table_html or '<p class="no-data" style="padding:1rem">Run analysis.py to generate app data.</p>'}
    </div>
  </div>

  <!-- ── CONTEXT SWITCHING TABLE ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">🔀</span>
      <div class="section-title">Context Switching — Top Transitions</div>
    </div>
    <div class="insight {sw_cls}">
      <strong>Switch Rate:</strong> {r.get("switches_per_hour", 0):.1f} switches/hr across
      {r.get("context_switches", 0)} total window changes in {fmt_min(r.get("session_minutes", 0))}.
    </div>
    <div class="table-wrap">{sw_table_html}</div>
  </div>

  <!-- ── RAW EVENT LOG ── -->
  <div class="section">
    <div class="section-header">
      <span class="section-icon">🗂</span>
      <div class="section-title">Raw Event Log (last 150 events)</div>
    </div>
    <div class="table-wrap">{raw_html}</div>
  </div>

</div><!-- /container -->

<div class="footer">
  <strong>NeuroFocus v2.0</strong> &nbsp;·&nbsp;
  100% Offline &nbsp;·&nbsp; No cloud &nbsp;·&nbsp; No telemetry &nbsp;·&nbsp; No surveillance
  &nbsp;·&nbsp; Report generated {now}
</div>

</body>
</html>"""
    return html


def _build_timeline_bar(r: dict) -> str:
    """Renders a proportional session timeline bar (deep work / distraction / idle / neutral)."""
    total  = float(r.get("session_minutes", 1) or 1)
    dw     = float(r.get("deep_work_minutes", 0) or 0)
    dist   = float(r.get("distraction_minutes", 0) or 0)
    idle   = float(r.get("idle_minutes", 0) or 0)
    neut   = max(total - dw - dist - idle, 0)

    def pct(v):
        return round(v / total * 100, 1)

    segments = [
        (pct(dw),   "#16a34a", "Deep Work"),
        (pct(neut), "#4f9cf9", "Neutral"),
        (pct(dist), "#dc2626", "Distraction"),
        (pct(idle), "#cbd5e1", "Idle"),
    ]
    segs_html = "".join(
        f'<div class="tl-seg" style="width:{p}%;background:{c}" title="{lbl}: {p}%"></div>'
        for p, c, lbl in segments if p > 0
    )
    legend_html = "".join(
        f'<span><span class="tl-dot" style="background:{c}"></span>{lbl}: {p}%</span>'
        for p, c, lbl in segments if p > 0
    )
    return f"""
    <div class="tl-legend">{legend_html}</div>
    <div class="timeline">{segs_html}</div>
    """


# ── PDF via wkhtmltopdf ────────────────────────────────────────────────────
def generate_pdf(html_path: str, pdf_path: str) -> bool:
    """
    Attempt PDF generation via wkhtmltopdf.
    FIX: was missing pdf_path parameter — always used undefined variable.
    Install: https://wkhtmltopdf.org/downloads.html
    """
    try:
        import subprocess
        result = subprocess.run(
            [
                "wkhtmltopdf",
                "--enable-local-file-access",
                "--page-size", "A4",
                "--margin-top", "0",
                "--margin-bottom", "0",
                "--margin-left", "0",
                "--margin-right", "0",
                "--print-media-type",
                html_path,
                pdf_path,          # ← FIX: was missing entirely
            ],
            capture_output=True, timeout=90,
        )
        if result.returncode == 0:
            return True
        print(f"  wkhtmltopdf error: {result.stderr.decode()[:300]}")
        return False
    except FileNotFoundError:
        print("  wkhtmltopdf not found.")
        print("  Install from: https://wkhtmltopdf.org/downloads.html")
        return False
    except Exception as e:
        print(f"  PDF generation error: {e}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────
def generate_report(title: str = "NeuroFocus Attention Report",
                    html_only: bool = False) -> dict:
    print("\n  NeuroFocus Report Generator v2.0")
    print("  " + "─" * 44)

    data = load_data()
    if not data:
        print("  ✗  No data found. Run monitor.py then analysis.py first.")
        return {}

    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(OUTPUT_DIR, f"neurofocus_report_{ts}.html")
    pdf_path  = os.path.join(OUTPUT_DIR, f"neurofocus_report_{ts}.pdf")

    html = build_html_report(data, title=title)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓  HTML report saved: {html_path}")

    result = {"html": html_path}

    if not html_only:
        print("  →  Attempting PDF via wkhtmltopdf...")
        if generate_pdf(html_path, pdf_path):   # ← FIX: pass both args
            print(f"  ✓  PDF saved: {pdf_path}")
            result["pdf"] = pdf_path
        else:
            print("  ℹ  PDF skipped. Open HTML in browser → File → Print → Save as PDF.")

    print(f"\n  ✅  Report ready: {html_path}")
    print("  ▶   Open in browser to view or print to PDF.\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NeuroFocus Report Generator v2.0")
    parser.add_argument("--title",     default="NeuroFocus Attention Report", help="Report title")
    parser.add_argument("--html-only", action="store_true", help="Generate HTML only (skip PDF)")
    args = parser.parse_args()
    generate_report(title=args.title, html_only=args.html_only)