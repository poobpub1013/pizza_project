"""
Extract layer: ดึงข้อมูลจากแหล่งภายนอก 2 แหล่ง มาเก็บเป็น raw JSON

  Source #2  Open-Meteo Historical Weather Archive (REST API, nested JSON)
  Source #3  Nager.Date Public Holidays          (REST API, nested JSON)

สคริปต์นี้ idempotent: รันซ้ำได้เรื่อยๆ ผลลัพธ์เหมือนเดิม ไม่ต้องแก้ไฟล์ด้วยมือ
เก็บ response ดิบไว้ตามที่ API ส่งมาทุกตัวอักษร ไม่แปลงอะไรทั้งสิ้น
(การแปลง/ทำความสะอาด ไปทำที่ขั้น Transform)

    python 02_ETL/fetch_sources.py

ทั้งสอง API เป็นบริการฟรี ไม่ต้องใช้ API key
"""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# console ของ Windows default เป็น cp1252 พิมพ์ข้อความไทยแล้วสคริปต์พังทั้งตัว
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# ปีของข้อมูลยอดขาย (orders.csv มีตั้งแต่ 2015-01-01 ถึง 2015-12-31)
YEAR = 2015
START_DATE = f"{YEAR}-01-01"
END_DATE = f"{YEAR}-12-31"

# สมมติฐาน: ร้านพิซซ่าตั้งอยู่ที่ชิคาโก
# dataset ต้นทางไม่ได้ระบุพิกัดร้าน จึงต้องตั้งสมมติฐานและระบุไว้ในรายงาน
# เลือกชิคาโกเพราะมีสี่ฤดูชัดเจน (หิมะตกหนักหน้าหนาว ร้อนจัดหน้าร้อน)
# ทำให้เห็นความสัมพันธ์ระหว่างอากาศกับยอดขายได้ชัดกว่าเมืองที่อากาศคงที่
# ถ้ากลุ่มอยากเปลี่ยนเมือง แก้แค่ 4 บรรทัดนี้แล้วรันใหม่
CITY_NAME = "Chicago"
CITY_TIMEZONE = "America/Chicago"
LATITUDE = 41.8781
LONGITUDE = -87.6298

COUNTRY_CODE = "US"

# หมายเหตุเรื่องหน่วย: ไม่ระบุ unit parameter = ใช้ค่า default ของ API (metric)
# ตั้งใจดึงเป็น metric ทั้งที่ธุรกิจเป็นอเมริกา (ราคาเป็น USD คนอเมริกันใช้ °F)
# เพื่อให้ขั้น Transform มีงานแปลงหน่วยจริง ไม่ใช่แกล้งสร้างปัญหา
HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "wind_speed_10m",
]
DAILY_VARS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "precipitation_hours",
    "wind_speed_10m_max",
    "sunrise",
    "sunset",
]

RAW_DIR = Path(__file__).resolve().parent.parent / "01_Raw_Data"
WEATHER_DIR = RAW_DIR / "weather"
HOLIDAY_DIR = RAW_DIR / "holidays"

REQUEST_TIMEOUT = 120
MAX_RETRIES = 4


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def fetch_json(url: str, label: str) -> dict | list:
    """ดึง JSON พร้อม retry แบบ exponential backoff

    API สาธารณะมี rate limit และบางครั้งตอบ 5xx ชั่วคราว
    ถ้าไม่ retry สคริปต์จะพังกลางคัน = รันซ้ำไม่ได้จริง
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "MiniDW-PizzaProject/1.0"}
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                payload = resp.read().decode("utf-8")
            return json.loads(payload)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"{label}: ดึงข้อมูลไม่สำเร็จหลังลอง {MAX_RETRIES} ครั้ง") from exc
            wait = 2**attempt
            print(f"  ! {label} ล้มเหลว (ครั้งที่ {attempt}): {exc} -- รอ {wait}s แล้วลองใหม่")
            time.sleep(wait)
    raise AssertionError("unreachable")


def save_json(obj, path: Path) -> dict:
    """เขียนไฟล์ JSON แล้วคืน metadata สำหรับ manifest"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "file": path.name,
        "bytes": len(text.encode("utf-8")),
        "sha256": digest,
    }


# --------------------------------------------------------------------------
# Source #2 -- Open-Meteo Historical Weather Archive
# --------------------------------------------------------------------------


def fetch_weather() -> dict:
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&start_date={START_DATE}&end_date={END_DATE}"
        f"&hourly={','.join(HOURLY_VARS)}"
        f"&daily={','.join(DAILY_VARS)}"
        f"&timezone={CITY_TIMEZONE}"
    )
    print(f"[2/3] Open-Meteo Archive -- {CITY_NAME} {START_DATE}..{END_DATE}")
    data = fetch_json(url, "open-meteo")

    n_hourly = len(data["hourly"]["time"])
    n_daily = len(data["daily"]["time"])
    print(f"      hourly {n_hourly} ชั่วโมง | daily {n_daily} วัน")

    out = WEATHER_DIR / f"openmeteo_archive_{CITY_NAME.lower()}_{YEAR}.json"
    meta = save_json(data, out)
    meta.update(
        {
            "source": "Open-Meteo Historical Weather Archive",
            "url": url,
            "license": "CC-BY-4.0 (non-commercial use, no API key required)",
            "hourly_records": n_hourly,
            "daily_records": n_daily,
            "units_as_returned": {
                "hourly": data.get("hourly_units"),
                "daily": data.get("daily_units"),
            },
        }
    )
    return meta


# --------------------------------------------------------------------------
# Source #3 -- Nager.Date Public Holidays
# --------------------------------------------------------------------------


def fetch_holidays() -> dict:
    url = f"https://date.nager.at/api/v3/PublicHolidays/{YEAR}/{COUNTRY_CODE}"
    print(f"[3/3] Nager.Date -- วันหยุดราชการ {COUNTRY_CODE} ปี {YEAR}")
    data = fetch_json(url, "nager.date")
    print(f"      พบวันหยุด {len(data)} รายการ")

    out = HOLIDAY_DIR / f"nager_publicholidays_{COUNTRY_CODE.lower()}_{YEAR}.json"
    meta = save_json(data, out)
    meta.update(
        {
            "source": "Nager.Date Public Holiday API",
            "url": url,
            "license": "MIT / open data, no API key required",
            "records": len(data),
        }
    )
    return meta


# --------------------------------------------------------------------------
# Reference -- WMO weather interpretation codes
# --------------------------------------------------------------------------

# Open-Meteo คืน weather_code เป็นตัวเลข WMO 4677 ซึ่งอ่านเองไม่รู้เรื่อง
# (73 = snow, 95 = thunderstorm) ต้องมี lookup ถึงจะเอาไปทำ dimension ได้
# ตารางนี้คัดมาจากเอกสาร Open-Meteo -- เป็นข้อมูลอ้างอิงคงที่ ไม่มี API ให้ดึง
WMO_CODES = {
    0: ("Clear sky", "Clear"),
    1: ("Mainly clear", "Clear"),
    2: ("Partly cloudy", "Cloudy"),
    3: ("Overcast", "Cloudy"),
    45: ("Fog", "Fog"),
    48: ("Depositing rime fog", "Fog"),
    51: ("Drizzle: light", "Rain"),
    53: ("Drizzle: moderate", "Rain"),
    55: ("Drizzle: dense", "Rain"),
    56: ("Freezing drizzle: light", "Freezing"),
    57: ("Freezing drizzle: dense", "Freezing"),
    61: ("Rain: slight", "Rain"),
    63: ("Rain: moderate", "Rain"),
    65: ("Rain: heavy", "Rain"),
    66: ("Freezing rain: light", "Freezing"),
    67: ("Freezing rain: heavy", "Freezing"),
    71: ("Snow fall: slight", "Snow"),
    73: ("Snow fall: moderate", "Snow"),
    75: ("Snow fall: heavy", "Snow"),
    77: ("Snow grains", "Snow"),
    80: ("Rain showers: slight", "Rain"),
    81: ("Rain showers: moderate", "Rain"),
    82: ("Rain showers: violent", "Rain"),
    85: ("Snow showers: slight", "Snow"),
    86: ("Snow showers: heavy", "Snow"),
    95: ("Thunderstorm: slight or moderate", "Storm"),
    96: ("Thunderstorm with slight hail", "Storm"),
    99: ("Thunderstorm with heavy hail", "Storm"),
}


def write_wmo_lookup() -> dict:
    print("[1/3] เขียนตารางอ้างอิง WMO weather code")
    rows = [
        {"weather_code": code, "description": desc, "condition_group": group}
        for code, (desc, group) in sorted(WMO_CODES.items())
    ]
    out = WEATHER_DIR / "wmo_weather_codes.json"
    meta = save_json(rows, out)
    meta.update(
        {
            "source": "WMO code 4677 (คัดจากเอกสาร Open-Meteo)",
            "url": "https://open-meteo.com/en/docs",
            "records": len(rows),
        }
    )
    return meta


# --------------------------------------------------------------------------


def main() -> None:
    print(f"=== Extract external sources -- {CITY_NAME}, {YEAR} ===")
    files = [write_wmo_lookup(), fetch_weather(), fetch_holidays()]

    manifest = {
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "year": YEAR,
            "date_range": [START_DATE, END_DATE],
            "city": CITY_NAME,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "timezone": CITY_TIMEZONE,
            "country_code": COUNTRY_CODE,
        },
        "files": files,
    }
    save_json(manifest, RAW_DIR / "_extract_manifest.json")

    print("\n=== เสร็จสิ้น ===")
    for f in files:
        print(f"  {f['file']:<48} {f['bytes']:>9,} bytes  sha256:{f['sha256'][:12]}")
    print(f"\n  manifest -> {RAW_DIR / '_extract_manifest.json'}")


if __name__ == "__main__":
    main()
