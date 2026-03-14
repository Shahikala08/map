"""
NeuroFocus Monitor v2.2
=======================
Advanced attention & productivity tracker.
Captures: keystrokes, clicks, scroll events, idle periods,
window switches, per-session metadata, and arrow key navigation.

Arrow keys (↑ ↓ ← →) are tracked as "navigation" events —
equivalent to mouse scroll/click actions for attention mapping.

FIXED in v2.2:
  • pygetwindow import is fully guarded — works offline without it
  • Falls back to win32gui (Windows) then xdotool (Linux) then "Unknown"
  • No crash if pygetwindow is not installed

Usage:  python monitor.py
Stop:   CTRL+C
"""

from pynput import keyboard, mouse
import psutil
import time
import csv
import json
import os
import sys
from datetime import datetime
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────
LOG_FILE       = "events.csv"
SESSION_FILE   = "session_meta.json"
IDLE_THRESHOLD = 5   # seconds of silence = "idle" event logged once
STATS_INTERVAL = 30  # print live stats every N seconds

# Arrow keys to track as navigation (mouse-equivalent) actions
ARROW_KEYS = {
    keyboard.Key.up,
    keyboard.Key.down,
    keyboard.Key.left,
    keyboard.Key.right,
    keyboard.Key.page_up,
    keyboard.Key.page_down,
    keyboard.Key.home,
    keyboard.Key.end,
}

ARROW_KEY_NAMES = {
    keyboard.Key.up:        "up",
    keyboard.Key.down:      "down",
    keyboard.Key.left:      "left",
    keyboard.Key.right:     "right",
    keyboard.Key.page_up:   "page_up",
    keyboard.Key.page_down: "page_down",
    keyboard.Key.home:      "home",
    keyboard.Key.end:       "end",
}

# ── Window title detection — multi-backend, fully guarded ───────────────
_WIN_BACKEND = None

def _detect_window_backend():
    """Try to find a working window-title backend. Called once at startup."""
    global _WIN_BACKEND

    # 1) pygetwindow (cross-platform, preferred)
    try:
        import pygetwindow as _gw
        _gw.getActiveWindow()  # test call
        _WIN_BACKEND = ("pygetwindow", _gw)
        return
    except Exception:
        pass

    # 2) win32gui (Windows fallback, no extra install needed if pywin32 present)
    if sys.platform.startswith("win"):
        try:
            import win32gui as _w32
            _w32.GetForegroundWindow()
            _WIN_BACKEND = ("win32gui", _w32)
            return
        except Exception:
            pass

    # 3) xdotool (Linux fallback — runs subprocess)
    if sys.platform.startswith("linux"):
        try:
            import subprocess
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                _WIN_BACKEND = ("xdotool", None)
                return
        except Exception:
            pass

    # 4) No backend found — will always return "Unknown"
    _WIN_BACKEND = ("none", None)
    print("  ⚠  No window-title backend found. Tracking will work but app names will show as 'Unknown'.")
    print("     Install pygetwindow:  pip install pygetwindow")


def get_active_window() -> str:
    """Return the title of the currently focused window (safe, never raises)."""
    global _WIN_BACKEND
    if _WIN_BACKEND is None:
        _detect_window_backend()

    backend, lib = _WIN_BACKEND

    try:
        if backend == "pygetwindow":
            win = lib.getActiveWindow()
            return (win.title.strip() if win and win.title else "Unknown")

        elif backend == "win32gui":
            hwnd = lib.GetForegroundWindow()
            return lib.GetWindowText(hwnd).strip() or "Unknown"

        elif backend == "xdotool":
            import subprocess
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=1
            )
            return result.stdout.strip() or "Unknown"

    except Exception:
        pass

    return "Unknown"


def get_system_stats() -> dict:
    """Return lightweight CPU / RAM snapshot."""
    try:
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
        }
    except Exception:
        return {"cpu": 0, "ram": 0}


# ── State ────────────────────────────────────────────────────────────────
last_event_time  = time.time()
idle_logged      = False
session_start    = datetime.now()
event_counts     = defaultdict(int)
prev_window      = None
keystroke_buf    = 0   # count regular keystrokes between flushes
scroll_buf       = 0   # count scroll ticks between flushes
arrow_buf        = defaultdict(int)  # count arrow key presses: {direction: count}
ARROW_FLUSH      = 5   # batch-log every 5 arrow presses per direction


# ── Helpers ───────────────────────────────────────────────────────────────

def log_event(event_type: str, extra: str = ""):
    global prev_window, event_counts
    now        = datetime.now()
    timestamp  = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    app        = get_active_window()
    stats      = get_system_stats()
    hour_slot  = now.strftime("%H:00")

    # Detect window switches
    if app != prev_window and prev_window is not None:
        write_row(timestamp, "window_switch", prev_window,
                  stats["cpu"], stats["ram"], hour_slot, f"→ {app}")
        event_counts["window_switch"] += 1
    prev_window = app

    write_row(timestamp, event_type, app,
              stats["cpu"], stats["ram"], hour_slot, extra)
    event_counts[event_type] += 1


def write_row(ts, etype, app, cpu, ram, hour, extra=""):
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([ts, etype, app, cpu, ram, hour, extra])


def flush_arrow_buf():
    """Flush any buffered arrow key counts immediately."""
    global arrow_buf
    for direction, count in list(arrow_buf.items()):
        if count > 0:
            log_event("navigation", f"arrow:{direction}:count:{count}")
            arrow_buf[direction] = 0


# ── Listeners ─────────────────────────────────────────────────────────────

def on_key_press(key):
    global last_event_time, idle_logged, keystroke_buf, arrow_buf

    last_event_time = time.time()
    idle_logged     = False

    # ── Arrow / Navigation keys ──────────────────────────────────────────
    if key in ARROW_KEYS:
        direction = ARROW_KEY_NAMES.get(key, "unknown")
        arrow_buf[direction] += 1
        if arrow_buf[direction] >= ARROW_FLUSH:
            log_event("navigation", f"arrow:{direction}:count:{arrow_buf[direction]}")
            event_counts["navigation"] += 1
            arrow_buf[direction] = 0
        return

    # ── Regular keystrokes ───────────────────────────────────────────────
    keystroke_buf += 1
    if keystroke_buf >= 10:
        log_event("keyboard", f"burst:{keystroke_buf}")
        keystroke_buf = 0


def on_click(x, y, button, pressed):
    global last_event_time, idle_logged
    if pressed:
        last_event_time = time.time()
        idle_logged     = False
        log_event("mouse_click", f"btn:{button.name}")


def on_scroll(x, y, dx, dy):
    global last_event_time, idle_logged, scroll_buf
    last_event_time = time.time()
    idle_logged     = False
    scroll_buf     += abs(dy)
    if scroll_buf >= 5:
        log_event("scroll", f"ticks:{scroll_buf}")
        scroll_buf = 0


# ── Idle watcher ─────────────────────────────────────────────────────────

def idle_watcher_loop(stop_flag):
    global idle_logged, last_event_time
    next_stats = time.time() + STATS_INTERVAL
    while not stop_flag["stop"]:
        now = time.time()
        if now - last_event_time > IDLE_THRESHOLD and not idle_logged:
            log_event("idle", f"gap:{int(now - last_event_time)}s")
            idle_logged = True
        if now >= next_stats:
            total = sum(event_counts.values())
            parts = "  ".join(f"{k}={v}" for k, v in sorted(event_counts.items()))
            print(f"\r  📊 {total} events  |  {parts}   ", end="", flush=True)
            next_stats = now + STATS_INTERVAL
        time.sleep(0.5)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    global prev_window

    # Detect window backend before starting
    _detect_window_backend()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "event_type", "application",
            "cpu_pct", "ram_pct", "hour_slot", "extra"
        ])

    prev_window = get_active_window()

    print("\n")
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   NeuroFocus Monitor v2.2  — RECORDING      ║")
    print("  ╠══════════════════════════════════════════════╣")
    print(f"  ║  Session started : {session_start.strftime('%Y-%m-%d %H:%M:%S')}      ║")
    print(f"  ║  Log file        : {LOG_FILE:<24} ║")
    print(f"  ║  Idle threshold  : {IDLE_THRESHOLD}s                         ║")
    print("  ╠══════════════════════════════════════════════╣")
    print("  ║  Tracking: keys · clicks · scroll            ║")
    print("  ║            arrow keys · window switches      ║")
    print("  ║  Press  CTRL+C  to stop and save             ║")
    print("  ╚══════════════════════════════════════════════╝\n")

    stop_flag = {"stop": False}

    import threading
    watcher = threading.Thread(target=idle_watcher_loop,
                               args=(stop_flag,), daemon=True)
    watcher.start()

    try:
        with keyboard.Listener(on_press=on_key_press) as kl, \
             mouse.Listener(on_click=on_click, on_scroll=on_scroll) as ml:
            kl.join()
            ml.join()
    except KeyboardInterrupt:
        pass
    finally:
        stop_flag["stop"] = True

        # Flush any remaining buffers
        if keystroke_buf > 0:
            log_event("keyboard", f"burst:{keystroke_buf}")
        if scroll_buf > 0:
            log_event("scroll", f"ticks:{scroll_buf}")
        flush_arrow_buf()

        end_time = datetime.now()
        duration = (end_time - session_start).total_seconds()

        meta = {
            "session_start"   : session_start.isoformat(),
            "session_end"     : end_time.isoformat(),
            "duration_seconds": round(duration, 1),
            "event_totals"    : dict(event_counts),
        }
        with open(SESSION_FILE, "w") as f:
            json.dump(meta, f, indent=2)

        print(f"\n\n  ✅  Session ended — {round(duration/60, 1)} min recorded.")
        print(f"  📁  Saved: {LOG_FILE}  &  {SESSION_FILE}")
        print("  ▶   Run  python analysis.py  to process.\n")


if __name__ == "__main__":
    main()