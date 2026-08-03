import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess
from datetime import datetime

# --- CONFIGURATION FOR ALLU CINEMAS ---
DATES = ["20260808", "20260809"]
VENUE_CODE = "ALUC"                  # ALLU Cinemas, Kokapet
EVENT_CODE = "ET00502689"            # Spider-Man: Brand New Day
STATE_FILE = "state_alus.json"
NTFY_TOPIC = "alusdolby"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins

USE_WARP = False

# Cloudflare WARP local proxy
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

# Regional Headers set to NCR for ALLU Cinemas
GET_HEADERS = {
    "Host": "in.bookmyshow.com",
    "Content-Type": "application/json",
    "X-Latitude": "28.6139",
    "X-Subregion-Code": "NCR",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive"
}

POST_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-Timeout": "10",
    "X-Latitude": "28.6139",
    "X-Subregion-Code": "NCR",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate"
}

def humanize_date(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    day = dt.day
    if 11 <= (day % 100) <= 13:
        suffix = 'th'
    else:
        suffix = ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
    month_name = dt.strftime("%B")
    return f"{day}{suffix} {month_name}"

def quiet_git_pull():
    """Fetches and hard resets to match remote. Wipes local noise to prevent conflicts."""
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def read_local_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[STATE] ⚠️ JSON Error reading state: {e}")
            return {}
    return {}

def load_state():
    quiet_git_pull()
    return read_local_state()

def save_state(deltas, commit_msg="Update seat state"):
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
    print(f"\n[!] ALERTING VIA NTFY: {message}")
    for i in range(1):
        try:
            resp = requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=message.encode('utf-8'),
                headers={"Priority": "urgent"},
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
        time.sleep(6)
        print(f"\n[NETWORK] Fetching sessions for Date: {date_code}...")
        url = f"https://in.bookmyshow.com/api/movies-data/seatlayout/v1/primary?eventCode={EVENT_CODE}&dateCode={date_code}&regionCode=NCR&venueCode={VENUE_CODE}"
        
        resp = make_bms_request('GET', url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200:
            print(f"    -> Failed fetching {date_code}. Skipping...")
            continue
            
        try:
            shows = resp.json().get("data", {}).get("showTimes", [])
            print(f"    -> Found {len(shows)} total shows for this date. Filtering for DOLBY CINEMA...")
            
            dolby_count = 0
            for show in shows:
                attr = str(show.get("attributes", "")).upper()
                if "DOLBY" in attr or "ALL" in attr:
                    sessions.append({
                        "sessionId": show["sessionId"],
                        "dateCode": show.get("showDateCode", date_code),
                        "time": show["showTime"]
                    })
                    dolby_count += 1
            print(f"    -> Filtered {dolby_count} DOLBY CINEMA sessions for {date_code}.")
            
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

def parse_layout_allu(str_data):
    """
    Reverse-engineered parser designed specifically for ALLU Cinemas layout format.
    Filters tokens like 'D102+33' where status flag '1' represents open/available seats.
    """
    if not str_data:
        return {}
    
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    
    available_seats_by_row = {}
    
    for row in rows_data.split("|"):
        if not row or ":" not in row:
            continue
            
        elements = row.split(":")
        if len(elements) < 3:
            continue
            
        row_letter = elements[1]
        seats_tokens = elements[2:]
        
        available_in_row = []
        for token in seats_tokens:
            # Matches tokens starting with Category Letter, Status 1 (Available), 2 digits, and +SeatNumber
            match = re.search(r"^[A-Z]1\d{2}\+(\d+)$", token.strip())
            if match:
                seat_num = match.group(1).lstrip("0")
                available_in_row.append(seat_num)
                
        if available_in_row:
            available_seats_by_row[row_letter] = available_in_row
            
    return available_seats_by_row

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🚀 STARTING ALLU CINEMAS (DOLBY CINEMA) MONITOR")
    print("==================================================")
    target_sessions = fetch_sessions()
    
    total_sessions = len(target_sessions)
    print(f"\n✅ Found a total of {total_sessions} DOLBY CINEMA sessions to monitor.")
    print("==================================================")
    
    if total_sessions == 0:
        print("No valid sessions found. Exiting.")
        return

    print("\n[GIT] Loading initial state from repository...")
    state = load_state()
    is_first_run = len(state) == 0
    if is_first_run:
        print("[STATE] Empty state found. Initializing baseline silently...")
    else:
        print(f"[STATE] Loaded existing state for {len(state)} sessions.")

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
            
            print(f"\n[{index}/{total_sessions}] Checking Session {s_id} (Date: {s_date} Time: {s_time})")
            print("    -> Sleeping for 21 seconds (Rate Limit Prevention)...")
            time.sleep(21) 
            
            str_data = fetch_seat_layout(s_id)
            if not str_data:
                print("    -> Error: Received empty strData.")
                continue
                
            current_seats = parse_layout_allu(str_data)
            current_total = sum(len(seats) for seats in current_seats.values())
            print(f"    -> Parse successful. Current Available Seats: {current_total}")
            
            if s_id not in state:
                state[s_id] = {"date": s_date, "time": s_time, "total": 0, "rows": {}}
            
            previous_total = state[s_id].get("total", 0)
            previous_rows = state[s_id].get("rows", {})
            
            newly_unblocked_count = 0
            unblocked_rows_list = []
            
            for row, seats in current_seats.items():
                old_seats_in_row = previous_rows.get(row, [])
                new_seats = set(seats) - set(old_seats_in_row)
                if new_seats:
                    newly_unblocked_count += len(new_seats)
                    unblocked_rows_list.append(row)
            
            if newly_unblocked_count > 0:
                print(f"    -> 🟢 DETECTED UNBLOCKS: +{newly_unblocked_count} new seats!")
                
                if not is_first_run:
                    if newly_unblocked_count >= 1: # Alert on any unblock
                        rows_str = ", ".join(sorted(unblocked_rows_list))
                        human_date = humanize_date(s_date)
                        
                        msg = (
                            f"[{newly_unblocked_count}] BND DOLBY.\n"
                            f"{rows_str} rows unblocked for #SpiderManBrandNewDay at ALLU Cinemas Dolby Screen.\n\n"
                            f"{human_date}, {s_time}"
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
                print("    -> ⚪ No changes detected.")

        if deltas:
            print("\n[STATE] Cycle finished. Changes detected, merging and saving to Git...")
            state = save_state(deltas, f"ALLU state update cycle {cycle_count}")
        else:
            print("\n[STATE] Cycle finished. No changes detected.")
            
        if is_first_run:
            is_first_run = False
            print("[STATE] First run baseline has been successfully established in state_alus.json.")
            
        cycle_count += 1
        
    print("\n🏁 Time limit reached (5h 55m). Gracefully shutting down.")

if __name__ == "__main__":
    main()
