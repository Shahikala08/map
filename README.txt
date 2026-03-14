
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║        ███╗   ██╗███████╗██╗   ██╗██████╗  ██████╗                 ║
║        ████╗  ██║██╔════╝██║   ██║██╔══██╗██╔═══██╗                ║
║        ██╔██╗ ██║█████╗  ██║   ██║██████╔╝██║   ██║                ║
║        ██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██║   ██║                ║
║        ██║ ╚████║███████╗╚██████╔╝██║  ██║╚██████╔╝                ║
║        ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝                ║
║                                                                      ║
║              F O C U S   —   v 2 . 0                                ║
║         Offline Human Attention Intelligence Platform                ║
╚══════════════════════════════════════════════════════════════════════╝


WHAT'S NEW IN v2.0
══════════════════

  Monitor
  ───────
  • Mouse scroll tracking (in addition to clicks & keyboard)
  • Window-switch detection with source → destination logging
  • Idle period detection with gap duration
  • System CPU & RAM snapshot per event
  • Hourly time-slot tagging
  • Session metadata JSON (start/end, duration, event totals)
  • Batch-logging (reduces noise; logs every 10 keystrokes / 5 scrolls)

  Analysis
  ────────
  • App classification: deep_work / distraction / neutral
  • Attention Score per app (0–100) based on time, density & type
  • Overall Focus Score for session
  • Deep-work vs distraction breakdown (minutes & percentages)
  • Context-switch rate per hour
  • Hourly patterns CSV (events, CPU, RAM by hour)
  • Clean JSON report for dashboard

  Dashboard (5 pages)
  ───────────────────
  • 📊 Overview     — KPI cards, focus gauge, breakdown donut, app bar chart
  • ⏱ Focus Deep-Dive — scatter, treemap, event-type distribution
  • 📈 Hourly Patterns — timeline, heatmap, CPU/RAM chart
  • 🔀 Context Switching — switch frequency, source & destination apps
  • 🖥 System & Raw Log  — session info, raw event table, full JSON report


QUICK START
═══════════

  Step 1 — Install Python 3.9+
  ─────────────────────────────
    https://python.org/downloads

  Step 2 — Install dependencies
  ──────────────────────────────
    pip install -r requirements.txt

  Step 3 — Start monitoring
  ─────────────────────────
    python monitor.py

    Work normally (at least 5–10 minutes for meaningful data).
    Stop recording with  CTRL+C  when done.

  Step 4 — Run analysis
  ──────────────────────
    python analysis.py

    A summary will print in the terminal.

  Step 5 — View dashboard
  ────────────────────────
    streamlit run dashboard.py

    Your browser will open automatically at  http://localhost:8501


OUTPUT FILES
════════════

  events.csv          Raw event log (every interaction)
  session_meta.json   Session start/end/duration metadata
  focus_summary.csv   Per-app aggregated stats + attention scores
  hourly_patterns.csv Hour-by-hour activity breakdown
  attention_report.json Full structured intelligence report


PRIVACY
═══════

  Everything runs 100% locally.
  No data leaves your machine.
  No analytics, no telemetry, no cloud.
  Delete the CSV / JSON files to erase all history.


TIPS
════

  • Run for at least 30 minutes to get meaningful patterns.
  • The dashboard auto-refreshes every 30 seconds if you leave it open.
  • Use the "Top N apps" slider in the sidebar to focus on key apps.
  • The Focus Score is highest when you spend long uninterrupted stretches
    in deep-work apps with low context-switching frequency.


────────────────────────────────────────────────────────────────────────
NeuroFocus v2.0  ·  Offline  ·  Private  ·  Open Source
────────────────────────────────────────────────────────────────────────
