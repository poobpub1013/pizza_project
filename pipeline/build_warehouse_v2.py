"""
Pizza Warehouse v2 — integrates external signals for higher forecast accuracy.
Adds:  weather (Open-Meteo, Chicago 2015)  +  US public holidays (Nager.Date)
       + an ingredient bridge & stocking marts.
Location assumption for weather: Chicago (source data has no store location).
"""
import duckdb, os, json, zipfile, pandas as pd

OUT  = os.path.dirname(os.path.abspath(__file__))
HOME = "/home/puyawapun"
DB   = os.path.join(OUT, "pizza_warehouse.duckdb")
PZ   = "/tmp/pizza_extract/pizza_sales"
AUX  = "/tmp/aux/01_Raw_Data"

# ---- ensure raw inputs are extracted ----
if not os.path.exists(PZ):
    with zipfile.ZipFile(f"{HOME}/Pizza+Place+Sales.zip") as z: z.extractall("/tmp/pizza_extract")
if not os.path.exists(AUX):
    with zipfile.ZipFile(f"{HOME}/01_Raw_Data.zip") as z: z.extractall("/tmp/aux")

if os.path.exists(DB): os.remove(DB)
con = duckdb.connect(DB)
for s in ("raw","core","mart"): con.execute(f"CREATE SCHEMA IF NOT EXISTS {s};")

# ======================================================================
# RAW — pizza sales
# ======================================================================
con.execute(f"CREATE TABLE raw.orders AS SELECT order_id::INT order_id, date::DATE order_date, time::TIME order_time FROM read_csv_auto('{PZ}/orders.csv',header=True);")
con.execute(f"CREATE TABLE raw.order_details AS SELECT order_details_id::INT order_details_id, order_id::INT order_id, pizza_id::VARCHAR pizza_id, quantity::INT quantity FROM read_csv_auto('{PZ}/order_details.csv',header=True);")
con.execute(f"CREATE TABLE raw.pizzas AS SELECT pizza_id::VARCHAR pizza_id, pizza_type_id::VARCHAR pizza_type_id, size::VARCHAR size, price::DOUBLE price FROM read_csv_auto('{PZ}/pizzas.csv',header=True);")
ptypes = pd.read_csv(f"{PZ}/pizza_types.csv", encoding="cp1252")
con.register("ptypes_df", ptypes)
con.execute('CREATE TABLE raw.pizza_types AS SELECT pizza_type_id::VARCHAR AS pizza_type_id, "name"::VARCHAR AS "name", category::VARCHAR AS category, ingredients::VARCHAR AS ingredients FROM ptypes_df;')

# ======================================================================
# RAW — weather (daily + hourly) from nested JSON
# ======================================================================
w = json.load(open(f"{AUX}/weather/openmeteo_archive_chicago_2015.json"))
wd = pd.DataFrame(w["daily"]);  wd["time"] = pd.to_datetime(wd["time"]).dt.date
wh = pd.DataFrame(w["hourly"]);
wh_dt = pd.to_datetime(wh["time"]); wh["w_date"] = wh_dt.dt.date; wh["w_hour"] = wh_dt.dt.hour
con.register("wd_df", wd); con.register("wh_df", wh)
con.execute("""CREATE TABLE raw.weather_daily AS SELECT
  time::DATE AS w_date, weather_code::INT AS weather_code,
  temperature_2m_max AS temp_max, temperature_2m_min AS temp_min, temperature_2m_mean AS temp_mean,
  apparent_temperature_max AS feels_max, precipitation_sum AS precip_mm, rain_sum AS rain_mm,
  snowfall_sum AS snow_cm, precipitation_hours AS precip_hours, wind_speed_10m_max AS wind_max
  FROM wd_df;""")
con.execute("""CREATE TABLE raw.weather_hourly AS SELECT
  w_date::DATE AS w_date, w_hour::INT AS w_hour, temperature_2m AS temp,
  apparent_temperature AS feels, relative_humidity_2m AS humidity,
  precipitation AS precip_mm, rain AS rain_mm, snowfall AS snow_cm,
  weather_code::INT AS weather_code, wind_speed_10m AS wind FROM wh_df;""")

# weather code lookup
wc = json.load(open(f"{AUX}/weather/wmo_weather_codes.json"))
wc_df = pd.DataFrame([{"weather_code": int(r["weather_code"]),
                       "weather_desc": r.get("description"),
                       "condition_group": r.get("condition_group")} for r in wc])
con.register("wc_df", wc_df)
con.execute("CREATE TABLE core.dim_weather_code AS SELECT * FROM wc_df;")

# ======================================================================
# RAW — holidays
# ======================================================================
hol = json.load(open(f"{AUX}/holidays/nager_publicholidays_us_2015.json"))
hol_df = pd.DataFrame([{"holiday_date": h["date"], "holiday_name": h["name"],
                        "is_public": ("Public" in (h.get("types") or [])),
                        "is_global": bool(h.get("global"))} for h in hol])
con.register("hol_df", hol_df)
con.execute("CREATE TABLE raw.holidays AS SELECT holiday_date::DATE holiday_date, holiday_name, is_public, is_global FROM hol_df;")
# collapse to one flag per date (public/global holidays that shift demand)
con.execute("""CREATE TABLE core.dim_holiday AS
  SELECT holiday_date AS date_key, ANY_VALUE(holiday_name) AS holiday_name
  FROM raw.holidays WHERE is_public AND is_global GROUP BY holiday_date;""")

# ======================================================================
# CORE — dimensions
# ======================================================================
con.execute("""CREATE TABLE core.dim_pizza_type AS
  SELECT pizza_type_id, name AS pizza_name, category, ingredients,
         (length(ingredients)-length(replace(ingredients,',',''))+1) AS n_ingredients
  FROM raw.pizza_types;""")
con.execute("""CREATE TABLE core.dim_pizza AS
  SELECT p.pizza_id, p.pizza_type_id, t.pizza_name, t.category, p.size, p.price,
         CASE p.size WHEN 'S' THEN 1 WHEN 'M' THEN 2 WHEN 'L' THEN 3 WHEN 'XL' THEN 4 WHEN 'XXL' THEN 5 END AS size_ordinal,
         t.n_ingredients
  FROM raw.pizzas p JOIN core.dim_pizza_type t USING (pizza_type_id);""")

# dim_date enriched with weather + holiday
con.execute("""CREATE TABLE core.dim_date AS
  SELECT DISTINCT o.order_date AS date_key,
     EXTRACT(year FROM o.order_date) AS year, EXTRACT(month FROM o.order_date) AS month,
     monthname(o.order_date) AS month_name, EXTRACT(day FROM o.order_date) AS day_of_month,
     EXTRACT(dow FROM o.order_date) AS day_of_week, dayname(o.order_date) AS day_name,
     EXTRACT(week FROM o.order_date) AS week_of_year, EXTRACT(quarter FROM o.order_date) AS quarter,
     CASE WHEN EXTRACT(dow FROM o.order_date) IN (0,6) THEN TRUE ELSE FALSE END AS is_weekend,
     wd.temp_mean, wd.temp_max, wd.temp_min, wd.precip_mm, wd.rain_mm, wd.snow_cm,
     wd.precip_hours, wd.wind_max, wd.weather_code,
     CASE WHEN h.date_key IS NOT NULL THEN TRUE ELSE FALSE END AS is_holiday,
     h.holiday_name
  FROM raw.orders o
  LEFT JOIN raw.weather_daily wd ON o.order_date = wd.w_date
  LEFT JOIN core.dim_holiday h    ON o.order_date = h.date_key;""")

# ======================================================================
# CORE — fact (pizza-line grain) with hourly weather
# ======================================================================
con.execute("""CREATE TABLE core.fact_order_details AS
  SELECT od.order_details_id, od.order_id, o.order_date AS date_key, o.order_time,
     EXTRACT(hour FROM o.order_time) AS order_hour,
     od.pizza_id, dp.pizza_type_id, dp.category, dp.size,
     od.quantity, dp.price AS unit_price, od.quantity*dp.price AS line_revenue,
     wh.temp AS hr_temp, wh.precip_mm AS hr_precip, wh.snow_cm AS hr_snow, wh.weather_code AS hr_weather_code
  FROM raw.order_details od
  JOIN raw.orders o        ON od.order_id = o.order_id
  JOIN core.dim_pizza dp   ON od.pizza_id = dp.pizza_id
  LEFT JOIN raw.weather_hourly wh ON o.order_date = wh.w_date AND EXTRACT(hour FROM o.order_time) = wh.w_hour;""")

# ======================================================================
# CORE — ingredient bridge (explode comma-separated ingredients)
# ======================================================================
con.execute("""CREATE TABLE core.bridge_pizza_ingredient AS
  SELECT pizza_type_id, category, trim(ing) AS ingredient
  FROM core.dim_pizza_type, UNNEST(string_split(ingredients, ',')) AS t(ing);""")
con.execute("""CREATE TABLE core.dim_ingredient AS
  SELECT ingredient, COUNT(DISTINCT pizza_type_id) AS used_in_types
  FROM core.bridge_pizza_ingredient GROUP BY ingredient ORDER BY ingredient;""")

# ======================================================================
# MART — analytics (enriched with weather + holiday)
# ======================================================================
con.execute("""CREATE TABLE mart.mart_daily_sales AS
  SELECT f.date_key, d.year, d.month, d.month_name, d.day_of_week, d.day_name,
     d.is_weekend, d.quarter, d.week_of_year,
     d.temp_mean, d.temp_max, d.precip_mm, d.snow_cm, d.wind_max, d.weather_code,
     CASE WHEN d.is_holiday THEN 1 ELSE 0 END AS is_holiday, d.holiday_name,
     COUNT(DISTINCT f.order_id) AS orders, SUM(f.quantity) AS pizzas_sold,
     ROUND(SUM(f.line_revenue),2) AS revenue,
     ROUND(SUM(f.line_revenue)/COUNT(DISTINCT f.order_id),2) AS avg_order_value,
     ROUND(SUM(f.quantity)*1.0/COUNT(DISTINCT f.order_id),3) AS pizzas_per_order
  FROM core.fact_order_details f JOIN core.dim_date d ON f.date_key=d.date_key
  GROUP BY ALL ORDER BY f.date_key;""")

con.execute("""CREATE TABLE mart.mart_hourly_demand AS
  SELECT f.date_key, d.day_of_week, d.day_name, d.is_weekend, f.order_hour,
     CASE WHEN d.is_holiday THEN 1 ELSE 0 END AS is_holiday,
     AVG(f.hr_temp) AS temp, MAX(f.hr_precip) AS precip_mm, MAX(f.hr_snow) AS snow_cm,
     COUNT(DISTINCT f.order_id) AS orders, SUM(f.quantity) AS pizzas_sold,
     ROUND(SUM(f.line_revenue),2) AS revenue
  FROM core.fact_order_details f JOIN core.dim_date d ON f.date_key=d.date_key
  GROUP BY ALL ORDER BY f.date_key, f.order_hour;""")

con.execute("""CREATE TABLE mart.mart_category_performance AS
  SELECT category, COUNT(*) AS line_items, SUM(quantity) AS pizzas_sold,
     ROUND(SUM(line_revenue),2) AS revenue, ROUND(AVG(unit_price),2) AS avg_unit_price
  FROM core.fact_order_details GROUP BY ALL ORDER BY revenue DESC;""")

con.execute("""CREATE TABLE mart.mart_pizza_performance AS
  SELECT f.pizza_id, dp.pizza_name, dp.category, dp.size, dp.size_ordinal, dp.price AS unit_price,
     dp.n_ingredients, SUM(f.quantity) AS pizzas_sold, COUNT(DISTINCT f.order_id) AS orders,
     ROUND(SUM(f.line_revenue),2) AS revenue
  FROM core.fact_order_details f JOIN core.dim_pizza dp ON f.pizza_id=dp.pizza_id
  GROUP BY ALL ORDER BY revenue DESC;""")

# ingredient usage mart — total portions consumed over the year (size-weighted)
# size weight approximates ingredient amount per pizza by size
con.execute("""CREATE TABLE mart.mart_ingredient_usage AS
  WITH line AS (
    SELECT f.pizza_type_id, f.quantity,
           CASE f.size WHEN 'S' THEN 1.0 WHEN 'M' THEN 1.5 WHEN 'L' THEN 2.0
                       WHEN 'XL' THEN 2.5 WHEN 'XXL' THEN 3.0 END AS size_weight
    FROM core.fact_order_details f)
  SELECT b.ingredient,
         ROUND(SUM(l.quantity * l.size_weight),1) AS portions_year,
         SUM(l.quantity) AS pizzas_with_ingredient
  FROM line l JOIN core.bridge_pizza_ingredient b ON l.pizza_type_id=b.pizza_type_id
  GROUP BY b.ingredient ORDER BY portions_year DESC;""")

# report
print("Warehouse v2 built:", DB)
for sch in ("raw","core","mart"):
    rows = con.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{sch}' ORDER BY 1").fetchall()
    print(f"[{sch}] " + ", ".join(f"{t[0]}={con.execute(f'SELECT count(*) FROM {sch}.{t[0]}').fetchone()[0]}" for t in rows))
miss = con.execute("SELECT COUNT(*) FROM core.dim_date WHERE temp_mean IS NULL").fetchone()[0]
print("dates missing weather:", miss)
print("holidays flagged in dim_date:", con.execute("SELECT COUNT(*) FROM core.dim_date WHERE is_holiday").fetchone()[0])
print("unique ingredients:", con.execute("SELECT COUNT(*) FROM core.dim_ingredient").fetchone()[0])
print("corr(temp_mean,pizzas):", con.execute("SELECT round(corr(temp_mean,pizzas_sold),3) FROM mart.mart_daily_sales").fetchone()[0])
print("holiday vs normal avg pizzas:", con.execute("SELECT is_holiday, round(avg(pizzas_sold),1) FROM mart.mart_daily_sales GROUP BY is_holiday ORDER BY is_holiday").fetchall())
con.close()
