"""
ml_train_v3 — rebuilds every analytic on top of the FRIEND'S cleaned warehouse
(03_Data_Warehouse/pizza_dw.duckdb) instead of the old self-built one.

Design decisions:
  * The friend's star schema is the single source of truth: fact_sales_line +
    dim_date / dim_time / dim_pizza / dim_weather / dim_ingredient / bridge.
  * Weather in the DW is banded (categorical). Per the user's "hybrid" choice we
    ALSO attach the raw continuous Open-Meteo values (01_Raw_Data) so the planner
    keeps temperature/rain sliders and the correlation view keeps numeric weather.
  * The friend uses ISO weekday (1=Mon..7=Sun); the dashboard expects JS weekday
    (0=Sun..6=Sat), so every day_of_week is converted with  iso % 7.
  * Time-periods for the planner now come from the friend's dim_time dayparts
    (Morning/Lunch/Afternoon/Dinner/Late Night) computed from real hours.

Output: dashboard_data.json  (same contract the dashboard already consumes).
"""
import duckdb, os, json, numpy as np, pandas as pd
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.model_selection import cross_val_predict, KFold
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

OUT      = os.path.dirname(os.path.abspath(__file__))
PROJECT  = os.environ.get("PIZZA_PROJECT", "/home/puyawapun/project_pizza")
DW       = os.path.join(PROJECT, "03_Data_Warehouse", "pizza_dw.duckdb")
WEATHER  = os.path.join(PROJECT, "01_Raw_Data", "weather", "openmeteo_archive_chicago_2015.json")

con = duckdb.connect(DW, read_only=True)

# ---------------------------------------------------------------- raw weather
wj = json.load(open(WEATHER))
wd = pd.DataFrame({
    "dt":        pd.to_datetime(wj["daily"]["time"]).strftime("%Y-%m-%d"),
    "temp_mean": wj["daily"]["temperature_2m_mean"],
    "temp_max":  wj["daily"]["temperature_2m_max"],
    "precip_mm": wj["daily"]["precipitation_sum"],
    "snow_cm":   wj["daily"]["snowfall_sum"],
    "wind_max":  wj["daily"]["wind_speed_10m_max"],
})
# hourly weather keyed by 'YYYY-MM-DD HH' for the hourly model
ht = pd.to_datetime(wj["hourly"]["time"])
wh = pd.DataFrame({
    "key":     [f"{t.strftime('%Y-%m-%d')} {t.hour:02d}" for t in ht],
    "temp":    wj["hourly"]["temperature_2m"],
    "h_precip":wj["hourly"]["precipitation"],
    "h_snow":  wj["hourly"]["snowfall"],
})
con.register("wd", wd); con.register("wh", wh)

MODELS = {"Linear Regression": lambda: LinearRegression(),
          "Random Forest": lambda: RandomForestRegressor(n_estimators=200, random_state=42),
          "Gradient Boosting": lambda: GradientBoostingRegressor(random_state=42)}
CV = KFold(5, shuffle=True, random_state=42)
def benchmark(X, y):
    out = {}
    for name, mk in MODELS.items():
        p = cross_val_predict(mk(), X, y, cv=CV)
        out[name] = {"r2": round(float(r2_score(y,p)),4), "mae": round(float(mean_absolute_error(y,p)),3),
                     "rmse": round(float(np.sqrt(mean_squared_error(y,p))),3)}
    return out
def cv_r2(X, y):
    return round(float(r2_score(y, cross_val_predict(LinearRegression(), X, y, cv=CV))), 4)

bundle = {}

# ======================================================================
# DAILY — fact grain rolled to day, joined to dim_date + continuous weather
# ======================================================================
daily = con.execute("""
  SELECT d.date_key,
         (d.day_of_week % 7)              AS day_of_week,   -- ISO->JS (Sun=0)
         d.month, d.day_of_month, d.week_of_year, d.quarter,
         CAST(d.is_weekend AS INT)        AS is_weekend,
         CAST(d.is_holiday AS INT)        AS is_holiday,
         strftime(d.full_date, '%Y-%m-%d') AS dt,
         COUNT(DISTINCT f.order_id)       AS orders,
         SUM(f.quantity)                  AS pizzas_sold,
         ROUND(SUM(f.line_revenue),2)     AS revenue
  FROM fact_sales_line f JOIN dim_date d USING(date_key)
  GROUP BY ALL ORDER BY d.date_key
""").df().merge(wd, on="dt", how="left")
daily["avg_order_value"] = (daily.revenue / daily.orders).round(2)

cal_feats  = ["day_of_week","month","is_weekend"]
hol_feats  = cal_feats + ["is_holiday"]
full_feats = hol_feats + ["temp_mean","precip_mm","snow_cm","wind_max"]
def dmat(feats, cat=("day_of_week","month")):
    return pd.get_dummies(daily[feats], columns=[c for c in cat if c in feats]).astype(float)
y = daily["pizzas_sold"].astype(float)
Xcal, Xhol, Xfull = dmat(cal_feats), dmat(hol_feats), dmat(full_feats)
lift = {"calendar_only": cv_r2(Xcal, y), "plus_holiday": cv_r2(Xhol, y), "plus_weather": cv_r2(Xfull, y)}

bundle["daily"] = {
    "label":"Daily Demand Forecast","grain":"One row per calendar day (2015)",
    "target":"pizzas_sold","target_label":"Pizzas sold per day",
    "predictors": full_feats,
    "corr_columns":["day_of_week","month","is_weekend","is_holiday","temp_mean","temp_max",
                    "precip_mm","snow_cm","wind_max","orders","pizzas_sold","revenue","avg_order_value"],
    "rows": json.loads(daily.drop(columns=["date_key","dt"]).to_json(orient="records")),
    "models": benchmark(Xhol, y),
    "lift": lift,
}

# ---- linear model spec for in-browser exact-date prediction ----
num_feats = ["temp_mean","precip_mm","snow_cm","wind_max"]
means = {f: float(daily[f].mean()) for f in num_feats}
sds   = {f: float(daily[f].std() or 1) for f in num_feats}
design = pd.DataFrame(index=daily.index)
for f in num_feats: design[f] = (daily[f]-means[f])/sds[f]
design["is_weekend"] = daily["is_weekend"].astype(float)
design["is_holiday"] = daily["is_holiday"].astype(float)
for dow in range(7):  design[f"dow_{dow}"] = (daily["day_of_week"]==dow).astype(float)
for mo in range(1,13):design[f"mo_{mo}"]  = (daily["month"]==mo).astype(float)
ridge = Ridge(alpha=1.0).fit(design.values, y.values)
coef = dict(zip(design.columns, ridge.coef_))
bundle["daily"]["model_spec"] = {
    "intercept": float(ridge.intercept_),
    "numeric": [{"feat": f, "mean": means[f], "sd": sds[f], "coef": float(coef[f])} for f in num_feats],
    "flags":   {"is_weekend": float(coef["is_weekend"]), "is_holiday": float(coef["is_holiday"])},
    "dow":     {str(d): float(coef[f"dow_{d}"]) for d in range(7)},
    "month":   {str(m): float(coef[f"mo_{m}"]) for m in range(1,13)}}

# ======================================================================
# HOURLY — fact grain by date x hour, joined to dim_time + hourly weather
# ======================================================================
hourly = con.execute("""
  SELECT (d.day_of_week % 7)          AS day_of_week,
         CAST(d.is_weekend AS INT)    AS is_weekend,
         CAST(d.is_holiday AS INT)    AS is_holiday,
         t.hour_24                    AS order_hour,
         strftime(d.full_date,'%Y-%m-%d') || ' ' || printf('%02d', t.hour_24) AS wkey,
         COUNT(DISTINCT f.order_id)   AS orders,
         SUM(f.quantity)              AS pizzas_sold,
         ROUND(SUM(f.line_revenue),2) AS revenue
  FROM fact_sales_line f JOIN dim_date d USING(date_key) JOIN dim_time t USING(time_key)
  GROUP BY ALL
""").df().merge(wh.rename(columns={"key":"wkey","h_precip":"precip_mm","h_snow":"snow_cm"}), on="wkey", how="left")
hourly = hourly.drop(columns=["wkey"])
Xh = pd.get_dummies(hourly[["order_hour","day_of_week","is_weekend","is_holiday","temp","precip_mm","snow_cm"]],
                    columns=["order_hour","day_of_week"]).astype(float)
yh = hourly["pizzas_sold"].astype(float)
bundle["hourly"] = {
    "label":"Hourly Demand Forecast","grain":"One row per date x hour",
    "target":"pizzas_sold","target_label":"Pizzas sold per hour-slot",
    "predictors":["order_hour","day_of_week","is_weekend","is_holiday","temp"],
    "corr_columns":["order_hour","day_of_week","is_weekend","is_holiday","temp","precip_mm","snow_cm",
                    "orders","pizzas_sold","revenue"],
    "rows": json.loads(hourly.to_json(orient="records")),
    "models": benchmark(Xh, yh),
}

# ======================================================================
# PIZZA — revenue drivers per pizza (type x size)
# ======================================================================
pizza = con.execute("""
  SELECT p.pizza_name, p.category, p.size_code AS size, p.size_rank AS size_ordinal,
         p.unit_price, ic.n_ingredients,
         SUM(f.quantity) AS pizzas_sold, COUNT(DISTINCT f.order_id) AS orders,
         ROUND(SUM(f.line_revenue),2) AS revenue
  FROM fact_sales_line f JOIN dim_pizza p USING(pizza_key)
  JOIN (SELECT pizza_type_id, COUNT(*) n_ingredients FROM bridge_pizza_ingredient GROUP BY pizza_type_id) ic
       ON ic.pizza_type_id = p.pizza_type_id
  GROUP BY ALL
""").df()
Xp = pd.get_dummies(pizza[["size_ordinal","unit_price","n_ingredients","category"]], columns=["category"]).astype(float)
yp = pizza["revenue"].astype(float)
bundle["pizza"] = {
    "label":"Pizza Revenue Drivers","grain":"One row per pizza (type x size)",
    "target":"revenue","target_label":"Total annual revenue per pizza",
    "predictors":["size_ordinal","unit_price","n_ingredients","category"],
    "corr_columns":["size_ordinal","unit_price","n_ingredients","pizzas_sold","orders","revenue"],
    "rows": json.loads(pizza.to_json(orient="records")),
    "models": benchmark(Xp, yp),
}

# ======================================================================
# PLANNER — seasonal weather, daypart periods (from dim_time), hourly shares
# ======================================================================
seasonal = (daily.groupby("month")[num_feats].mean().round(2).reset_index())
def hshare(is_wk):
    df = hourly[hourly.is_weekend==is_wk].groupby("order_hour").pizzas_sold.sum()
    tot = df.sum()
    return {int(h): round(float(v/tot),5) for h,v in df.items()}
# time-periods straight from the friend's dim_time dayparts
dayparts = con.execute("""
  SELECT daypart, list(hour_24 ORDER BY hour_24) AS hrs, min(hour_24) AS mn
  FROM dim_time WHERE is_open GROUP BY daypart ORDER BY mn""").df()
periods = {r.daypart: [int(h) for h in r.hrs] for r in dayparts.itertuples()}
hol_mean = daily[daily.is_holiday==1].pizzas_sold.mean()
norm_mean= daily[daily.is_holiday==0].pizzas_sold.mean()
holidays = con.execute("""
  SELECT strftime(full_date,'%m-%d') AS md, ANY_VALUE(holiday_name) AS name
  FROM dim_date WHERE is_holiday GROUP BY md ORDER BY md""").df()
bundle["planner"] = {
    "seasonal_defaults": json.loads(seasonal.to_json(orient="records")),
    "hourly_share_weekday": hshare(0),
    "hourly_share_weekend": hshare(1),
    "periods": periods,
    "weather_ranges": {"temp_mean":[-25,35,1],"precip_mm":[0,40,1],"snow_cm":[0,20,0.5],"wind_max":[0,60,1]},
    "avg_daily_pizzas": round(float(daily.pizzas_sold.mean()),1),
    "holiday_uplift_pct": round(float((hol_mean/norm_mean-1)*100),1),
    "holidays": [{"md": r.md, "name": r.name} for r in holidays.itertuples()],
}

# ======================================================================
# STOCKING — size-weighted ingredient portions per pizza sold
#   size weight: S=1.0, M=1.5, L=2.0, XL=2.5, XXL=3.0  => 0.5 + 0.5*size_rank
# ======================================================================
usage = con.execute("""
  SELECT ig.ingredient_name AS ingredient,
         ANY_VALUE(ig.ingredient_category) AS category,
         ROUND(SUM(f.quantity * (0.5 + 0.5*p.size_rank)),1) AS portions_year,
         COUNT(DISTINCT p.pizza_type_id) AS pizza_types_with_ingredient
  FROM fact_sales_line f JOIN dim_pizza p USING(pizza_key)
  JOIN bridge_pizza_ingredient b ON b.pizza_type_id = p.pizza_type_id
  JOIN dim_ingredient ig ON ig.ingredient_key = b.ingredient_key
  GROUP BY ig.ingredient_name ORDER BY portions_year DESC
""").df()
total_pizzas = con.execute("SELECT SUM(quantity) FROM fact_sales_line").fetchone()[0]
usage["per_pizza"] = usage["portions_year"]/total_pizzas
bundle["stocking"] = {
    "total_pizzas_year": int(total_pizzas),
    "ingredients": [{"ingredient": r.ingredient, "category": r.category,
                     "per_pizza": round(float(r.per_pizza),4),
                     "portions_year": round(float(r.portions_year),1)} for r in usage.itertuples()],
    # grams of raw ingredient per size-weighted portion (one Small-pizza serving).
    # ponytail: category-level averages — the source data has no recipe weights.
    # The dashboard exposes these as editable inputs, so a kitchen can calibrate
    # them against its own recipe card without touching this file.
    "grams_per_portion": {"Cheese": 70, "Sauce": 50, "Meat": 35,
                          "Seafood": 30, "Vegetable & Other": 25},
    "note":"per_pizza = size-weighted ingredient portions consumed per pizza sold (S=1.0 … XXL=3.0). "
           "Stock = per_pizza × predicted pizzas. Exact weight = portions × grams_per_portion[category].",
}

# ======================================================================
# KPI + charts
# ======================================================================
kpi = con.execute("""SELECT
  (SELECT ROUND(SUM(line_revenue),0) FROM fact_sales_line) total_revenue,
  (SELECT COUNT(DISTINCT order_id)   FROM fact_sales_line) total_orders,
  (SELECT SUM(quantity)              FROM fact_sales_line) total_pizzas,
  (SELECT COUNT(*)                   FROM dim_pizza)       menu_items,
  (SELECT COUNT(*)                   FROM dim_ingredient)  ingredients,
  (SELECT ROUND(AVG(rev),2) FROM (SELECT SUM(line_revenue) rev FROM fact_sales_line GROUP BY order_id) t) avg_order_value
""").df().iloc[0].to_dict()
bundle["kpi"] = {k:(float(v) if v is not None else None) for k,v in kpi.items()}

cat = con.execute("""SELECT p.category, ROUND(SUM(f.line_revenue),0) revenue, SUM(f.quantity) pizzas_sold
  FROM fact_sales_line f JOIN dim_pizza p USING(pizza_key) GROUP BY p.category ORDER BY revenue DESC""").df()
monthly = con.execute("""SELECT d.month, ANY_VALUE(d.month_name) month_name,
  SUM(f.quantity) pizzas_sold, ROUND(SUM(f.line_revenue),0) revenue
  FROM fact_sales_line f JOIN dim_date d USING(date_key) GROUP BY d.month ORDER BY d.month""").df()
weekday = (daily.groupby("day_of_week")
           .agg(avg_pizzas=("pizzas_sold","mean")).reset_index())
weekday["avg_pizzas"] = weekday["avg_pizzas"].round(1)
hourly_chart = (hourly.groupby("order_hour").pizzas_sold.mean().round(2)
                .reset_index().rename(columns={"pizzas_sold":"avg_pizzas"}))
bundle["charts"] = {
  "category": json.loads(cat.to_json(orient="records")),
  "monthly":  json.loads(monthly.to_json(orient="records")),
  "weekday":  json.loads(weekday.to_json(orient="records")),
  "hourly":   json.loads(hourly_chart.to_json(orient="records")),
  "top_pizzas": json.loads(pizza.sort_values("revenue",ascending=False)
                           .head(10)[["pizza_name","size","revenue"]].to_json(orient="records")),
  "top_ingredients": json.loads(usage.head(15)[["ingredient","portions_year"]].to_json(orient="records")),
}

with open(os.path.join(OUT,"dashboard_data.json"),"w") as f: json.dump(bundle, f)

# render the dashboard: template + data -> index.html (what GitHub Pages serves)
tpl  = open(os.path.join(OUT, "dashboard_template.html")).read()
html = os.path.join(PROJECT, "index.html")
with open(html, "w") as f: f.write(tpl.replace("__DATA__", json.dumps(bundle)))

# ------------------------------------------------------------------ report
print("Source warehouse:", DW)
print(f"KPIs: revenue={bundle['kpi']['total_revenue']:.0f} orders={bundle['kpi']['total_orders']:.0f} "
      f"pizzas={bundle['kpi']['total_pizzas']:.0f} menu={bundle['kpi']['menu_items']:.0f}")
print("\nDaily demand feature-set comparison (5-fold CV R2):")
for k in ("calendar_only","plus_holiday","plus_weather"): print(f"   {k:<14}: {lift[k]}")
print("\nBenchmarks:")
for t in ("daily","hourly","pizza"):
    print(f"  {bundle[t]['label']} (n={len(bundle[t]['rows'])}):")
    for m,s in bundle[t]["models"].items(): print(f"    {m:<18} R2={s['r2']:>8} MAE={s['mae']:>8} RMSE={s['rmse']:>8}")
print("\nDayparts (periods):", {k:v for k,v in periods.items()})
print("Holiday uplift %:", bundle["planner"]["holiday_uplift_pct"], "| holidays:", len(bundle["planner"]["holidays"]))
print("Top ingredients:", [(i['ingredient'],i['portions_year']) for i in bundle['stocking']['ingredients'][:5]])
print("JSON bytes:", os.path.getsize(os.path.join(OUT,"dashboard_data.json")))
con.close()
