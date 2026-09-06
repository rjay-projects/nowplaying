#!/usr/bin/env python3
"""
Terminal Spotify Now Playing dashboard.
Designed for small screens (e.g. 1024x600 touchscreens).
Uses only the Python standard library + the external 'chafa' binary for art.
"""

import os
import re
import sys
import json
import time
import random
import base64
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.parse
import urllib.error

CHAFA_AVAILABLE = shutil.which("chafa") is not None


def load_dotenv():
    """Load KEY=VALUE lines from a .env file next to this script, without overriding
    any environment variables that are already set."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv()

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN", "")

if not (CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN):
    print("Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REFRESH_TOKEN env vars.")
    sys.exit(1)

# ---------- colour helpers ----------

def rgb(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
HOME = "\x1b[H"
CLEAR = "\x1b[2J"
ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"

SPOTIFY_GREEN = rgb(30, 215, 96)
WHITE = rgb(235, 235, 235)
GREY = rgb(120, 120, 120)
CYAN = rgb(70, 200, 220)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def visible_len(s):
    return len(ANSI_RE.sub("", s))


def pad(s, width, align="left"):
    vlen = visible_len(s)
    if vlen >= width:
        return s
    space = " " * (width - vlen)
    if align == "left":
        return s + space
    if align == "right":
        return space + s
    left = (width - vlen) // 2
    right = width - vlen - left
    return " " * left + s + " " * right


def eq_color(level):
    # 0..1 -> green/yellow/red like a VU meter
    if level < 0.5:
        return rgb(40, 220, 100)
    if level < 0.8:
        return rgb(240, 210, 60)
    return rgb(240, 80, 70)


def truncate(s, width):
    if len(s) <= width:
        return s + " " * (width - len(s))
    return s[: max(0, width - 1)] + "…"


def truncate_visible(s, width):
    """ANSI-aware truncate: walks the string preserving escape codes intact,
    only counting and limiting *visible* characters. A naive raw-index slice
    here is dangerous - a chafa row can be packed with escape codes, and
    slicing by raw character count can cut mid-code and silently corrupt the
    row's actual visible width, throwing off everything printed beside it."""
    if visible_len(s) <= width:
        return s
    out = []
    visible_count = 0
    i = 0
    while i < len(s):
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        if visible_count >= max(0, width - 1):
            break
        out.append(s[i])
        visible_count += 1
        i += 1
    return "".join(out) + "…" + RESET


# ---------- Spotify API ----------

_access_token = None
_expires_at = 0


def get_access_token():
    global _access_token, _expires_at
    if _access_token and time.time() < _expires_at - 30:
        return _access_token
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    data = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN}
    ).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={"Authorization": f"Basic {auth}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    _access_token = body["access_token"]
    _expires_at = time.time() + body.get("expires_in", 3600)
    return _access_token


def get_playback_state():
    token = get_access_token()
    req = urllib.request.Request(
        "https://api.spotify.com/v1/me/player",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        raise


def get_queue():
    token = get_access_token()
    req = urllib.request.Request(
        "https://api.spotify.com/v1/me/player/queue",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_recently_played(limit=5):
    token = get_access_token()
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/me/player/recently-played?limit={limit}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ---------- rendering ----------

def format_time(ms):
    seconds = int(ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def render_bar(fraction, width, filled_color, icon_filled="█", icon_empty="░"):
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(width * fraction))
    return filled_color + (icon_filled * filled) + GREY + (icon_empty * (width - filled)) + RESET


_art_cache = {"url": None, "lines": None}


def render_album_art(url, cell_w, cell_h):
    if _art_cache["url"] == url and _art_cache["lines"] is not None:
        return _art_cache["lines"]

    if not url or not CHAFA_AVAILABLE:
        lines = [" " * cell_w for _ in range(cell_h)]
        _art_cache["url"] = url
        _art_cache["lines"] = lines
        return lines
    try:
        with urllib.request.urlopen(url) as resp:
            img_bytes = resp.read()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(img_bytes)
            path = f.name
        result = subprocess.run(
            [
                "chafa",
                "--size", f"{cell_w}x{cell_h}",
                "--symbols", "block",
                "--colors", "full",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        os.unlink(path)
        lines = result.stdout.split("\n")
        lines = [l for l in lines if l != ""]
        while len(lines) < cell_h:
            lines.append(" " * cell_w)
        lines = lines[:cell_h]
        # Force every row to the exact same visible width regardless of how chafa
        # letterboxed it internally - a mismatch here misaligns every column drawn
        # beside the art (equalizer, volume bar, side panel).
        lines = [pad(truncate_visible(l, cell_w), cell_w, "left") for l in lines]
        _art_cache["url"] = url
        _art_cache["lines"] = lines
        return lines
    except Exception:
        return [" " * cell_w for _ in range(cell_h)]


_eq_levels = None


def next_eq_frame(num_bands, height, playing, band_width=2, gap=1):
    global _eq_levels
    if _eq_levels is None or len(_eq_levels) != num_bands:
        _eq_levels = [random.random() for _ in range(num_bands)]

    rows = [[] for _ in range(height)]  # rows[0] = top
    gap_str = " " * gap
    for i in range(num_bands):
        if playing:
            _eq_levels[i] += random.uniform(-0.12, 0.12)
            _eq_levels[i] = max(0.05, min(1.0, _eq_levels[i]))
        else:
            _eq_levels[i] = max(0.0, _eq_levels[i] - 0.05)

        filled_rows = int(round(_eq_levels[i] * height))
        for r in range(height):
            row_from_bottom = height - r
            if row_from_bottom <= filled_rows:
                level_at_row = row_from_bottom / height
                cell = eq_color(level_at_row) + ("█" * band_width) + RESET
            else:
                cell = GREY + ("░" * band_width) + RESET
            if i > 0:
                rows[r].append(gap_str)
            rows[r].append(cell)

    return ["".join(row) for row in rows]


_side_panel_cache = {"last_fetch": 0, "queue": [], "recent": []}


def get_side_panel_lines(panel_h, panel_w):
    now = time.time()
    if now - _side_panel_cache["last_fetch"] > 15:
        try:
            q = get_queue()
            _side_panel_cache["queue"] = [
                f"{t['name']} — {t['artists'][0]['name']}" for t in q.get("queue", [])[:5]
            ]
        except Exception:
            pass
        try:
            r = get_recently_played(limit=5)
            _side_panel_cache["recent"] = [
                f"{it['track']['name']} — {it['track']['artists'][0]['name']}"
                for it in r.get("items", [])
            ]
        except Exception:
            pass
        _side_panel_cache["last_fetch"] = now

    lines = []
    half = panel_h // 2

    lines.append(f"{BOLD}{CYAN}UP NEXT{RESET}" + " " * max(0, panel_w - 7))
    for i in range(half - 1):
        if i < len(_side_panel_cache["queue"]):
            lines.append(GREY + truncate(_side_panel_cache["queue"][i], panel_w) + RESET)
        else:
            lines.append(" " * panel_w)

    lines.append(f"{BOLD}{CYAN}RECENTLY PLAYED{RESET}" + " " * max(0, panel_w - 15))
    for i in range(panel_h - half - 1):
        if i < len(_side_panel_cache["recent"]):
            lines.append(GREY + truncate(_side_panel_cache["recent"][i], panel_w) + RESET)
        else:
            lines.append(" " * panel_w)

    while len(lines) < panel_h:
        lines.append(" " * panel_w)
    return lines[:panel_h]


def clear():
    sys.stdout.write(CLEAR + HOME)


# ---------- system stats (CPU / memory / disk) ----------

_prev_cpu_times = {}
_prev_cpu_total = None


def _read_cpu_times():
    times = {}
    total_line = None
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            label = parts[0]
            nums = list(map(int, parts[1:]))
            idle = nums[3] + nums[4] if len(nums) > 4 else nums[3]
            total = sum(nums)
            if label == "cpu":
                total_line = (idle, total)
            else:
                times[label] = (idle, total)
    return times, total_line


def get_cpu_usage_per_core():
    global _prev_cpu_times, _prev_cpu_total
    current, current_total = _read_cpu_times()
    usage = {}
    for core, (idle, total) in current.items():
        prev = _prev_cpu_times.get(core)
        if prev:
            prev_idle, prev_total = prev
            delta_idle = idle - prev_idle
            delta_total = total - prev_total
            usage[core] = max(0.0, min(100.0, 100.0 * (1 - delta_idle / delta_total))) if delta_total > 0 else 0.0
        else:
            usage[core] = 0.0

    overall = 0.0
    if current_total and _prev_cpu_total:
        prev_idle, prev_total = _prev_cpu_total
        idle, total = current_total
        delta_idle = idle - prev_idle
        delta_total = total - prev_total
        overall = max(0.0, min(100.0, 100.0 * (1 - delta_idle / delta_total))) if delta_total > 0 else 0.0

    _prev_cpu_times = current
    _prev_cpu_total = current_total

    ordered = sorted(usage.items(), key=lambda kv: int(kv[0].replace("cpu", "")))
    return [v for _, v in ordered], overall


def get_cpu_model_and_freq():
    model = ""
    freqs = []
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name") and not model:
                    model = line.split(":", 1)[1].strip()
                    # trim marketing fluff, keep it short
                    model = model.replace("(R)", "").replace("(TM)", "").replace("CPU", "").strip()
                    model = re.sub(r"\s+", " ", model)
                if line.startswith("cpu MHz"):
                    freqs.append(float(line.split(":", 1)[1].strip()))
    except Exception:
        pass
    freq_ghz = (sum(freqs) / len(freqs) / 1000) if freqs else 0.0
    return model, freq_ghz


def get_core_temps():
    """Best-effort per-core temps via hwmon (coretemp). Returns {core_index: celsius}."""
    temps = {}
    try:
        hwmon_root = "/sys/class/hwmon"
        for hwmon in os.listdir(hwmon_root):
            name_path = os.path.join(hwmon_root, hwmon, "name")
            if not os.path.exists(name_path):
                continue
            with open(name_path) as f:
                name = f.read().strip()
            if "coretemp" not in name and "k10temp" not in name:
                continue
            base = os.path.join(hwmon_root, hwmon)
            for fname in os.listdir(base):
                if not re.match(r"temp\d+_label", fname):
                    continue
                label_path = os.path.join(base, fname)
                with open(label_path) as f:
                    label = f.read().strip()
                m = re.search(r"Core (\d+)", label)
                if not m:
                    continue
                core_idx = int(m.group(1))
                input_path = label_path.replace("_label", "_input")
                if os.path.exists(input_path):
                    with open(input_path) as f:
                        temps[core_idx] = int(f.read().strip()) / 1000.0
    except Exception:
        pass
    return temps


def get_loadavg():
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        return 0.0, 0.0, 0.0


def get_uptime_str():
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours:02d}:{minutes:02d}"
        return f"{hours:02d}:{minutes:02d}"
    except Exception:
        return "?"


def get_memory_usage():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            value_kb = int(rest.strip().split()[0])
            info[key] = value_kb
    total = info.get("MemTotal", 0)
    free = info.get("MemFree", 0)
    available = info.get("MemAvailable", 0)
    cached = info.get("Cached", 0) + info.get("SReclaimable", 0)
    buffers = info.get("Buffers", 0)
    used = max(0, total - free - buffers - cached)
    to_gib = lambda kb: kb / (1024 * 1024)
    return {
        "total": to_gib(total),
        "used": to_gib(used),
        "available": to_gib(available),
        "cached": to_gib(cached),
        "free": to_gib(free),
        "used_pct": (used / total * 100) if total else 0,
        "available_pct": (available / total * 100) if total else 0,
        "cached_pct": (cached / total * 100) if total else 0,
        "free_pct": (free / total * 100) if total else 0,
    }


def get_disk_usage(path):
    try:
        st = os.statvfs(path)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        percent = (used / total * 100) if total else 0
        return percent, used / (1024 ** 3), total / (1024 ** 3)
    except Exception:
        return None


def meter_color(percent):
    if percent < 60:
        return rgb(60, 220, 100)
    if percent < 85:
        return rgb(240, 210, 60)
    return rgb(240, 80, 70)


def dot_bar(value_pct, width, filled_char="█", empty_char="░"):
    """Meter bar - colored solid fill for the used portion, light shade for empty."""
    value_pct = max(0.0, min(100.0, value_pct))
    filled = int(round(width * value_pct / 100))
    color = meter_color(value_pct)
    return color + (filled_char * filled) + RESET + GREY + (empty_char * (width - filled)) + RESET


def vertical_dot_bar(value_pct, height, filled_char="█", empty_char="░"):
    """Vertical meter, filled from the bottom up. Returns a list of
    strings, one per row, top to bottom."""
    value_pct = max(0.0, min(100.0, value_pct))
    filled_rows = int(round(height * value_pct / 100))
    color = meter_color(value_pct)
    rows = []
    for r in range(height):
        row_from_bottom = height - r
        if row_from_bottom <= filled_rows:
            rows.append(color + filled_char + RESET)
        else:
            rows.append(GREY + empty_char + RESET)
    return rows


def render_meter(label, percent, width, suffix=""):
    label_str = f"{label:<5}"
    bar_w = max(4, width - len(label_str) - 7 - len(suffix))
    color = meter_color(percent)
    bar = dot_bar(percent, bar_w)
    pct_str = f"{percent:5.1f}%"
    return f"{DIM}{label_str}{RESET}{bar} {color}{pct_str}{RESET}{suffix}"


# ---------- main frame builder ----------

def estimate_system_line_count(num_disks):
    # model line + overall CPU bar + mem bar + one line per disk
    return 3 + num_disks


def build_frame(data, term_cols, term_rows, system_line_count):
    lines = []
    width = max(60, min(term_cols - 2, 150))

    title = f"{BOLD}{SPOTIFY_GREEN}♪  NOW PLAYING{RESET}"
    lines.append(pad(title, width, "center"))
    lines.append("")

    has_track = bool(data and data.get("item"))

    item = data["item"] if has_track else {}
    track = item.get("name", "") if has_track else ""
    artists = ", ".join(a["name"] for a in item.get("artists", [])) if has_track else ""
    album = item.get("album", {}).get("name", "") if has_track else ""
    progress = data.get("progress_ms", 0) or 0 if has_track else 0
    duration = item.get("duration_ms", 0) or 1 if has_track else 1
    playing = data.get("is_playing", False) if has_track else False
    volume = None
    if has_track:
        device = data.get("device") or {}
        if "volume_percent" in device and device["volume_percent"] is not None:
            volume = device["volume_percent"]

    images = item.get("album", {}).get("images", []) if has_track else []
    art_url = images[0]["url"] if images else None

    # Fixed overhead outside the art block: title(1) + blank(1) + blank after art(1)
    # + track info(3) + blank(1) + progress(1) + volume(1) + border top/bottom(2)
    # + the system section (variable, but constant for the life of the process).
    FIXED_OVERHEAD = 11
    art_h = max(8, min(16, term_rows - FIXED_OVERHEAD - system_line_count))
    art_w = art_h * 2

    eq_bands = 12
    eq_band_width = 2
    eq_gap = 1
    eq_w = eq_bands * eq_band_width + (eq_bands - 1) * eq_gap

    vol_value = volume if (has_track and volume is not None) else 0
    vol_lines = vertical_dot_bar(vol_value, art_h)
    vol_w = 1

    art_lines = render_album_art(art_url, art_w, art_h)
    eq_lines = next_eq_frame(eq_bands, art_h, playing)

    panel_w = min(40, max(20, width - art_w - vol_w - eq_w - 14))
    try:
        panel_lines = get_side_panel_lines(art_h, panel_w) if has_track else [
            " " * panel_w for _ in range(art_h)
        ]
    except Exception:
        panel_lines = [" " * panel_w for _ in range(art_h)]

    gap = "   "
    for i in range(art_h):
        lines.append("  " + art_lines[i] + gap + vol_lines[i] + gap + eq_lines[i] + gap + panel_lines[i])
    lines.append("")

    if has_track:
        status_icon = f"{SPOTIFY_GREEN}▶{RESET}" if playing else f"{GREY}⏸{RESET}"
        lines.append(f"  {status_icon}  {BOLD}{WHITE}{track}{RESET}")
        lines.append(f"     {CYAN}{artists}{RESET}")
        lines.append(f"     {DIM}{GREY}{album}{RESET}")
    else:
        lines.append(pad(f"{GREY}Nothing playing{RESET}", width, "center"))
        lines.append("")
        lines.append("")
    lines.append("")

    bar_width = min(width - 16, 60)
    frac = progress / duration if duration else 0
    prog_bar = render_bar(frac, bar_width, SPOTIFY_GREEN) if has_track else GREY + ("░" * bar_width) + RESET
    lines.append(
        f"  {GREY}{format_time(progress) if has_track else '0:00'}{RESET} {prog_bar} {GREY}{format_time(duration) if has_track else '0:00'}{RESET}"
    )

    # volume moved to a vertical bar beside the album art; this row stays as a
    # blank spacer so the frame's total line count never changes.
    lines.append(" " * width)

    return lines, width


def build_system_lines(width, sys_stats):
    """Compact, single-column layout designed to fit small screens."""
    lines = []

    model, freq = sys_stats.get("cpu_info", ("", 0.0))
    load1, load5, load15 = sys_stats.get("loadavg", (0, 0, 0))
    uptime = sys_stats.get("uptime", "")
    if model:
        info_line = f"{DIM}{model}{' @ ' + format(freq, '.2f') + 'GHz' if freq else ''}{RESET}"
        lines.append(truncate_visible(info_line, width))

    overall = sys_stats.get("cpu_overall", 0.0)
    overall_color = meter_color(overall)
    overall_bar_w = max(10, width - 42)
    lines.append(
        f"{DIM}CPU  {RESET}{dot_bar(overall, overall_bar_w)} "
        f"{overall_color}{overall:5.1f}%{RESET} {DIM}ld {load1:.1f} {load5:.1f} {load15:.1f}  up {uptime}{RESET}"
    )

    cores = sys_stats.get("cpu", [])

    mem = sys_stats.get("mem")
    if mem:
        mem_bar_w = max(10, width - 30)
        color = meter_color(mem["used_pct"])
        lines.append(
            f"{DIM}MEM  {RESET}{dot_bar(mem['used_pct'], mem_bar_w)} "
            f"{color}{mem['used_pct']:5.1f}%{RESET} {DIM}{mem['used']:.1f}/{mem['total']:.1f}G{RESET}"
        )

    for disk_label, disk_stats in sys_stats.get("disks", []):
        if disk_stats is None:
            continue
        d_percent, d_used, d_total = disk_stats
        disk_bar_w = max(10, width - 30)
        color = meter_color(d_percent)
        label = f"{disk_label:<4}"[:4]
        lines.append(
            f"{DIM}{label} {RESET}{dot_bar(d_percent, disk_bar_w)} "
            f"{color}{d_percent:5.1f}%{RESET} {DIM}{d_used:.0f}/{d_total:.0f}G{RESET}"
        )

    return lines


def main():
    sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)

    POLL_INTERVAL = 3.0     # how often we actually ask Spotify
    RENDER_INTERVAL = 0.15  # how often we redraw the screen (smooth animation)
    SYS_POLL_INTERVAL = 1.0  # how often we sample CPU/memory/disk

    last_poll = 0
    last_sys_poll = 0
    data = None
    error = None
    local_progress = 0
    local_playing = False
    last_render_time = time.time()
    prev_lines = []

    disk_paths = [("/", "/")]
    extra_disks = os.environ.get("NOWPLAYING_EXTRA_DISKS", "")
    for entry in extra_disks.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            path, label = entry.split(":", 1)
        else:
            path, label = entry, entry
        if os.path.exists(path):
            disk_paths.append((path.strip(), label.strip()[:5]))

    cpu_model, cpu_freq = get_cpu_model_and_freq()
    zero_mem = {
        "total": 0, "used": 0, "available": 0, "cached": 0, "free": 0,
        "used_pct": 0, "available_pct": 0, "cached_pct": 0, "free_pct": 0,
    }
    sys_stats = {
        "cpu": [0.0] * (os.cpu_count() or 1),
        "cpu_overall": 0.0,
        "cpu_info": (cpu_model, cpu_freq),
        "temps": {},
        "loadavg": (0, 0, 0),
        "uptime": "",
        "mem": zero_mem,
        "disks": [(label, (0.0, 0.0, 0.0)) for _, label in disk_paths],
    }
    get_cpu_usage_per_core()  # prime the first sample so deltas work from the start

    try:
        while True:
            now = time.time()

            if now - last_poll >= POLL_INTERVAL:
                try:
                    fresh = get_playback_state()
                    error = None
                    data = fresh
                    if data and data.get("item"):
                        local_progress = data.get("progress_ms", 0) or 0
                        local_playing = data.get("is_playing", False)
                except Exception as e:
                    error = str(e)
                last_poll = now
            else:
                # advance progress locally between polls so the bar keeps moving smoothly
                if data and data.get("item") and local_playing:
                    elapsed = (now - last_render_time) * 1000
                    duration = data["item"].get("duration_ms", 0) or 0
                    local_progress = min(local_progress + elapsed, duration)

            if now - last_sys_poll >= SYS_POLL_INTERVAL:
                try:
                    cores, overall = get_cpu_usage_per_core()
                    sys_stats["cpu"] = cores
                    sys_stats["cpu_overall"] = overall
                    sys_stats["temps"] = get_core_temps()
                    sys_stats["loadavg"] = get_loadavg()
                    sys_stats["uptime"] = get_uptime_str()
                    sys_stats["mem"] = get_memory_usage()
                    sys_stats["disks"] = [
                        (label, get_disk_usage(path)) for path, label in disk_paths
                    ]
                except Exception:
                    pass
                last_sys_poll = now

            last_render_time = now

            display_data = None
            if data and data.get("item"):
                display_data = dict(data)
                display_data["progress_ms"] = local_progress
                display_data["is_playing"] = local_playing

            size = shutil.get_terminal_size((100, 30))
            system_line_count = estimate_system_line_count(len(sys_stats.get("disks", [])))
            frame, width = build_frame(display_data, size.columns, size.lines, system_line_count)
            frame += build_system_lines(width, sys_stats)

            border_top = "  " + "─" * width
            border_bottom = "  " + "─" * width

            out_lines = [border_top]
            for line in frame:
                out_lines.append("  " + line)
            # Always reserve a fixed 2-line slot for errors (blank when there isn't
            # one) so the total line count - and therefore every row index below it -
            # never shifts between frames. A shifting line count was the root cause
            # of the stale/overlapping block artifact with the old diff-based redraw.
            if error:
                out_lines.append("")
                out_lines.append(f"  {rgb(240,80,70)}Error: {error}{RESET}")
            else:
                out_lines.append("")
                out_lines.append("")
            out_lines.append(border_bottom)

            out_lines = [pad(l, size.columns, "left") for l in out_lines]

            # blank out any leftover lines from a previous, taller frame
            while len(out_lines) < len(prev_lines):
                out_lines.append(" " * size.columns)

            # only transmit the lines that actually changed since the last frame -
            # this is the key fix for tearing/glitching on the bare console, since
            # a full-screen rewrite every 150ms is far more data than the hardware
            # can cleanly redraw in that window.
            buf_parts = []
            for i, line in enumerate(out_lines):
                if i >= len(prev_lines) or line != prev_lines[i]:
                    buf_parts.append(f"\x1b[{i + 1};1H" + line + "\x1b[K")
            prev_lines = out_lines

            if buf_parts:
                sys.stdout.write("".join(buf_parts))
                sys.stdout.flush()
            time.sleep(RENDER_INTERVAL)
    finally:
        sys.stdout.write(SHOW_CURSOR + ALT_SCREEN_OFF)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + ALT_SCREEN_OFF)
        sys.stdout.flush()
