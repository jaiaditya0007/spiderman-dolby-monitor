import os
import json
import time
import subprocess
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from curl_cffi import requests as cffi_requests

# --- TARGET SHOW CONFIGURATION ---
CINEMA_ID = "1039848"
TARGET_DATES = ["2026-08-22", "2026-08-23"]
TARGET_CONTENT_ID = 214275
MOVIE_TITLE = "Irumudi"
THEATRE_NAME = "Vimal 70MM"

STATE_FILE = "district.json"
NTFY_TOPIC = "alusdolby"
CHECK_INTERVAL_SECONDS = 15
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60)  # 5 hours 55 minutes

IST_OFFSET = timedelta(hours=5, minutes=30)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

def quiet_git_pull():
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def read_local_state():
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

def save_state(deltas, commit_msg="Update District target show state"):
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
                print("[GIT] Successfully pushed state to repository.")
                return latest_state
            else:
                print(f"[GIT] Push attempt {attempt+1} failed. Retrying merge...")
                time.sleep(2)
        else:
            print("[GIT] Merged state is identical. Nothing to push.")
            return latest_state

    print("[GIT] Failed to push state after 3 attempts.")
    return latest_state

def trigger_ntfy(title_ascii, message, click_url):
    print(f"\n[!] ALERTING VIA NTFY ({NTFY_TOPIC}):\n{message}")
    safe_title = title_ascii.encode("ascii", "ignore").decode("ascii")
    headers = {
        "Priority": "urgent",
        "Title": safe_title,
        "Tags": "movie_camera,ticket,rotating_light",
        "Click": click_url,
        "Actions": f"view, Book on District, {click_url}"
    }
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8'),
            headers=headers,
            timeout=10
        )
        print(f"    -> Ntfy notification sent! Status: {resp.status_code}")
    except Exception as e:
        print(f"    -> Ntfy ping failed: {e}")

def format_show_time_ist(raw_time):
    try:
        dt_utc = datetime.fromisoformat(raw_time)
        dt_ist = dt_utc + IST_OFFSET
        return dt_ist.strftime("%I:%M %p")
    except Exception:
        return raw_time

def build_direct_booking_url(session, target_date):
    enc_sid = session.get("encSessionId")
    fid = session.get("fid", "b0meltruw2").lower()
    cid = TARGET_CONTENT_ID
    if enc_sid:
        return (
            f"https://www.district.in/movies/seat-layout/{fid}?"
            f"encsessionid={enc_sid}&fromdate={target_date}&freeseating=false"
            f"&fromsessions=true&type=CINEMAS&contentid={cid}"
        )
    return f"https://www.district.in/movies/theatre-in-vizag-CD{CINEMA_ID}?fromdate={target_date}"

def fetch_sessions_for_date(target_date):
    """Extracts all Irumudi sessions for a specific date from District SSR payload."""
    url = f"https://www.district.in/movies/theatre-in-vizag-CD{CINEMA_ID}?fromdate={target_date}"
    try:
        resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if resp.status_code != 200:
            print(f"    -> [{target_date}] HTTP Error: {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        script_tag = soup.find("script", id="__NEXT_DATA__")
        if not script_tag:
            return []

        data = json.loads(script_tag.string)
        movies_state = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("movies", {})
        cinema_sessions = movies_state.get("cinemaSessions", {})

        irumudi_sessions = []
        for date_key, cinema_data in cinema_sessions.items():
            theatre_label = cinema_data.get("cinemaName") or THEATRE_NAME
            for m in cinema_data.get("arrangedSessions", []):
                code = m.get("contentId") or m.get("entityCode")
                name = str(m.get("entityName") or m.get("label") or "").lower()

                if code == TARGET_CONTENT_ID or str(code) == str(TARGET_CONTENT_ID) or "irumudi" in name:
                    for s in m.get("sessions", []):
                        s["theatreName"] = theatre_label
                        s["targetDate"] = target_date
                        irumudi_sessions.append(s)

        # Deduplicate by sid
        unique = {}
        for s in irumudi_sessions:
            sid = s.get("sid")
            if sid and sid not in unique:
                unique[sid] = s

        return sorted(unique.values(), key=lambda x: x.get("showTime", ""))
    except Exception as e:
        print(f"    -> [{target_date}] Extraction error: {e}")
        return []

def main():
    start_time = time.time()

    print("==================================================")
    print(f" DISTRICT SEAT MONITOR: {MOVIE_TITLE}")
    print(f" Cinema: {THEATRE_NAME}")
    print(f" Dates Monitored: {', '.join(TARGET_DATES)}")
    print(f" Topic: https://ntfy.sh/{NTFY_TOPIC}")
    print("==================================================")

    state = load_state()
    is_first_run = len(state) == 0
    cycle_count = 1

    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n==================================================")
        print(f" CYCLE {cycle_count} @ {datetime.now().strftime('%H:%M:%S')}")
        print(f"==================================================")

        deltas = {}
        all_shows_count = 0

        for target_date in TARGET_DATES:
            shows = fetch_sessions_for_date(target_date)
            all_shows_count += len(shows)

            if not shows:
                print(f"[{target_date}] No active sessions found.")
                continue

            print(f"\n--- {target_date} ({len(shows)} show(s) found) ---")

            for index, session in enumerate(shows, 1):
                s_id = session.get("sid")
                raw_time = session.get("showTime", target_date)
                show_time_str = format_show_time_ist(raw_time)
                audi_name = session.get("audi", "VIMAL 70MM")
                theatre_name = session.get("theatreName", THEATRE_NAME)
                booking_url = build_direct_booking_url(session, target_date)

                categories = {}
                current_total = 0

                for area in session.get("areas", []):
                    label = area.get("label", "Standard")
                    avail = int(area.get("sAvail", 0))
                    total = int(area.get("sTotal", 0))
                    price = area.get("price", 0)
                    current_total += avail
                    categories[label] = {
                        "available": avail,
                        "total": total,
                        "price": price
                    }

                status_badge = " SOLD OUT" if current_total == 0 else f" {current_total} LEFT"
                print(f"\n[{index}/{len(shows)}] {audi_name} @ {show_time_str} IST [{status_badge}]")
                for cat, cdata in categories.items():
                    print(f"       • {cat:<22} (Rs.{cdata['price']}): {cdata['available']}/{cdata['total']} left")

                if s_id not in state:
                    state[s_id] = {
                        "date": target_date,
                        "time": show_time_str,
                        "audi": audi_name,
                        "theatre": theatre_name,
                        "total": 0,
                        "categories": {}
                    }

                previous_total = state[s_id].get("total", 0)
                previous_categories = state[s_id].get("categories", {})

                newly_unblocked_count = 0
                unblocked_details = []

                for cat_name, cur_stats in categories.items():
                    prev_avail = previous_categories.get(cat_name, {}).get("available", 0)
                    diff = cur_stats["available"] - prev_avail
                    if diff > 0:
                        newly_unblocked_count += diff
                        unblocked_details.append(f"{cat_name} (+{diff})")

                if newly_unblocked_count > 0 and not is_first_run:
                    print(f"    ->  UNBLOCKS DETECTED: +{newly_unblocked_count} new seats in {audi_name} @ {show_time_str} ({target_date})!")
                    details_str = ", ".join(unblocked_details)
                    breakdown_lines = [f"• {cat} (Rs.{d['price']}): {d['available']} available" for cat, d in categories.items()]

                    safe_title = f"[{target_date} {show_time_str}] {newly_unblocked_count} SEATS OPEN: {audi_name}"
                    msg = (
                        f"[{newly_unblocked_count}] SEATS UNBLOCKED!\n"
                        f"Movie: {MOVIE_TITLE}\n"
                        f"Cinema: {theatre_name}\n"
                        f"Date: {target_date}\n"
                        f"Screen/Show: {audi_name} @ {show_time_str} IST\n"
                        f"Unblocked: {details_str}\n"
                        f"Total Available: {current_total}\n\n"
                        f"Category Breakdown:\n" + "\n".join(breakdown_lines) + f"\n\n"
                        f"Book: {booking_url}"
                    )
                    trigger_ntfy(safe_title, msg, booking_url)

                    state[s_id] = {
                        "date": target_date,
                        "time": show_time_str,
                        "audi": audi_name,
                        "theatre": theatre_name,
                        "total": current_total,
                        "categories": categories
                    }
                    deltas[s_id] = state[s_id]

                elif current_total < previous_total:
                    print(f"    ->  Seats booked. Dropped from {previous_total} down to {current_total}.")
                    state[s_id] = {
                        "date": target_date,
                        "time": show_time_str,
                        "audi": audi_name,
                        "theatre": theatre_name,
                        "total": current_total,
                        "categories": categories
                    }
                    deltas[s_id] = state[s_id]

                else:
                    state[s_id] = {
                        "date": target_date,
                        "time": show_time_str,
                        "audi": audi_name,
                        "theatre": theatre_name,
                        "total": current_total,
                        "categories": categories
                    }
                    if is_first_run:
                        deltas[s_id] = state[s_id]
                    print("    ->  No changes detected.")

        if is_first_run or deltas:
            print(f"\n[STATE] Syncing state baseline to {STATE_FILE}...")
            state = save_state(deltas if deltas else state, f"Vimal 70MM update cycle {cycle_count}")
            if is_first_run:
                is_first_run = False
                print(f"[STATE] Initial baseline established for all {all_shows_count} shows across target dates!")
        else:
            print("\n[STATE] Cycle finished. No unblocks detected.")

        cycle_count += 1
        time.sleep(CHECK_INTERVAL_SECONDS)

    print("\nTime limit reached (5h 55m). Gracefully shutting down.")

if __name__ == "__main__":
    main()
