import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess
from datetime import datetime

# --- CONFIGURATION FOR DOOKUDU @ DEVI 70MM ---
DATES = ["20260809"]
VENUE_CODE = "DVRR"                  # Devi 70mm 4K Laser Dolby Atmos
EVENT_CODE = "ET00006198"            # Dookudu
STATE_FILE = "state_dkdv.json"       # Persistent state tracking
NTFY_TOPIC = "dksdntf"               # Notification topic
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 minutes

USE_WARP = False

PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

# Regional Headers configured for HYDERABAD (HYD)
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

def humanize_date(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    day = dt.day
    suffix = 'th' if 11 <= (day % 100) <= 13 else ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
    return f"{day}{suffix} {dt.strftime('%B')}"

def quiet_git_pull():
    """Fetches and hard resets to match remote main, wiping local noise to prevent conflicts."""
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def read_local_state():
    """Reads state_dksd.json safely even if the file is empty or missing."""
    if os.path.exists(STATE_FILE):
        try:
            if os.path.getsize(STATE_FILE) == 0:
                return {}
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def load_state():
    quiet_git_pull()
    return read_local_state()

def save_state(deltas, commit_msg="Update Dookudu Devi 70mm state"):
    """Merges delta updates cleanly with remote Git state and pushes."""
    for attempt in range(3):
        quiet_git_pull()
        latest_state = read_local_state()
        
        for s_id, s_data in deltas.items():
            latest_state[s_id] = s_data
            
        with open(STATE_FILE, "w") as f:
            json.dump(latest_state, f, indent=2)
            
        subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        
        if STATE_FILE in status.stdout:
            print(f"[GIT] Committing changes to {STATE_FILE} (Attempt {attempt+1})...")
            subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
            
            if quiet_git_push():
                print(f"[GIT] Successfully pushed merged state to repository.")
                return latest_state
            else:
                print(f"[GIT] Push attempt {attempt+1} failed. Retrying merge...")
                time.sleep(2)
        else:
            print("[GIT] Merged state is identical to remote. Nothing to push.")
            return latest_state
            
    print("[GIT] ❌ Failed to push after 3 attempts.")
    return latest_state

def trigger_ntfy(message):
    print(f"\n[!] ALERTING VIA NTFY ({NTFY_TOPIC}):\n{message}")
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8'),
            headers={"Priority": "urgent", "Title": "DOOKUDU DEVI 70MM UNBLOCK ALERT"},
            timeout=10
        )
        print(f"    -> Ntfy ping sent! Status: {resp.status_code}")
    except Exception as e:
        print(f"    -> Ntfy ping failed: {e}")

def toggle_warp():
    global USE_WARP
    if USE_WARP:
        print("    -> 🚨 [IP ROTATION] Disconnecting WARP...")
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        print("    -> 🚨 [IP ROTATION] Connecting to WARP...")
        subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
        time.sleep(5)
        USE_WARP = True

def make_bms_request(method, url, max_retries=3, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        try:
            if method.upper() == 'GET':
                resp = cffi_requests.get(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            print(f"    -> Status: {resp.status_code} (Using WARP: {USE_WARP})")
            
            if resp.status_code in [429, 403]:
                print(f"    -> ⚠️ Rate limited ({resp.status_code}) on attempt {attempt}/{max_retries}.")
                if attempt < max_retries:
                    toggle_warp()
                    continue
            return resp
            
        except Exception as e:
            print(f"    -> ⚠️ Network exception on attempt {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(3)
                continue
    return None

def fetch_sessions():
    sessions = []
    for date_code in DATES:
        time.sleep(4)
        print(f"\n[NETWORK] Fetching sessions for Date: {date_code}...")
        url = f"https://in.bookmyshow.com/api/movies-data/seatlayout/v1/primary?eventCode={EVENT_CODE}&dateCode={date_code}&regionCode=HYD&venueCode={VENUE_CODE}"
        
        resp = make_bms_request('GET', url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200:
            print(f"    -> Failed fetching {date_code}. Skipping...")
            continue
            
        try:
            shows = resp.json().get("data", {}).get("showTimes", [])
            print(f"    -> Found {len(shows)} total showtimes at Devi 70mm.")
            
            for show in shows:
                sessions.append({
                    "sessionId": show["sessionId"],
                    "dateCode": show.get("showDateCode", date_code),
                    "time": show["showTime"]
                })
                print(f"    -> Session ID: {show['sessionId']} | Time: {show['showTime']}")
            
        except Exception as e:
            print(f"    -> JSON Parse error for {date_code}: {e}")
            
    return sessions

def fetch_seat_layout(session_id):
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    
    print(f"    -> [POST] {url} (Session: {session_id})")
    resp = make_bms_request('POST', url, headers=POST_HEADERS, data=payload)
    if not resp or resp.status_code != 200:
        return ""
    try:
        return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception:
        return ""

def parse_layout_devi(str_data):
    """
    Reverse-engineered parser tailored specifically for Devi 70mm (DVRR).
    Maps Category Headers (UPPER BALCONY, LOWER BALCONY, FIRST CLASS, SECOND CLASS)
    and combines Category + Row Letter to track repeating row names accurately.
    """
    if not str_data:
        return {}
    
    parts = str_data.split("||")
    category_header = parts[0]
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    
    # 1. Map Category Codes (A, B, C, D) to human-readable names
    category_map = {}
    for cat in category_header.split("|"):
        cat_parts = cat.split(":")
        if len(cat_parts) >= 3:
            cat_name = cat_parts[0].strip()
            cat_code = cat_parts[1].strip()
            category_map[cat_code] = cat_name

    available_seats_by_row = {}
    
    # 2. Extract Available Seats per Row
    for row in rows_data.split("|"):
        if not row or ":" not in row:
            continue
            
        elements = row.split(":")
        if len(elements) < 3:
            continue
            
        row_letter = elements[1].strip()
        seats_tokens = elements[2:]
        
        category_name = "SECTION"
        available_in_row = []
        
        for token in seats_tokens:
            token = token.strip()
            # Token pattern: Category Prefix [A-Z], Status '1' (Available), 2 digits, '+' + Seat Number
            # Examples: A104+01 (Balcony Open), B101+01 (Lower Balcony Open), C101+01 (First Class Open)
            match = re.search(r"^([A-Z])1\d{2}\+(\d+)$", token)
            if match:
                cat_code = match.group(1)
                seat_num = match.group(2).lstrip("0")
                seat_num = seat_num if seat_num else "0"
                available_in_row.append(seat_num)
                
                if cat_code in category_map:
                    category_name = category_map[cat_code]
                    
        if available_in_row:
            unique_row_key = f"{category_name}: Row {row_letter}"
            available_seats_by_row[unique_row_key] = available_in_row
            
    return available_seats_by_row

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🚀 DOOKUDU @ DEVI 70MM 4K DOLBY ATMOS MONITOR")
    print("==================================================")
    target_sessions = fetch_sessions()
    
    total_sessions = len(target_sessions)
    print(f"\n✅ Found {total_sessions} active session(s) to monitor.")
    print("==================================================")
    
    if total_sessions == 0:
        print("No valid sessions found. Exiting.")
        return

    print("\n[GIT] Loading initial state from repository...")
    state = load_state()
    is_first_run = len(state) == 0
    if is_first_run:
        print("[STATE] Empty state found. Initializing baseline...")
    else:
        print(f"[STATE] Loaded existing state for {len(state)} session(s).")

    cycle_count = 1
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n==================================================")
        print(f"🔄 STARTING POLLING CYCLE {cycle_count}")
        print(f"==================================================")
        
        state = load_state()
        deltas = {}
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            s_date = session["dateCode"]
            s_time = session["time"]
            booking_url = f"https://in.bookmyshow.com/movies/hyderabad/seat-layout/ET00006198/DVRR/{s_id}/{s_date}"
            
            print(f"\n[{index}/{total_sessions}] Checking Session {s_id} ({s_date} @ {s_time})")
            print("    -> Sleeping for 21 seconds (Rate Limit Prevention)...")
            time.sleep(21) 
            
            str_data = fetch_seat_layout(s_id)
            if not str_data:
                print("    -> Error: Received empty strData.")
                continue
                
            current_seats = parse_layout_devi(str_data)
            current_total = sum(len(seats) for seats in current_seats.values())
            print(f"    -> Parse successful. Available Seats: {current_total}")
            
            if s_id not in state:
                state[s_id] = {"date": s_date, "time": s_time, "total": 0, "rows": {}}
            
            previous_total = state[s_id].get("total", 0)
            previous_rows = state[s_id].get("rows", {})
            
            newly_unblocked_count = 0
            unblocked_rows_list = []
            
            for row_key, seats in current_seats.items():
                old_seats_in_row = previous_rows.get(row_key, [])
                new_seats = set(seats) - set(old_seats_in_row)
                if new_seats:
                    newly_unblocked_count += len(new_seats)
                    unblocked_rows_list.append(row_key)
            
            if newly_unblocked_count > 0 and not is_first_run:
                print(f"    -> 🟢 DETECTED UNBLOCKS: +{newly_unblocked_count} new seats!")
                rows_str = ", ".join(sorted(unblocked_rows_list))
                human_date = humanize_date(s_date)
                
                msg = (
                    f"[{newly_unblocked_count}] DOOKUDU SEATS UNBLOCKED!\n"
                    f"Devi 70mm (RTC X Roads)\n"
                    f"Sections: {rows_str}\n"
                    f"Date/Time: {human_date}, {s_time}\n"
                    f"Total Available: {current_total}\n"
                    f"Book: {booking_url}"
                )
                trigger_ntfy(msg)
                
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]

            elif current_total < previous_total:
                print(f"    -> 🔴 Seats booked. Total dropped from {previous_total} down to {current_total}.")
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]
                
            else:
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                if is_first_run:
                    deltas[s_id] = state[s_id]
                print("    -> ⚪ No changes detected.")

        if is_first_run or deltas:
            print("\n[STATE] Syncing baseline to state_dksd.json...")
            state = save_state(deltas if deltas else state, f"Dookudu Devi 70mm state update cycle {cycle_count}")
            if is_first_run:
                is_first_run = False
                print("[STATE] Initial baseline successfully established in state_dksd.json!")
        else:
            print("\n[STATE] Cycle finished. No changes detected.")
            
        cycle_count += 1
        
    print("\n🏁 Time limit reached (5h 55m). Gracefully shutting down.")

if __name__ == "__main__":
    main()
