#!/usr/bin/env python3
"""
Watch InStock Radar for iPhone Duo stock at several Manhattan Apple Stores
and alert you (Mac + iPhone) when any of them comes in stock.

Run (Mac):      caffeinate -dimsu python3 duo_watch.py
Run (Windows):  py duo_watch.py      (keeps the PC awake by itself while running)
Stop:  Ctrl+C

While running:
  - type  s  + Enter in Terminal for a status update
  - send "status" to your ntfy command topic (<NTFY_TOPIC>-cmd) from your phone
"""

import html
import platform
import webbrowser
import json
import threading
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

# ---------- Settings ----------
STORES = {
    "World Trade Center": "https://instockradar.com/us/store/worldtradecenter",
    "West 14th Street":   "https://instockradar.com/us/store/west14thstreet",
    "SoHo":               "https://instockradar.com/us/store/soho",
    "Grand Central":      "https://instockradar.com/us/store/grandcentral",
}
PRODUCT = "iPhone Duo"
CHECK_EVERY_SECONDS = 60          # time between full rounds; be polite, don't go much lower
NTFY_TOPIC = ""                   # push to iPhone via the free ntfy app, e.g. "duo-watch-bc7342". Leave "" to skip.
IMESSAGE_TO = ""                  # your phone number or Apple ID email, e.g. "+16175551234". Leave "" to skip.
OPEN_PAGE_WHEN_IN_STOCK = True
HEALTHCHECK_URL = ""              # optional: healthchecks.io ping URL; alerts you if the Mac stops checking
# ------------------------------

# Shared state for status reports
STATE = {"started": datetime.now(), "last_round": None, "rounds": 0, "errors": 0, "last": {}}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
}

# Matches e.g. "iPhone Duo $1,999+ Sold out" (anchored on the price so the
# promo banner text that also says "iPhone Duo" doesn't confuse it).
STATUS_RE = re.compile(
    re.escape(PRODUCT) + r"\s*\$[\d,]+\+?\s*(Sold out|In stock|Low stock|Available)",
    re.IGNORECASE,
)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fetch_text(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(text))


def get_status(text):
    m = STATUS_RE.search(text)
    return m.group(1).strip().lower() if m else None


IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"


def osa(script):
    if IS_MAC:
        subprocess.run(["osascript", "-e", script], check=False)


def notify(title, message):
    try:
        if IS_MAC:
            osa(f'display notification "{message}" with title "{title}" sound name "Glass"')
        elif IS_WIN:
            import ctypes, winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            # Pop-up box in its own thread so it doesn't pause the checks
            threading.Thread(
                target=lambda: ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x40000),
                daemon=True,
            ).start()
    except Exception as e:
        log(f"Desktop notification failed: {e}")


def speak(text):
    try:
        if IS_MAC:
            subprocess.run(["say", text], check=False)
        elif IS_WIN:
            subprocess.Popen([
                "powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; "
                f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{text}')",
            ])
    except Exception as e:
        log(f"Speech failed: {e}")


def keep_awake():
    """Windows: stop the PC from sleeping while this script runs (like caffeinate on Mac)."""
    if IS_WIN:
        try:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            log("Windows sleep blocked while the watcher runs.")
        except Exception as e:
            log(f"Couldn't block sleep: {e}")


def send_imessage(to, message):
    osa(
        'tell application "Messages"\n'
        f'  send "{message}" to participant "{to}" of '
        '(1st account whose service type = iMessage)\n'
        "end tell"
    )


def push_phone(message, click_url=None, urgent=True):
    if not NTFY_TOPIC:
        return
    headers = {
        "Title": "iPhone Duo watcher",
        "Priority": "urgent" if urgent else "default",
    }
    if click_url:
        headers["Click"] = click_url
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(), headers=headers
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"Phone push failed: {e}")


def alert_in_stock(store, url, status):
    msg = f"{PRODUCT} is {status.upper()} at Apple {store}! {url}"
    log("*** " + msg + " ***")
    push_phone(msg, click_url=url)          # phone first: most important
    notify(f"iPhone Duo at {store}!", msg)
    speak(f"iPhone Duo is in stock at {store}")
    if IMESSAGE_TO and IS_MAC:
        send_imessage(IMESSAGE_TO, msg)
    if OPEN_PAGE_WHEN_IN_STOCK:
        webbrowser.open(url)


def ago(t):
    if not t:
        return "never"
    secs = int((datetime.now() - t).total_seconds())
    return f"{secs}s ago" if secs < 120 else f"{secs // 60} min ago"


def status_text():
    up = datetime.now() - STATE["started"]
    lines = [
        f"Watching {PRODUCT} - running {int(up.total_seconds() // 3600)}h {int(up.total_seconds() % 3600 // 60)}m",
        f"Last check: {ago(STATE['last_round'])} | rounds: {STATE['rounds']} | errors: {STATE['errors']}",
    ]
    lines += [f"{s}: {STATE['last'].get(s) or '?'}" for s in STORES]
    return "\n".join(lines)


def terminal_commands():
    """Type 's' + Enter in Terminal for a status report."""
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd in ("s", "status", ""):
            print("\n" + status_text() + "\n", flush=True)
        elif cmd in ("p", "phone"):
            push_phone(status_text(), urgent=False)
            log("Sent status to phone.")
        else:
            print("Commands: s = status, p = send status to phone, Ctrl+C = stop", flush=True)


def phone_commands():
    """Send 'status' to the <NTFY_TOPIC>-cmd topic in the ntfy app to get a reply push."""
    since = str(int(time.time()))
    while True:
        try:
            url = f"https://ntfy.sh/{NTFY_TOPIC}-cmd/json?poll=1&since={since}"
            with urllib.request.urlopen(url, timeout=20) as r:
                for raw in r.read().decode().splitlines():
                    msg = json.loads(raw)
                    if msg.get("event") != "message":
                        continue
                    since = msg["id"]
                    if "status" in msg.get("message", "").lower():
                        log("Status requested from phone.")
                        push_phone(status_text(), urgent=False)
        except Exception:
            pass
        time.sleep(10)


def heartbeat():
    if HEALTHCHECK_URL:
        try:
            urllib.request.urlopen(HEALTHCHECK_URL, timeout=10)
        except Exception:
            pass


def main():
    names = ", ".join(STORES)
    log(f"Watching {PRODUCT} at: {names}. Every ~{CHECK_EVERY_SECONDS}s. Ctrl+C to stop.")
    start_msg = f"Watcher started for {PRODUCT} at {names}."
    keep_awake()
    push_phone(start_msg, urgent=False)
    if IMESSAGE_TO and IS_MAC:
        send_imessage(IMESSAGE_TO, start_msg)

    last = STATE["last"] = {s: None for s in STORES}
    misses = {s: 0 for s in STORES}

    threading.Thread(target=terminal_commands, daemon=True).start()
    if NTFY_TOPIC:
        threading.Thread(target=phone_commands, daemon=True).start()
    log("Type s + Enter any time for a status update.")

    while True:
        for store, url in STORES.items():
            try:
                status = get_status(fetch_text(url))
                if status is None:
                    misses[store] += 1
                    log(f"{store}: couldn't find {PRODUCT} on the page (miss {misses[store]}).")
                    if misses[store] == 5:
                        warn = f"Can't read the {store} page anymore - check the script."
                        notify("Duo watcher", warn)
                        push_phone(warn, urgent=False)
                else:
                    misses[store] = 0
                    if status != last[store]:
                        log(f"{store}: {status}")
                        if status != "sold out":
                            alert_in_stock(store, url, status)
                    last[store] = status
            except Exception as e:
                STATE["errors"] += 1
                log(f"{store}: error: {e}")
            time.sleep(random.uniform(2, 5))   # small gap between stores
        summary = ", ".join(f"{s}: {last[s] or '?'}" for s in STORES)
        log(f"Checked - {summary}")
        STATE["last_round"] = datetime.now()
        STATE["rounds"] += 1
        heartbeat()
        time.sleep(CHECK_EVERY_SECONDS + random.randint(0, 15))


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="iPhone Duo stock watcher")
    p.add_argument("--test-alert", action="store_true",
                   help="fire a fake in-stock alert right now (tests notifications) and exit")
    p.add_argument("--product", help='watch a different product, e.g. "iPhone 18 Pro" (tests the full pipeline)')
    p.add_argument("--once", action="store_true",
                   help="check every store once, print the status, and exit")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.product:
        PRODUCT = args.product
        STATUS_RE = re.compile(
            re.escape(PRODUCT) + r"\s*\$[\d,]+\+?\s*(Sold out|In stock|Low stock|Available)",
            re.IGNORECASE,
        )
    if args.test_alert:
        store, url = next(iter(STORES.items()))
        alert_in_stock(store, url, "TEST - in stock")
        sys.exit(0)
    if args.once:
        for store, url in STORES.items():
            try:
                print(f"{store}: {get_status(fetch_text(url)) or 'NOT FOUND on page'}")
            except Exception as e:
                print(f"{store}: error: {e}")
        sys.exit(0)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nStopped.")
