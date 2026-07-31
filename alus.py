import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess
from datetime import datetime

# --- CONFIGURATION ---
DATES = ["20260801", "20260807", "20260808", "20260809"]
VENUE_CODE = "ALUC"
EVENT_CODE = "ET00502689"
STATE_FILE = "state_alus.json"

MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60)

# WARP only on GitHub cloud, not on laptop
USE_WARP = True if os.getenv("GITHUB_ACTIONS") else False

PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

GET_HEADERS = {
    "Host": "in.bookmyshow.com",
    "Content-Type": "application/json",
    "X-Latitude": "17.385044",
    "X-Subregion-Code": "HYD",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive"
}

POST_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-Timeout": "10",
    "X-Latitude": "17.385044",
    "X-Subregion-Code": "HYD",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate"
}

# Exact label confirmed from diagnostic
SCREEN_LABEL = "DOLBY CINEMA"

# Your ntfy topic
NTFY_TOPIC = "spiderman_prasads_730"

# Sleep settings
SLEEP_BETWEEN_SESSIONS = 45   # seconds between each session
SLEEP_BETWEEN_CYCLES   = 120  # seconds after all sessions done

# ================================================================
# GIT FUNCTIONS
# ================================================================

def quiet_git_pull():
    subprocess.run(
        ["git", "pull", "origin", "main", "--rebase"],
        capture_output=True, text=True, check=False
    )

def quiet_git_push():
    res = subprocess.run(
        ["git", "push", "origin", "main"],
        capture_output=True, text=True, check=False
    )
    return res.returncode == 0

def load_state():
    quiet_git_pull()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_state(state, commit_msg="Update ALUS state"):
    quiet_git_pull()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    subprocess.run(
        ["git", "add", STATE_FILE],
        capture_output=True, check=False
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True
    )

    if STATE_FILE in status.stdout:
        print(f"[GIT] Committing {STATE_FILE}...")
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, check=False
        )
        for attempt in range(3):
            if quiet_git_push():
                print(f"[GIT] Pushed successfully.")
                break
            print(f"[GIT] Push attempt {attempt+1} failed. Retrying...")
            time.sleep(2)
            quiet_git_pull()

# ================================================================
# NOTIFICATIONS
# ================================================================

def trigger_ntfy(title, message):
    print(f"\n{'='*52}")
    print(f"  ALERT: {title}")
    print(f"  {message}")
    print(f"{'='*52}")

    for i in range(3):
        try:
            resp = requests.post(
                f"https://ntfy.sh/alusdolby",
                data=message.encode("utf-8"),
                headers={
                    "Title":    title,
                    "Priority": "urgent",
                    "Tags":     "rotating_light,ticket",
                },
                timeout=10
            )
            if resp.status_code == 200:
                print(f"  -> Alert {i+1}/3 sent! Status: 200")
            else:
                print(f"  -> Alert {i+1}/3 status: {resp.status_code}")
        except Exception as e:
            print(f"  -> Alert {i+1}/3 failed: {e}")
        if i < 2:
            time.sleep(5)

def test_ntfy():
    print("\n  Sending startup test notification...")
    try:
        resp = requests.post(
            f"https://ntfy.sh/alusdolby",
            data="Spider-Man Dolby Monitor started at Allu Cinemas Kokapet!".encode("utf-8"),
            headers={
                "Title":    "ALUS Dolby Monitor Started",
                "Priority": "default",
                "Tags":     "white_check_mark",
            },
            timeout=10
        )
        if resp.status_code == 200:
            print(f"  -> Test sent! Check your phone.")
        else:
            print(f"  -> Test failed. Status: {resp.status_code}")
            print(f"  -> Check NTFY_TOPIC = 'alusdolby' is correct.")
    except Exception as e:
        print(f"  -> Test error: {e}")

# ================================================================
# WARP
# ================================================================

def toggle_warp():
    global USE_WARP
    if USE_WARP:
        print("    -> [WARP] Disconnecting...")
        subprocess.run(
            ["warp-cli", "--accept-tos", "disconnect"],
            capture_output=True, check=False
        )
        USE_WARP = False
    else:
        print("    -> [WARP] Connecting...")
        subprocess.run(
            ["warp-cli", "--accept-tos", "connect"],
            capture_output=True, check=False
        )
        time.sleep(5)
        USE_WARP = True

# ================================================================
# NETWORK
# ================================================================

def make_bms_request(method, url, max_retries=3, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        try:
            if method.upper() == "GET":
                resp = cffi_requests.get(
                    url, proxies=current_proxies,
                    impersonate="chrome", timeout=15, **kwargs
                )
            else:
                resp = cffi_requests.post(
                    url, proxies=current_proxies,
                    impersonate="chrome", timeout=15, **kwargs
                )

            print(f"    -> Status: {resp.status_code} (WARP: {USE_WARP})")

            if resp.status_code == 429:
                print(f"    -> Rate limited (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    toggle_warp()
                    continue
                else:
                    print("    -> Max retries reached.")

            return resp

        except Exception as e:
            print(f"    -> Error attempt {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(3)

    return None

# ================================================================
# BMS DATA FETCH
# ================================================================

def fetch_sessions():
    sessions = []
    for date_code in DATES:
        print(f"\n[NETWORK] Date: {date_code}...")
        url = (
            f"https://in.bookmyshow.com/api/movies-data/seatlayout/v1/primary"
            f"?eventCode={EVENT_CODE}&dateCode={date_code}"
            f"&regionCode=HYD&venueCode={VENUE_CODE}"
        )

        resp = make_bms_request("GET", url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200:
            print(f"    -> Failed for {date_code}. Skipping.")
            continue

        try:
            data = resp.json()

            if not resp.text.strip():
                print(f"    -> Empty response for {date_code}.")
                continue

            shows = data.get("data", {}).get("showTimes", [])
            print(f"    -> {len(shows)} shows found.")

            dolby_count = 0
            for show in shows:
                attr = show.get("attributes", "")

                if SCREEN_LABEL.upper() in attr.upper():
                    sessions.append({
                        "sessionId": show["sessionId"],
                        "dateCode":  show.get("showDateCode", date_code),
                        "time":      show["showTime"],
                        "screen":    attr,
                    })
                    dolby_count += 1
                    print(f"    -> MATCH: {show['showTime']} | '{attr}' | ID:{show['sessionId']}")

            if dolby_count == 0:
                print(f"    -> No Dolby matches. All screens found:")
                for s in shows:
                    print(f"       {s.get('showTime')} | '{s.get('attributes')}'")

        except Exception as e:
            print(f"    -> Parse error: {e}")

    return sessions

def fetch_seat_layout(session_id):
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = (
        f"strParam4=&strParam5=Y&strParam6=&strParam7=N"
        f"&strParam1={session_id}&strParam2=WEB&strParam3="
        f"&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0"
        f"&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    )

    resp = make_bms_request("POST", url, headers=POST_HEADERS, data=payload)

    if not resp or resp.status_code != 200:
        return ""

    try:
        return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception:
        return ""

def parse_layout(str_data):
    if not str_data:
        return {}

    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]

    available = {}
    for row in rows_data.split("|"):
        if not row or ":" not in row:
            continue
        elements = row.split(":")
        if len(elements) < 3:
            continue
        row_letter = elements[1]
        seats = elements[2:]

        available_in_row = []
        for seat in seats:
            match = re.search(r"A[^2]\d{2}(\d+)\+", seat)
            if match:
                available_in_row.append(match.group(1))

        if available_in_row:
            available[row_letter] = available_in_row

    return available

# ================================================================
# MAIN
# ================================================================

def main():
    start_time = time.time()

    print("=" * 52)
    print("  SPIDER-MAN — ALLU CINEMAS KOKAPET DOLBY MONITOR")
    print("=" * 52)
    print(f"  Movie  : Spider-Man Brand New Day (ET00502689)")
    print(f"  Venue  : Allu Cinemas Kokapet (ALUC)")
    print(f"  Screen : DOLBY CINEMA")
    print(f"  Dates  : {DATES}")
    print(f"  ntfy   : ntfy.sh/alusdolby")
    print(f"  WARP   : {USE_WARP}")
    print(f"  Sleep  : {SLEEP_BETWEEN_SESSIONS}s between sessions")
    print("=" * 52)

    # Send startup test notification
    test_ntfy()

    # Fetch all Dolby sessions
    print("\nFetching Dolby sessions...")
    target_sessions = fetch_sessions()
    total = len(target_sessions)

    print(f"\nFound {total} Dolby session(s) to monitor.")

    if total > 0:
        print("\nSession summary:")
        for s in target_sessions:
            print(f"  {s['dateCode']} | {s['time']} | ID:{s['sessionId']}")

    print("=" * 52)

    if total == 0:
        print("No sessions found. Exiting.")
        return

    # Load state
    state = load_state()
    is_first_run = len(state) == 0

    if is_first_run:
        print("\n[STATE] First run — recording baseline.")
        print("        No alerts this cycle.")
        print("        Alerts ACTIVE from cycle 2 onwards.")
    else:
        print(f"\n[STATE] Loaded {len(state)} session(s) from memory.")
        print("        Alerts ACTIVE.")

    cycle_count = 1

    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n{'='*52}")
        print(f"  CYCLE {cycle_count} | {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*52}")

        state = load_state()
        state_changed = False

        for index, session in enumerate(target_sessions, 1):
            s_id        = session["sessionId"]
            s_date      = session["dateCode"]
            s_time      = session["time"]
            booking_url = (
                f"https://in.bookmyshow.com/movies/NCR/seat-layout/ET00502689/ALUC/{s_id}/{s_date}"
            )

            print(f"\n  [{index}/{total}] {s_date} {s_time} | ID:{s_id}")

            if index > 1:
                time.sleep(SLEEP_BETWEEN_SESSIONS)

            str_data = fetch_seat_layout(s_id)
            if not str_data:
                print("    -> Empty seat data.")
                continue

            current_seats = parse_layout(str_data)
            current_total = sum(len(v) for v in current_seats.values())
            current_rows  = sorted(current_seats.keys())
            print(f"    -> Seats: {current_total} | Rows: {current_rows}")

            # Get previous state
            if s_id not in state:
                state[s_id] = {
                    "date":  s_date,
                    "time":  s_time,
                    "total": 0,
                    "rows":  {}
                }

            prev_total = state[s_id].get("total", 0)
            prev_rows  = state[s_id].get("rows", {})

            # Detect newly unblocked seats
            newly_unblocked = 0
            unblocked_rows  = []

            for row, seats in current_seats.items():
                old = set(prev_rows.get(row, []))
                new = set(seats) - old
                if new:
                    newly_unblocked += len(new)
                    unblocked_rows.append(row)

            # React to changes
            if newly_unblocked > 0 and not is_first_run:
                rows_str = ", ".join(sorted(unblocked_rows))
                print(f"    -> UNBLOCKED: +{newly_unblocked} seats in rows {rows_str}!")

                title = f"SPIDER-MAN DOLBY — {newly_unblocked} seats unblocked!"
                msg = (
                    f"Allu Cinemas Kokapet | Dolby Cinema\n"
                    f"Date: {s_date} | Time: {s_time}\n"
                    f"Rows: {rows_str}\n"
                    f"New seats: {newly_unblocked} | Total: {current_total}\n"
                    f"Detected: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"Book: {booking_url}"
                )
                trigger_ntfy(title, msg)

                state[s_id]["rows"]  = current_seats
                state[s_id]["total"] = current_total
                state_changed = True

            elif newly_unblocked > 0 and is_first_run:
                print(f"    -> First run — {newly_unblocked} seats noted (no alert).")
                state[s_id]["rows"]  = current_seats
                state[s_id]["total"] = current_total
                state_changed = True

            elif current_total > prev_total and not is_first_run:
                delta    = current_total - prev_total
                rows_str = ", ".join(current_rows)
                print(f"    -> SEATS INCREASED: +{delta}")

                title = f"SPIDER-MAN DOLBY — +{delta} more seats!"
                msg = (
                    f"Allu Cinemas Kokapet | Dolby Cinema\n"
                    f"Date: {s_date} | Time: {s_time}\n"
                    f"Rows: {rows_str}\n"
                    f"Total now: {current_total} (+{delta})\n"
                    f"Detected: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"Book: {booking_url}"
                )
                trigger_ntfy(title, msg)

                state[s_id]["rows"]  = current_seats
                state[s_id]["total"] = current_total
                state_changed = True

            elif current_total < prev_total:
                print(f"    -> Seats booked: {prev_total} -> {current_total}")
                state[s_id]["rows"]  = current_seats
                state[s_id]["total"] = current_total
                state_changed = True

            else:
                print(f"    -> No change ({current_total} seats).")

        if state_changed:
            save_state(state, f"ALUS Dolby cycle {cycle_count}")
        else:
            print(f"\n[STATE] No changes this cycle.")

        if is_first_run:
            is_first_run = False
            print("\n[STATE] Baseline established.")
            print("[STATE] Alerts ACTIVE from next cycle.")

        cycle_count += 1
        print(f"\n  Next cycle in {SLEEP_BETWEEN_CYCLES}s...")
        time.sleep(SLEEP_BETWEEN_CYCLES)

    print("\nTime limit reached.")
    save_state(load_state(), "ALUS final save")

if __name__ == "__main__":
    main()