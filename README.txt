================================================================================
  PIZZA SALES - MINI DATA WAREHOUSE & ANALYTICS DASHBOARD
  Group Assignment #1 : Data Warehouse and Business Intelligence
================================================================================

วิเคราะห์ยอดขายร้านพิซซ่าปีงบประมาณ 2015 ตั้งแต่ข้อมูลต้นทางจนถึง Dashboard
โดยรวมข้อมูลยอดขายจากระบบ POS เข้ากับข้อมูลสภาพอากาศและปฏิทินวันหยุดจาก REST API


--------------------------------------------------------------------------------
1. โครงสร้างโฟลเดอร์
--------------------------------------------------------------------------------

  01_Raw_Data/
      pizza_sales/        CSV ยอดขายจากระบบ POS จำนวน 4 ตาราง
      weather/            JSON สภาพอากาศรายชั่วโมงจาก Open-Meteo API
      holidays/           JSON วันหยุดราชการสหรัฐฯ จาก Nager.Date API
      data_dictionary.csv คำอธิบายฟิลด์ของข้อมูลต้นทาง
      SOURCES.md          รายละเอียดแหล่งข้อมูลและปัญหาคุณภาพที่พบทั้งหมด
      _extract_manifest.json  บันทึกการดึงข้อมูล พร้อมค่า SHA-256 ของทุกไฟล์

  02_ETL/
      fetch_sources.py    ดึงข้อมูลจาก REST API มาเก็บเป็น raw JSON
      etl_pipeline.ipynb  Extract > Clean > Transform > Integrate > Load

  03_Data_Warehouse/
      pizza_dw.duckdb     คลังข้อมูล Star Schema 7 ตาราง
      csv/                ตารางทั้งหมดในรูปแบบ CSV สำหรับ BI Tool ที่ต่อ DuckDB ไม่ได้

  04_Dashboard/
      Pizza_Analysis.ipynb         EDA, Feature Engineering, Machine Learning

  index.html
      Dashboard เชิงโต้ตอบ 8 หน้าจอ เผยแพร่ผ่าน GitHub Pages
      วางที่รากของโปรเจกต์เพราะ GitHub Pages ให้บริการจากรากหรือ /docs เท่านั้น
      เมื่ออัปขึ้น Google Drive ให้คัดลอกไฟล์นี้เข้าโฟลเดอร์ 04_Dashboard ด้วย

  pipeline/
      dashboard_template.html      เทมเพลตของ Dashboard (แก้ที่นี่ ไม่ใช่ index.html)
      ml_train_v3.py               ฝึกโมเดลและ generate index.html
      test_stocking.py             ชุดทดสอบตัวคำนวณวัตถุดิบ

  05_AI_Usage_Log/
      บันทึกการใช้ Generative AI จำนวน 9 prompt
      (เก็บในโฟลเดอร์ Google Drive ของกลุ่ม ไม่ได้รวมอยู่ใน repository)

  Report/
      รายงานโครงการ_Mini_DW_Pizza.pdf   รายงานฉบับสมบูรณ์ 15 หน้า (มีฉบับ Word ให้แก้ไขด้วย)
      report.html                        ไฟล์ต้นฉบับที่ใช้สร้าง PDF
      01_Objectives.md                   Business Problem, Stakeholders, วัตถุประสงค์
      02_Questions_and_Measures.md       Business Questions 8 ข้อ และ Measures 7 ตัว


--------------------------------------------------------------------------------
2. วิธีรัน
--------------------------------------------------------------------------------

  ติดตั้งไลบรารีที่ต้องใช้

      pip install pandas duckdb jupyter matplotlib scikit-learn nbformat

  ขั้นที่ 1 - ดึงข้อมูลจาก API (ทำครั้งเดียวพอ ข้อมูลถูกเก็บไว้ให้แล้ว)

      python 02_ETL/fetch_sources.py

  ขั้นที่ 2 - สร้างคลังข้อมูล

      jupyter nbconvert --to notebook --execute --inplace 02_ETL/etl_pipeline.ipynb

  ขั้นที่ 3 - วิเคราะห์ข้อมูล

      jupyter notebook 04_Dashboard/Pizza_Analysis.ipynb

  ขั้นที่ 4 - เปิด Dashboard

      ดับเบิลคลิกไฟล์ index.html
      เปิดด้วยเบราว์เซอร์ได้ทันที ไม่ต้องรันเซิร์ฟเวอร์

      หากต้องการแก้ Dashboard ให้แก้ที่ pipeline/dashboard_template.html
      แล้วรัน python pipeline/ml_train_v3.py เพื่อสร้าง index.html ใหม่
      จากนั้นตรวจด้วย python pipeline/test_stocking.py

  หมายเหตุ
      etl_pipeline.ipynb ทำงานแบบ full refresh รันซ้ำกี่ครั้งก็ได้ผลลัพธ์เหมือนเดิม
      และไม่แก้ไขไฟล์ใน 01_Raw_Data/ ซึ่งถือเป็น read-only


--------------------------------------------------------------------------------
3. แหล่งข้อมูล
--------------------------------------------------------------------------------

  [1] Pizza Place Sales (Maven Analytics)
      รูปแบบ  : CSV เชิงสัมพันธ์ 4 ตาราง
      ปริมาณ  : 48,620 รายการสินค้า / 21,350 บิล

  [2] Open-Meteo Historical Weather Archive     https://open-meteo.com/
      รูปแบบ  : Nested JSON ผ่าน REST API
      ปริมาณ  : 8,760 ชั่วโมง + 365 วัน
      ไม่ต้องใช้ API key

  [3] Nager.Date Public Holidays                https://date.nager.at/
      รูปแบบ  : Nested JSON ผ่าน REST API
      ปริมาณ  : 16 รายการ / 13 วันที่
      ไม่ต้องใช้ API key

  สมมติฐาน
      ข้อมูลต้นทางไม่ระบุที่ตั้งร้าน โครงการกำหนดให้ร้านตั้งอยู่ที่ชิคาโก
      เพื่อให้ดึงข้อมูลสภาพอากาศได้ ผลการวิเคราะห์ด้านอากาศจึงอยู่บนสมมติฐานนี้


--------------------------------------------------------------------------------
4. STAR SCHEMA
--------------------------------------------------------------------------------

  Grain ของ Fact Table
      หนึ่งแถว = พิซซ่าหนึ่งชนิดในหนึ่งขนาด ที่ปรากฏในหนึ่งบิล

  ตาราง                      แถว      คำอธิบาย
  -------------------------  -------  --------------------------------------------
  fact_sales_line             48,620  Transaction fact
                                      measures: quantity, unit_price, line_revenue
  dim_date                       365  ปฏิทินเต็มปี รวมวันที่ร้านปิด
                                      is_holiday, is_trading_day, season
  dim_time                        24  ชั่วโมงของวัน พร้อม daypart
  dim_pizza                       96  เมนู x ขนาด แบนรวมหมวดหมู่ในตารางเดียว
  dim_weather                     23  Mini-dimension สภาพอากาศแบบจัดช่วง
  dim_ingredient                  65  วัตถุดิบแต่ละชนิด
  bridge_pizza_ingredient        181  ตารางเชื่อมความสัมพันธ์แบบหลายต่อหลาย

  หมายเหตุสำคัญ
      dim_date สร้างจากปฏิทินเต็ม 365 วัน ไม่ใช่จากวันที่ที่มียอดขาย
      เพราะร้านปิด 7 วัน หากสร้างจากข้อมูลขายเท่านั้น วันที่ปิดจะหายไปจากมิติ

      bridge_pizza_ingredient ใช้ตอบคำถามเรื่องวัตถุดิบเท่านั้น
      ห้ามนำมารวมยอดขาย เพราะจะนับซ้ำตามจำนวนวัตถุดิบในแต่ละเมนู


--------------------------------------------------------------------------------
5. ตัวเลขควบคุม (CONTROL TOTALS)
--------------------------------------------------------------------------------

  etl_pipeline.ipynb ตรวจค่าเหล่านี้อัตโนมัติทุกครั้งที่รัน
  หากค่าใดไม่ตรง pipeline จะหยุดทำงานทันที ไม่ปล่อยข้อมูลผิดเข้าคลัง

      Total Revenue          $817,860.05
      Total Quantity Sold    49,574
      Number of Orders       21,350
      แถวใน Fact Table       48,620
      วันทำการ               358 จาก 365 วัน

  ผลการรันล่าสุด : ผ่านการตรวจสอบทั้ง 41 จุด


--------------------------------------------------------------------------------
6. ปัญหาคุณภาพข้อมูลที่จัดการแล้ว
--------------------------------------------------------------------------------

  [1] pizza_types.csv ไม่ใช่ UTF-8 มี byte 0x91
      แก้โดยอ่านด้วย encoding='cp1252'

  [2] Open-Meteo คืนข้อมูลเป็น columnar arrays
      แก้โดย transpose ให้เป็น row-oriented

  [3] หน่วยวัดเป็นระบบเมตริก (C, mm, cm, km/h) แต่ธุรกิจอยู่ในสหรัฐฯ
      แก้โดยแปลงเป็น F, inch, mph โดยหิมะใช้ตัวหารคนละค่ากับฝน

  [4] วันที่มี 3 รูปแบบจาก 3 แหล่ง
      แก้โดยระบุ format string ชัดเจนทุกจุด ไม่ปล่อยให้ระบบเดา

  [5] วันหยุดซ้ำ 16 แถว แต่มีเพียง 13 วันที่
      แก้โดย deduplicate ก่อน join มิฉะนั้น Fact Table จะบานปลาย
      (พิสูจน์แล้วว่าทำให้ออเดอร์เพิ่มจาก 21,350 เป็น 21,414 แถว)

  [6] weather_code เป็นรหัสตัวเลข WMO 4677
      แก้โดย join ตารางอ้างอิงเพื่อถอดความหมาย

  [7] API ใช้ UTC offset คงที่ ไม่ขยับตาม Daylight Saving Time
      ตัดสินใจไม่แก้ แต่บันทึกเป็นข้อจำกัดไว้ในรายงาน

  ทุกข้อบันทึกจำนวนแถวก่อนและหลังการแก้ไขไว้ใน Audit Log
  ท้ายไฟล์ 02_ETL/etl_pipeline.ipynb


--------------------------------------------------------------------------------
7. เครื่องมือที่ใช้
--------------------------------------------------------------------------------

  Python + pandas      จัดการข้อมูลทุกขั้นตอน
  Jupyter Notebook     เอกสารประกอบกระบวนการ ETL
  DuckDB               คลังข้อมูลแบบคอลัมน์ เก็บในไฟล์เดียว
  Chart.js             Dashboard เชิงโต้ตอบในไฟล์ HTML เดียว
  scikit-learn         Machine Learning ใน Pizza_Analysis.ipynb

================================================================================
