import os
import re
import requests
import pdfplumber
from bs4 import BeautifulSoup
from io import BytesIO
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Cambridge Timetable API",
    description="Scrapes and serves exam timetable data for CIE",
    version="1.0.0"
)

# Enable CORS for Flutter app calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://www.cambridgeinternational.org/exam-administration/cambridge-exams-officers-guide/phase-1-preparation/timetabling-exams/exam-timetables/"

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
        
        season = f"{month} {year}"
        zone = f"Zone {zone_num}"
        return zone, season
    
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
                                syllabus_name = cleaned_row[0]
                                code = cleaned_row[1]
                                field_a = cleaned_row[2]
                                field_b = cleaned_row[3]
                                session = cleaned_row[4]

                                if re.match(duration_pattern, field_a, re.IGNORECASE):
                                    duration = field_a
                                    date = field_b
                                else:
                                    date = field_a
                                    duration = field_b

                                extracted_data.append({
                                    "id": current_id,
                                    "zone": zone,
                                    "season": season,
                                    "syllabus_name": syllabus_name,
                                    "code": code,
                                    "date": date,
                                    "duration": duration,
                                    "session": session
                                })
                                current_id += 1
    except Exception as e:
        print(f"Error processing {pdf_url}: {e}")

    return extracted_data, current_id

def generate_timetable_data():
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

@app.get("/")
def health_check():
    return {"status": "online", "message": "Cambridge Timetable API is running."}

@app.get("/timetable")
def get_timetable():
    data = generate_timetable_data()
    return {
        "status": "success",
        "count": len(data),
        "data": data
    }
@app.get("/test")
def get_test_timetable():
    mock_data = [
        {
            "id": 1,
            "zone": "Zone 4",
            "season": "June 2026",
            "syllabus_name": "Mathematics",
            "code": "9709/12",
            "date": "15 May 2026",
            "duration": "1h 50m",
            "session": "AM"
        },
        {
            "id": 2,
            "zone": "Zone 4",
            "season": "June 2026",
            "syllabus_name": "Physics",
            "code": "9702/22",
            "date": "20 May 2026",
            "duration": "1h 15m",
            "session": "PM"
        },
        {
            "id": 3,
            "zone": "Zone 4",
            "season": "June 2026",
            "syllabus_name": "Computer Science",
            "code": "9618/12",
            "date": "22 May 2026",
            "duration": "1h 30m",
            "session": "AM"
        }
    ]
    return {
        "status": "success",
        "count": len(mock_data),
        "data": mock_data
    }