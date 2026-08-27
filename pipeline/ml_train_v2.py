"""
Model benchmarking v2 — adds weather + holiday signals and exports everything the
dashboard needs: model specs for exact-date prediction, seasonal weather defaults,
hourly-share distribution for time-period forecasting, and ingredient stocking intensities.
"""
import duckdb, os, json, numpy as np, pandas as pd
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.model_selection import cross_val_predict, KFold
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

OUT = os.path.dirname(os.path.abspath(__file__))
con = duckdb.connect(os.path.join(OUT, "pizza_warehouse.duckdb"), read_only=True)
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
# DAILY — with weather + holiday
# ======================================================================
daily = con.execute("""
  SELECT date_key, day_of_week, month, EXTRACT(day FROM date_key) AS day_of_month,
         week_of_year, quarter, is_weekend, is_holiday,
         temp_mean, temp_max, precip_mm, snow_cm, wind_max,
         orders, pizzas_sold, revenue, avg_order_value
  FROM mart.mart_daily_sales ORDER BY date_key""").df()
daily["is_weekend"] = daily["is_weekend"].astype(int)

cal_feats  = ["day_of_week","month","is_weekend"]
hol_feats  = cal_feats + ["is_holiday"]
full_feats = hol_feats + ["temp_mean","precip_mm","snow_cm","wind_max"]
def dmat(feats, cat=("day_of_week","month")):
    return pd.get_dummies(daily[feats], columns=[c for c in cat if c in feats]).astype(float)
y = daily["pizzas_sold"].astype(float)
Xcal, Xhol, Xfull = dmat(cal_feats), dmat(hol_feats), dmat(full_feats)

# Three-tier honest comparison of feature sets (5-fold CV R²)
lift = {"calendar_only": cv_r2(Xcal, y),
        "plus_holiday":  cv_r2(Xhol, y),
        "plus_weather":  cv_r2(Xfull, y)}

bundle["daily"] = {
    "label":"Daily Demand Forecast","grain":"One row per calendar day (2015)",
    "target":"pizzas_sold","target_label":"Pizzas sold per day",
    "predictors": full_feats,
    "corr_columns":["day_of_week","month","is_weekend","is_holiday","temp_mean","temp_max",
                    "precip_mm","snow_cm","wind_max","orders","pizzas_sold","revenue","avg_order_value"],
    "rows": json.loads(daily.drop(columns=["date_key"]).to_json(orient="records")),
    "models": benchmark(Xhol, y),   # production feature set = calendar + holiday (best CV)
    "lift": lift,
}

# ---- export a LINEAR MODEL SPEC for in-browser exact-date prediction ----
# one-hot day_of_week + month, plus flags, plus standardized numeric weather
num_feats = ["temp_mean","precip_mm","snow_cm","wind_max"]
means = {f: float(daily[f].mean()) for f in num_feats}
sds   = {f: float(daily[f].std() or 1) for f in num_feats}
design = pd.DataFrame(index=daily.index)
for f in num_feats: design[f] = (daily[f]-means[f])/sds[f]
design["is_weekend"] = daily["is_weekend"].astype(float)
design["is_holiday"] = daily["is_holiday"].astype(float)
for dow in range(7): design[f"dow_{dow}"] = (daily["day_of_week"]==dow).astype(float)
for mo in range(1,13): design[f"mo_{mo}"] = (daily["month"]==mo).astype(float)
ridge = Ridge(alpha=1.0).fit(design.values, y.values)
coef = dict(zip(design.columns, ridge.coef_))
spec = {"intercept": float(ridge.intercept_),
        "numeric": [{"feat": f, "mean": means[f], "sd": sds[f], "coef": float(coef[f])} for f in num_feats],
        "flags":   {"is_weekend": float(coef["is_weekend"]), "is_holiday": float(coef["is_holiday"])},
        "dow":     {str(d): float(coef[f"dow_{d}"]) for d in range(7)},
        "month":   {str(m): float(coef[f"mo_{m}"]) for m in range(1,13)}}
bundle["daily"]["model_spec"] = spec

# ======================================================================
# HOURLY — with weather + holiday
# ======================================================================
hourly = con.execute("""
  SELECT order_hour, day_of_week, is_weekend, is_holiday, temp, precip_mm, snow_cm,
         orders, pizzas_sold, revenue FROM mart.mart_hourly_demand""").df()
hourly["is_weekend"] = hourly["is_weekend"].astype(int)
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
# PIZZA — revenue drivers (unchanged features)
# ======================================================================
pizza = con.execute("""SELECT pizza_name, category, size, size_ordinal, unit_price, n_ingredients,
                       pizzas_sold, orders, revenue FROM mart.mart_pizza_performance""").df()
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
# PLANNER — seasonal weather defaults + hourly share distribution
# ======================================================================
seasonal = con.execute("""SELECT month, ROUND(AVG(temp_mean),1) temp_mean, ROUND(AVG(precip_mm),2) precip_mm,
   ROUND(AVG(snow_cm),2) snow_cm, ROUND(AVG(wind_max),1) wind_max FROM mart.mart_daily_sales GROUP BY month ORDER BY month""").df()
# hourly share of daily pizzas, split weekday/weekend
def hshare(is_wk):
    df = con.execute(f"""SELECT order_hour, SUM(pizzas_sold) p FROM mart.mart_hourly_demand
        WHERE is_weekend={is_wk} GROUP BY order_hour ORDER BY order_hour""").df()
    tot = df.p.sum(); df["share"] = df.p/tot
    return {int(r.order_hour): round(float(r.share),5) for r in df.itertuples()}
periods = {"Morning (9–11)":[9,10],"Lunch (11–14)":[11,12,13],"Afternoon (14–17)":[14,15,16],
           "Dinner (17–21)":[17,18,19,20],"Late (21–24)":[21,22,23]}
bundle["planner"] = {
    "seasonal_defaults": json.loads(seasonal.to_json(orient="records")),
    "hourly_share_weekday": hshare(0),
    "hourly_share_weekend": hshare(1),
    "periods": periods,
    "weather_ranges": {  # for override sliders (min,max,step)
        "temp_mean":[-25,35,1],"precip_mm":[0,40,1],"snow_cm":[0,20,0.5],"wind_max":[0,60,1]},
    "avg_daily_pizzas": round(float(daily.pizzas_sold.mean()),1),
    "holiday_uplift_pct": round(float((daily[daily.is_holiday==1].pizzas_sold.mean()/daily[daily.is_holiday==0].pizzas_sold.mean()-1)*100),1),
    # public+global holidays as month-day keys so the date picker can auto-detect them for any year
    "holidays": [{"md": r[0].strftime("%m-%d"), "name": r[1]} for r in
                 con.execute("SELECT holiday_date, holiday_name FROM raw.holidays WHERE is_public AND is_global ORDER BY holiday_date").fetchall()],
}

# ======================================================================
# STOCKING — per-pizza ingredient portion intensities
# ======================================================================
usage = con.execute("SELECT ingredient, portions_year, pizzas_with_ingredient FROM mart.mart_ingredient_usage ORDER BY portions_year DESC").df()
total_pizzas = con.execute("SELECT SUM(quantity) FROM core.fact_order_details").fetchone()[0]
usage["per_pizza"] = usage["portions_year"]/total_pizzas   # size-weighted portions per pizza sold
bundle["stocking"] = {
    "total_pizzas_year": int(total_pizzas),
    "ingredients": [{"ingredient": r.ingredient, "per_pizza": round(float(r.per_pizza),4),
                     "portions_year": round(float(r.portions_year),1)} for r in usage.itertuples()],
    "note":"per_pizza = size-weighted ingredient portions consumed per pizza sold (S=1.0 … XXL=3.0). "
           "Stock = per_pizza × predicted pizzas.",
}

# ======================================================================
# KPI + charts (kept, extended)
# ======================================================================
kpi = con.execute("""SELECT
  (SELECT ROUND(SUM(line_revenue),0) FROM core.fact_order_details) total_revenue,
  (SELECT COUNT(DISTINCT order_id) FROM core.fact_order_details) total_orders,
  (SELECT SUM(quantity) FROM core.fact_order_details) total_pizzas,
  (SELECT COUNT(*) FROM core.dim_pizza) menu_items,
  (SELECT COUNT(*) FROM core.dim_ingredient) ingredients,
  (SELECT ROUND(AVG(rev),2) FROM (SELECT SUM(line_revenue) rev FROM core.fact_order_details GROUP BY order_id) t) avg_order_value
""").df().iloc[0].to_dict()
bundle["kpi"] = {k:(float(v) if v is not None else None) for k,v in kpi.items()}
bundle["charts"] = {
  "category": json.loads(con.execute("SELECT category, revenue, pizzas_sold FROM mart.mart_category_performance ORDER BY revenue DESC").df().to_json(orient="records")),
  "monthly": json.loads(con.execute("SELECT month, ANY_VALUE(month_name) month_name, SUM(pizzas_sold) pizzas_sold, ROUND(SUM(revenue),0) revenue FROM mart.mart_daily_sales GROUP BY month ORDER BY month").df().to_json(orient="records")),
  "weekday": json.loads(con.execute("SELECT day_of_week, ANY_VALUE(day_name) day_name, ROUND(AVG(pizzas_sold),1) avg_pizzas FROM mart.mart_daily_sales GROUP BY day_of_week ORDER BY day_of_week").df().to_json(orient="records")),
  "hourly": json.loads(con.execute("SELECT order_hour, ROUND(AVG(pizzas_sold),2) avg_pizzas FROM mart.mart_hourly_demand GROUP BY order_hour ORDER BY order_hour").df().to_json(orient="records")),
  "top_pizzas": json.loads(con.execute("SELECT pizza_name, size, revenue FROM mart.mart_pizza_performance ORDER BY revenue DESC LIMIT 10").df().to_json(orient="records")),
  "top_ingredients": json.loads(usage.head(15)[["ingredient","portions_year"]].to_json(orient="records")),
}

with open(os.path.join(OUT,"dashboard_data.json"),"w") as f: json.dump(bundle, f)

print("== Daily demand feature-set comparison (5-fold CV R²) ==")
print(f"   calendar only   : {lift['calendar_only']}")
print(f"   + holiday       : {lift['plus_holiday']}")
print(f"   + weather       : {lift['plus_weather']}")
print("\n== Benchmarks ==")
for t in ("daily","hourly","pizza"):
    print(f" {bundle[t]['label']}:")
    for m,s in bundle[t]["models"].items(): print(f"    {m:<18} R2={s['r2']:>7} MAE={s['mae']:>8} RMSE={s['rmse']:>8}")
print("\nHoliday uplift %:", bundle["planner"]["holiday_uplift_pct"])
print("Top ingredients by yearly portions:", [(i['ingredient'],i['portions_year']) for i in bundle['stocking']['ingredients'][:5]])
print("JSON bytes:", os.path.getsize(os.path.join(OUT,"dashboard_data.json")))
con.close()
