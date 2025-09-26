from fastapi import FastAPI, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session
import datetime, httpx, csv, io
from typing import Optional
import psycopg

DATABASE_URL = "postgresql://weather_postgresql_advanced_user:z7errdZxW5cnUXq3Hs2B7h5mS9FVkF7x@dpg-d3b6ss0gjchc73f9vodg-a.oregon-postgres.render.com/weather_postgresql_advanced"
OPENWEATHER_API_KEY = "f33a92d1f423e75d96185317f09987f7"  # For live weather fetching if needed
templates = Jinja2Templates(directory="templates")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
app = FastAPI()

class WeatherRecordDB(Base):
    __tablename__ = "weather_records"
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    temperature_min = Column(Float, nullable=True)
    temperature_max = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

async def geocode_location(location: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "json", "limit": 1}
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, params=params, timeout=10)
            data = res.json()
            if not data:
                return None
            return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception:
            return None

async def fetch_weather(lat: float, lon: float):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, timeout=10)
            data = res.json()
            if res.status_code == 200 and "main" in data:
                temp_min = data["main"].get("temp_min")
                temp_max = data["main"].get("temp_max")
                return temp_min, temp_max
        except Exception:
            return None, None
    return None, None

def parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None

def get_youtube_video_links(location, max_results=3):
    query = location.replace(" ", "+")
    return [
        f"https://www.youtube.com/results?search_query={query}",
        f"https://www.youtube.com/results?search_query={query}+weather",
        f"https://www.youtube.com/results?search_query={query}+tour"
    ][:max_results]

@app.get("/", response_class=HTMLResponse)
def list_records(request: Request, db: Session = Depends(get_db)):
    records = db.query(WeatherRecordDB).order_by(WeatherRecordDB.created_at.desc()).all()
    for rec in records:
        rec.youtube_links = get_youtube_video_links(rec.location)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "records": records,
    })

@app.get("/create", response_class=HTMLResponse)
def create_form(request: Request):
    return templates.TemplateResponse("create.html", {"request": request})

@app.post("/create")
async def create_record(
    request: Request, location: str = Form(...),
    start_date: str = Form(...), end_date: str = Form(...),
    temperature_min: Optional[str] = Form(None),
    temperature_max: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    try:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_dt < start_dt:
            return templates.TemplateResponse("create.html", {
                "request": request,
                "error": "End date must be on or after start date",
                "location": location, "start_date": start_date, "end_date": end_date,
                "temperature_min": temperature_min, "temperature_max": temperature_max,
            })
    except ValueError:
        return templates.TemplateResponse("create.html", {
            "request": request,
            "error": "Invalid date format (use YYYY-MM-DD)",
            "location": location, "start_date": start_date, "end_date": end_date,
            "temperature_min": temperature_min, "temperature_max": temperature_max,
        })

    coords = await geocode_location(location)
    if coords is None:
        return templates.TemplateResponse("create.html", {
            "request": request,
            "error": "Could not find location",
            "location": location, "start_date": start_date, "end_date": end_date,
            "temperature_min": temperature_min, "temperature_max": temperature_max,
        })

    temp_min_val = parse_optional_float(temperature_min)
    temp_max_val = parse_optional_float(temperature_max)
    live_min, live_max = await fetch_weather(coords[0], coords[1])
    if live_min is not None and live_max is not None:
        temp_min_val = live_min
        temp_max_val = live_max

    record = WeatherRecordDB(
        location=location, latitude=coords[0], longitude=coords[1],
        start_date=start_dt, end_date=end_dt,
        temperature_min=temp_min_val, temperature_max=temp_max_val,
    )
    db.add(record)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/edit/{record_id}", response_class=HTMLResponse)
def edit_form(record_id: int, request: Request, db: Session = Depends(get_db)):
    record = db.query(WeatherRecordDB).filter(WeatherRecordDB.id == record_id).first()
    if record is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    record.youtube_links = get_youtube_video_links(record.location)
    return templates.TemplateResponse("edit.html", {
        "request": request,
        "record": record,
    })

@app.post("/edit/{record_id}")
async def edit_record(
    record_id: int, request: Request, location: str = Form(...),
    start_date: str = Form(...), end_date: str = Form(...),
    temperature_min: Optional[str] = Form(None),
    temperature_max: Optional[str] = Form(None), db: Session = Depends(get_db),
):
    record = db.query(WeatherRecordDB).filter(WeatherRecordDB.id == record_id).first()
    if record is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    try:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_dt < start_dt:
            record.youtube_links = get_youtube_video_links(record.location)
            return templates.TemplateResponse("edit.html", {
                "request": request,
                "record": record,
                "error": "End date must be on or after start date"
            })
    except ValueError:
        record.youtube_links = get_youtube_video_links(record.location)
        return templates.TemplateResponse("edit.html", {
            "request": request,
            "record": record,
            "error": "Invalid date format (use YYYY-MM-DD)"
        })

    coords = await geocode_location(location)
    if coords is None:
        record.youtube_links = get_youtube_video_links(record.location)
        return templates.TemplateResponse("edit.html", {
            "request": request,
            "record": record,
            "error": "Could not find location"
        })

    temp_min_val = parse_optional_float(temperature_min)
    temp_max_val = parse_optional_float(temperature_max)
    live_min, live_max = await fetch_weather(coords[0], coords[1])
    if live_min is not None and live_max is not None:
        temp_min_val = live_min
        temp_max_val = live_max

    record.location = location
    record.latitude = coords[0]
    record.longitude = coords[1]
    record.start_date = start_dt
    record.end_date = end_dt
    record.temperature_min = temp_min_val
    record.temperature_max = temp_max_val
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/delete/{record_id}")
def delete_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(WeatherRecordDB).filter(WeatherRecordDB.id == record_id).first()
    if record:
        db.delete(record)
        db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/export/csv")
def export_csv(db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Location", "Latitude", "Longitude", "Start Date",
        "End Date", "Temp Min", "Temp Max", "Created At"])
    records = db.query(WeatherRecordDB).all()
    for r in records:
        writer.writerow([
            r.id, r.location, r.latitude, r.longitude,
            r.start_date, r.end_date, r.temperature_min,
            r.temperature_max, r.created_at])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=weather.csv"})

@app.get("/export/json", response_class=JSONResponse)
def export_json(db: Session = Depends(get_db)):
    records = db.query(WeatherRecordDB).all()
    data = [{
        "id": r.id,
        "location": r.location,
        "latitude": r.latitude,
        "longitude": r.longitude,
        "start_date": str(r.start_date),
        "end_date": str(r.end_date),
        "temperature_min": r.temperature_min,
        "temperature_max": r.temperature_max,
        "created_at": str(r.created_at),
    } for r in records]
    return JSONResponse(content=data)

@app.get("/export/markdown")
def export_md(db: Session = Depends(get_db)):
    records = db.query(WeatherRecordDB).all()
    md = ""
    for r in records:
        md += f"### Weather Record {r.id}\n"
        md += f"- **Location**: {r.location} ({r.latitude}, {r.longitude})\n"
        md += f"- **Dates**: {r.start_date} to {r.end_date}\n"
        md += f"- **Temp Min/Max**: {r.temperature_min or ''}/{r.temperature_max or ''}\n"
        md += f"- **Created At**: {r.created_at}\n\n"
    return Response(md, media_type="text/markdown",
                    headers={"Content-Disposition": "attachment; filename=weather.md"})



