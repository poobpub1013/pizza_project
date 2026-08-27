# Pizza Sales — Mini Data Warehouse & Analytics Dashboard

วิเคราะห์ยอดขายร้านพิซซ่าปี 2015 ตั้งแต่ข้อมูลต้นทางจนถึง Dashboard
โดยรวมข้อมูลยอดขายจากระบบ POS เข้ากับข้อมูลสภาพอากาศและปฏิทินวันหยุดจาก REST API

---

## กระบวนการทั้งวงจร

```
01_Raw_Data/            02_ETL/                  03_Data_Warehouse/       ผลลัพธ์
─────────────           ─────────                ──────────────────       ──────────
pizza_sales/*.csv  ┐    fetch_sources.py    ┐    pizza_dw.duckdb     ┐    index.html
weather/*.json     ├──▶ etl_pipeline.ipynb  ├──▶ csv/*.csv           ├──▶ 04_Dashboard/
holidays/*.json    ┘                        ┘                        ┘      Pizza_Analysis.ipynb
```

| ขั้น | ไฟล์ | ทำอะไร |
|------|------|--------|
| Extract | `02_ETL/fetch_sources.py` | ดึงข้อมูลสภาพอากาศและวันหยุดจาก REST API มาเก็บเป็น raw JSON |
| ETL | `02_ETL/etl_pipeline.ipynb` | Extract → Clean → Transform → Integrate → Load เข้า DuckDB |
| วิเคราะห์ | `04_Dashboard/Pizza_Analysis.ipynb` | EDA, Feature Engineering, Machine Learning |
| นำเสนอ | `index.html` | Dashboard เชิงโต้ตอบ 8 หน้าจอ เผยแพร่ผ่าน GitHub Pages |
| สร้าง Dashboard | `pipeline/` | เทมเพลต สคริปต์ฝึกโมเดล และชุดทดสอบ |

---

## วิธีรัน

```bash
pip install pandas duckdb jupyter matplotlib scikit-learn nbformat

# 1. ดึงข้อมูลจาก API (ทำครั้งเดียวพอ ข้อมูลถูก commit ไว้แล้ว)
python 02_ETL/fetch_sources.py

# 2. สร้างคลังข้อมูล
jupyter nbconvert --to notebook --execute --inplace 02_ETL/etl_pipeline.ipynb

# 3. วิเคราะห์
jupyter notebook 04_Dashboard/Pizza_Analysis.ipynb
```

เปิด Dashboard โดยดับเบิลคลิก `index.html` ได้เลย ไม่ต้องรันเซิร์ฟเวอร์

> `index.html` ถูก **สร้างจากเทมเพลต** ไม่ควรแก้ไฟล์นี้โดยตรง
> ให้แก้ที่ `pipeline/dashboard_template.html` แล้วรัน `pipeline/ml_train_v3.py`
> จากนั้นตรวจด้วย `python pipeline/test_stocking.py`

> ไฟล์อยู่ที่รากของโปรเจกต์เพราะ GitHub Pages ให้บริการจากรากหรือ `/docs` เท่านั้น
> เมื่ออัปขึ้น Google Drive ให้คัดลอกไฟล์นี้เข้าโฟลเดอร์ `04_Dashboard/` ด้วย

> `etl_pipeline.ipynb` เป็น **full refresh** รันซ้ำกี่ครั้งก็ได้ผลเหมือนเดิม
> และไม่แก้ไฟล์ใน `01_Raw_Data/` ซึ่งถือเป็น read-only

---

## แหล่งข้อมูล

| # | แหล่ง | รูปแบบ | ขนาด |
|---|-------|--------|------|
| 1 | Pizza Place Sales (Maven Analytics) | CSV เชิงสัมพันธ์ 4 ตาราง | 48,620 รายการ / 21,350 บิล |
| 2 | [Open-Meteo](https://open-meteo.com/) Historical Archive | Nested JSON ผ่าน REST API | 8,760 ชั่วโมง + 365 วัน |
| 3 | [Nager.Date](https://date.nager.at/) Public Holidays | Nested JSON ผ่าน REST API | 16 รายการ / 13 วันที่ |

ทั้งสอง API เป็นบริการฟรี ไม่ต้องใช้ API key
รายละเอียดและปัญหาคุณภาพข้อมูลที่พบทั้งหมดอยู่ใน [`01_Raw_Data/SOURCES.md`](01_Raw_Data/SOURCES.md)

> **สมมติฐาน** — ข้อมูลต้นทางไม่ระบุที่ตั้งร้าน โครงการกำหนดให้ร้านอยู่ที่ **ชิคาโก**
> เพื่อให้ดึงข้อมูลสภาพอากาศได้ ผลการวิเคราะห์ด้านอากาศจึงอยู่บนสมมติฐานนี้

---

## Star Schema

**Grain ของ Fact:** หนึ่งแถว = พิซซ่าหนึ่งชนิดหนึ่งขนาด ในหนึ่งบิล

| ตาราง | แถว | คำอธิบาย |
|-------|-----|----------|
| `fact_sales_line` | 48,620 | Transaction fact — measures: `quantity`, `unit_price`, `line_revenue` |
| `dim_date` | 365 | ปฏิทินเต็มปี รวมวันที่ร้านปิด พร้อม `is_holiday`, `is_trading_day`, `season` |
| `dim_time` | 24 | ชั่วโมงของวัน พร้อม `daypart` |
| `dim_pizza` | 96 | เมนู × ขนาด แบนรวมหมวดหมู่ไว้ในตารางเดียว |
| `dim_weather` | 23 | Mini-dimension สภาพอากาศแบบจัดช่วง |
| `dim_ingredient` | 65 | วัตถุดิบแต่ละชนิด |
| `bridge_pizza_ingredient` | 181 | ตารางเชื่อมแบบหลายต่อหลาย |

`dim_date` สร้างจาก**ปฏิทินเต็ม 365 วัน** ไม่ใช่จากวันที่ที่มียอดขาย
เพราะร้านปิด 7 วัน ถ้าสร้างจากข้อมูลขายเท่านั้นวันที่ปิดจะหายไปจากมิติ
และ Dashboard จะแสดงไม่ได้ว่าวันนั้นขายได้ศูนย์

> ⚠️ `bridge_pizza_ingredient` ใช้ตอบคำถามเรื่องวัตถุดิบเท่านั้น
> **ห้ามนำมารวมยอดขาย** เพราะจะนับซ้ำตามจำนวนวัตถุดิบในแต่ละเมนู

---

## ตัวเลขควบคุม

ใช้ยืนยันว่าข้อมูลผ่าน ETL แล้วไม่ตกหล่นหรือถูกนับซ้ำ
`etl_pipeline.ipynb` ตรวจค่าเหล่านี้อัตโนมัติทุกครั้งที่รัน หากไม่ตรงจะหยุดทันที

| รายการ | ค่าที่ต้องได้ |
|--------|---------------|
| Total Revenue | $817,860.05 |
| Total Quantity Sold | 49,574 |
| Number of Orders | 21,350 |
| แถวใน Fact Table | 48,620 |
| วันทำการ | 358 จาก 365 |

---

## ปัญหาคุณภาพข้อมูลที่จัดการแล้ว

| ปัญหา | วิธีแก้ |
|-------|---------|
| `pizza_types.csv` ไม่ใช่ UTF-8 (byte `0x91`) | อ่านด้วย `encoding='cp1252'` |
| Open-Meteo คืนเป็น columnar arrays | Transpose เป็น row-oriented |
| หน่วยเมตริก (°C, mm, cm, km/h) | แปลงเป็น °F, inch, mph |
| วันที่ 3 รูปแบบจาก 3 แหล่ง | ระบุ format string ชัดเจนทุกจุด |
| วันหยุดซ้ำ 16 แถว / 13 วันที่ | Deduplicate ก่อน join ไม่งั้น Fact บานปลาย |
| `weather_code` เป็นรหัส WMO | Join ตารางอ้างอิงเพื่อถอดความหมาย |
| API ใช้ UTC offset คงที่ ไม่ขยับตาม DST | บันทึกเป็นข้อจำกัด ไม่ปรับแก้ข้อมูล |

ทุกข้อบันทึกจำนวนแถวก่อนและหลังไว้ใน Audit Log ท้าย `etl_pipeline.ipynb`

---

## เอกสารประกอบ

- [`Report/01_Objectives.md`](Report/01_Objectives.md) — Business Problem, Stakeholders, วัตถุประสงค์, ขอบเขต
- [`Report/02_Questions_and_Measures.md`](Report/02_Questions_and_Measures.md) — Business Questions 8 ข้อ และ Measures 7 ตัว
- [`01_Raw_Data/SOURCES.md`](01_Raw_Data/SOURCES.md) — รายละเอียดแหล่งข้อมูลและปัญหาคุณภาพ
- [`01_Raw_Data/data_dictionary.csv`](01_Raw_Data/data_dictionary.csv) — คำอธิบายฟิลด์ของข้อมูลต้นทาง
- `Report/รายงานโครงการ_Mini_DW_Pizza.pdf` — รายงานฉบับสมบูรณ์ 15 หน้า

> โฟลเดอร์ `05_AI_Usage_Log/` (บันทึกการใช้ Generative AI ตามที่โจทย์กำหนด)
> เก็บแยกไว้ในโฟลเดอร์ Google Drive ของกลุ่ม ไม่ได้รวมอยู่ใน repository นี้
