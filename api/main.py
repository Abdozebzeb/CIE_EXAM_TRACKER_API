import os
import re
import json
import base64
import requests
import pdfplumber
from bs4 import BeautifulSoup
from io import BytesIO
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Cambridge Timetable API")

BASE_URL = "https://www.cambridgeinternational.org/exam-administration/cambridge-exams-officers-guide/phase-1-preparation/timetabling-exams/exam-timetables/"

# GitHub Config from Environment Variables
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # Format: "username/repo-name"
JSON_FILE_PATH = "timetable.json"

# --- HELPER FUNCTIONS FOR SCRAPING ---
def get_pdf_links():
    response = requests.get(BASE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().endswith('.pdf') and "checkpoint" not in href.lower():
            full_url = href if href.startswith('http') else "https://www.cambridgeinternational.org" + href
            links.append(full_url)
    return links

def parse_metadata_from_filename(url):
    filename = url.split('/')[-1]
    pattern = r"\d+-(\w+)-(\d{4})-zone-(\d+(?:-uk)?)-timetable\.pdf"
    match = re.search(pattern, filename, re.IGNORECASE)
    if match:
        month = match.group(1).capitalize()
        year = match.group(2)
        zone_num = match.group(3).upper().replace('-', ' ')
        return f"Zone {zone_num}", f"{month} {year}"
    return "Unknown Zone", "Unknown Season"

def process_pdf(pdf_url, zone, season, start_id):
    extracted_data = []
    current_id = start_id
    target_phrase = "syllabus view"
    duration_pattern = r'^(\d+h\s*\d+m|\d+h|\d+m)$'

    try:
        response = requests.get(pdf_url)
        with pdfplumber.open(BytesIO(response.content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                normalized_text = re.sub(r'\s+', ' ', text).lower()
                if target_phrase in normalized_text:
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            cleaned_row = [str(cell).strip().replace('\n', ' ') if cell is not None else "" for cell in row]
                            if not cleaned_row or any(h.lower() in cleaned_row[0].lower() for h in ["syllabus name", "syllabus/component"]):
                                continue
                            while cleaned_row and cleaned_row[-1] == "":
                                cleaned_row.pop()

                            if len(cleaned_row) >= 5:
                                field_a, field_b = cleaned_row[2], cleaned_row[3]
                                if re.match(duration_pattern, field_a, re.IGNORECASE):
                                    duration, date = field_a, field_b
                                else:
                                    date, duration = field_a, field_b

                                extracted_data.append({
                                    "id": current_id,
                                    "zone": zone,
                                    "season": season,
                                    "syllabus_name": cleaned_row[0],
                                    "code": cleaned_row[1],
                                    "date": date,
                                    "duration": duration,
                                    "session": cleaned_row[4]
                                })
                                current_id += 1
    except Exception as e:
        print(f"Error processing {pdf_url}: {e}")
    return extracted_data, current_id

def run_full_scraper():
    pdf_links = get_pdf_links()
    master_data = []
    global_id_counter = 1
    for link in pdf_links:
        zone, season = parse_metadata_from_filename(link)
        if zone != "Unknown Zone":
            data, next_id = process_pdf(link, zone, season, global_id_counter)
            master_data.extend(data)
            global_id_counter = next_id
    return master_data

# --- API ENDPOINTS ---

@app.get("/")
def health_check():
    return {"status": "online", "message": "Cambridge Timetable API"}

# 1. INSTANT ENDPOINT FOR FLUTTER APP
@app.get("/getstoredtimetable")
def get_stored_timetable():
    """Fetches the latest pre-parsed JSON stored in your repository in ~100ms."""
    if not GITHUB_REPO:
        raise HTTPException(status_code=500, detail="GITHUB_REPO environment variable not configured.")
    
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{JSON_FILE_PATH}"
    response = requests.get(raw_url)
    
    if response.statusCode == 200:
        data = response.json()
        return {
            "status": "success",
            "count": len(data),
            "data": data
        }
    else:
        # Fallback if timetable.json hasn't been generated yet
        return {"status": "error", "message": "Stored timetable JSON not found yet. Run /api/cron-update first."}

# 2. AUTOMATED CRON JOB (Runs daily at 00:00 UTC)
@app.get("/api/cron-update")
def cron_update_timetable():
    """Triggered automatically by Vercel every 24h to scrape PDFs and save JSON to GitHub."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise HTTPException(status_code=500, detail="Missing GITHUB_TOKEN or GITHUB_REPO env variables.")

    # Step A: Run Scraper
    new_data = run_full_scraper()
    json_content = json.dumps(new_data, indent=2, ensure_ascii=False)

    # Step B: Commit new JSON file to GitHub via API
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{JSON_FILE_PATH}"
    headers = {
        "Authorization": "Bearer " + GITHUB_TOKEN,
        "Accept": "application/vnd.github.v3+json"
    }

    # Get current file SHA if it exists (required by GitHub API to overwrite files)
    get_file = requests.get(url, headers=headers)
    sha = get_file.json().get("sha") if get_file.status_code == 200 else None

    # Step C: Prepare GitHub Push Payload
    encoded_content = base64.b64encode(json_content.encode('utf-8')).decode('utf-8')
    payload = {
        "message": "Automated 24h timetable.json update [Vercel Cron]",
        "content": encoded_content
    }
    if sha:
        payload["sha"] = sha

    # Save to Repository
    put_response = requests.put(url, headers=headers, json=payload)
    
    if put_response.status_code in [200, 201]:
        return {"status": "success", "message": "Scraped and stored timetable.json successfully!"}
    else:
        return {"status": "error", "details": put_response.json()}