import nbformat as nbf, os
OUT = os.path.dirname(os.path.abspath(__file__))
nb = nbf.v4.new_notebook()
c = []
md = lambda s: c.append(nbf.v4.new_markdown_cell(s))
co = lambda s: c.append(nbf.v4.new_code_cell(s))

md("""# Pizza Sales — Analytics Workbench
**Built on the cleaned data warehouse `03_Data_Warehouse/pizza_dw.duckdb`.**

This notebook analyses the Pizza Place Sales data (FY2015) using the **cleaned dimensional model**
from the `01_Raw_Data → 02_ETL → 03_Data_Warehouse` pipeline. It walks through:

1. Connecting to the warehouse and reviewing the star schema
2. Exploratory data analysis (EDA)
3. Feature engineering for three prediction targets
4. Training & comparing models (Linear Regression, Random Forest, Gradient Boosting)
5. External signals — holidays & (raw) weather
6. Ingredient stocking from a demand forecast
7. **Extension points** — clearly marked places to add your own cleaning & models

> Requirements: `pip install duckdb pandas scikit-learn matplotlib`
""")

md("## 1 · Connect to the cleaned warehouse")
co("""import os, json, duckdb, pandas as pd, numpy as np
import matplotlib.pyplot as plt

# Resolve the warehouse path whether the notebook runs from the project root or elsewhere
CAND = ["03_Data_Warehouse/pizza_dw.duckdb", "pizza_dw.duckdb",
        os.path.expanduser("~/project_pizza/03_Data_Warehouse/pizza_dw.duckdb")]
DB = next((p for p in CAND if os.path.exists(p)), CAND[0])
con = duckdb.connect(DB, read_only=True)
print("connected:", DB)

# Every table in the cleaned star schema, with row counts
tabs = con.execute('''SELECT table_name FROM duckdb_tables()
                      WHERE schema_name='main' ORDER BY table_name''').df()
tabs["rows"] = [con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tabs.table_name]
tabs""")

md("""### The star schema
`fact_sales_line` sits at the sales-line grain (one row per pizza sold on an order),
surrounded by conformed dimensions:

- **dim_date** (365) — full calendar with `season`, `is_weekend`, `is_holiday`, `is_trading_day`
- **dim_time** (24) — hour of day with `daypart` (Morning / Lunch / Afternoon / Dinner / Late Night) and `is_open`
- **dim_weather** (23) — *banded* weather: `condition_group`, `temp_band`, `precip_level`
- **dim_pizza** (96) — menu: `size_rank`, `unit_price`, `category`, `ingredients`
- **dim_ingredient** (65) + **bridge_pizza_ingredient** (181) — many-to-many for stocking

> Note: the warehouse uses **ISO weekday** (1=Mon … 7=Sun). Convert with `dow % 7` if you want Sunday-first.
""")
co("""# Peek at the fact joined to its dimensions
con.execute('''
  SELECT f.sales_key, d.full_date::DATE AS date, d.day_name, t.hour_24, t.daypart,
         p.pizza_name, p.size_code, w.condition_group, w.temp_band,
         f.quantity, f.line_revenue
  FROM fact_sales_line f
  JOIN dim_date d USING(date_key) JOIN dim_time t USING(time_key)
  JOIN dim_pizza p USING(pizza_key) JOIN dim_weather w USING(weather_key)
  ORDER BY f.sales_key LIMIT 5''').df()""")

md("## 2 · Exploratory data analysis")
co("""# Daily sales rolled up from the fact
daily = con.execute('''
  SELECT d.date_key, d.full_date::DATE AS date, (d.day_of_week % 7) AS dow, d.day_name,
         d.month, d.month_name, d.is_weekend, d.is_holiday, d.season,
         COUNT(DISTINCT f.order_id) AS orders, SUM(f.quantity) AS pizzas_sold,
         ROUND(SUM(f.line_revenue),2) AS revenue
  FROM fact_sales_line f JOIN dim_date d USING(date_key)
  GROUP BY ALL ORDER BY d.date_key''').df()
print("Days:", len(daily), "| Revenue:", f"${daily.revenue.sum():,.0f}",
      "| Pizzas:", f"{daily.pizzas_sold.sum():,}")
daily[['pizzas_sold','revenue','orders']].describe().round(1)""")

co("""# Sales trend over the year
fig, ax = plt.subplots(figsize=(12,4))
ax.plot(pd.to_datetime(daily.date), daily.pizzas_sold, lw=.9, color='#1f7a8c')
ax.set_title("Pizzas sold per day — FY2015"); ax.set_ylabel("pizzas"); plt.tight_layout()""")

co("""# Demand by weekday and by daypart (the warehouse's own time periods)
wk = con.execute('''SELECT (d.day_of_week % 7) dow, ANY_VALUE(d.day_name) day_name,
      AVG(day_tot) avg_pizzas FROM (
        SELECT date_key, SUM(quantity) day_tot FROM fact_sales_line GROUP BY date_key
      ) s JOIN dim_date d USING(date_key) GROUP BY dow ORDER BY dow''').df()
dp = con.execute('''SELECT t.daypart, SUM(f.quantity) pizzas, MIN(t.hour_24) mn
      FROM fact_sales_line f JOIN dim_time t USING(time_key)
      GROUP BY t.daypart ORDER BY mn''').df()
fig, ax = plt.subplots(1,2, figsize=(13,3.5))
ax[0].bar(wk.day_name, wk.avg_pizzas, color='#16345c'); ax[0].set_title("Avg pizzas by weekday")
ax[0].tick_params(axis='x', rotation=45)
ax[1].bar(dp.daypart, dp.pizzas, color='#b32330'); ax[1].set_title("Total pizzas by daypart")
ax[1].tick_params(axis='x', rotation=20); plt.tight_layout()""")

co("""# Category & top pizzas
cat = con.execute('''SELECT p.category, ROUND(SUM(f.line_revenue),0) revenue, SUM(f.quantity) pizzas
   FROM fact_sales_line f JOIN dim_pizza p USING(pizza_key) GROUP BY p.category ORDER BY revenue DESC''').df()
print(cat)
con.execute('''SELECT p.pizza_name, p.size_code, ROUND(SUM(f.line_revenue),0) revenue
   FROM fact_sales_line f JOIN dim_pizza p USING(pizza_key)
   GROUP BY p.pizza_name, p.size_code ORDER BY revenue DESC LIMIT 10''').df()""")

md("## 3 · Feature engineering — three prediction targets")
md("""We model three business questions:

| Target | Grain | Predict |
|---|---|---|
| **Daily demand** | one day | `pizzas_sold` from calendar features |
| **Hourly demand** | date × hour | `pizzas_sold` from hour + weekday |
| **Pizza revenue** | one pizza (type×size) | `revenue` from menu attributes |
""")
co("""# --- Daily demand features (calendar only, no same-day leakage) ---
daily_feat = daily[['dow','month','is_weekend','is_holiday','pizzas_sold']].copy()
daily_feat['is_weekend'] = daily_feat['is_weekend'].astype(int)
Xd = pd.get_dummies(daily_feat.drop(columns='pizzas_sold'),
                    columns=['dow','month']).astype(float)
yd = daily_feat.pizzas_sold.astype(float)
Xd.head()""")

md("## 4 · Train & compare models")
co("""from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.model_selection import cross_val_predict, KFold
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

MODELS = {"Linear Regression": LinearRegression(),
          "Random Forest": RandomForestRegressor(n_estimators=200, random_state=42),
          "Gradient Boosting": GradientBoostingRegressor(random_state=42)}
CV = KFold(5, shuffle=True, random_state=42)

def benchmark(X, y):
    rows=[]
    for name, m in MODELS.items():
        p = cross_val_predict(m, X, y, cv=CV)
        rows.append({"model":name, "R2":round(r2_score(y,p),3),
                     "MAE":round(mean_absolute_error(y,p),2),
                     "RMSE":round(np.sqrt(mean_squared_error(y,p)),2)})
    return pd.DataFrame(rows)

benchmark(Xd, yd)   # daily demand""")

co("""# Feature importance from the Random Forest (daily demand)
rf = RandomForestRegressor(n_estimators=200, random_state=42).fit(Xd, yd)
imp = pd.Series(rf.feature_importances_, index=Xd.columns).sort_values(ascending=False)
imp.head(12).plot.barh(figsize=(7,4), color='#1f7a8c'); plt.gca().invert_yaxis()
plt.title("Daily demand — feature importance"); plt.tight_layout()""")

md("""## 5 · External signals — holidays & weather
`dim_date` already carries `is_holiday`. Weather in the warehouse is stored as **bands**
(`dim_weather`), so for a continuous-weather comparison we re-attach the raw Open-Meteo
daily values from `01_Raw_Data`. Location is *assumed* Chicago — the sales data has no store
location, so treat weather findings with that caveat.""")
co("""# Attach raw continuous weather to each date
WCAND = ["01_Raw_Data/weather/openmeteo_archive_chicago_2015.json",
         os.path.expanduser("~/project_pizza/01_Raw_Data/weather/openmeteo_archive_chicago_2015.json")]
WP = next((p for p in WCAND if os.path.exists(p)), WCAND[0])
wj = json.load(open(WP))
wd = pd.DataFrame({"date": pd.to_datetime(wj["daily"]["time"]).date,
                   "temp_mean": wj["daily"]["temperature_2m_mean"],
                   "precip_mm": wj["daily"]["precipitation_sum"],
                   "snow_cm":   wj["daily"]["snowfall_sum"],
                   "wind_max":  wj["daily"]["wind_speed_10m_max"]})
dw = daily.merge(wd, on="date", how="left")
print("Holiday uplift: {:.0%}".format(
    dw[dw.is_holiday].pizzas_sold.mean()/dw[~dw.is_holiday].pizzas_sold.mean()-1))
print("corr(temp, pizzas):", round(dw.temp_mean.corr(dw.pizzas_sold),3))
print("corr(rain, pizzas):", round(dw.precip_mm.corr(dw.pizzas_sold),3))""")
co("""# Honest feature-set comparison: calendar vs +holiday vs +weather (5-fold CV R2)
dw['is_weekend']=dw['is_weekend'].astype(int); dw['is_holiday']=dw['is_holiday'].astype(int)
def cvr2(feats):
    X = pd.get_dummies(dw[feats], columns=[c for c in ('dow','month') if c in feats]).astype(float)
    return round(r2_score(dw.pizzas_sold, cross_val_predict(LinearRegression(), X, dw.pizzas_sold, cv=CV)),3)
print("calendar only :", cvr2(['dow','month','is_weekend']))
print("+ holiday     :", cvr2(['dow','month','is_weekend','is_holiday']))
print("+ weather     :", cvr2(['dow','month','is_weekend','is_holiday','temp_mean','precip_mm','snow_cm','wind_max']))
# -> holidays add a little; weather adds noise here (assumed location).""")

md("""## 6 · Ingredient stocking from a demand forecast
`bridge_pizza_ingredient` links each pizza type to its ingredients. Weighting each sold pizza
by size (S=1.0 … XXL=3.0) gives size-weighted portions; dividing by pizzas sold gives a
**per-pizza intensity**, which multiplied by any forecast yields a stocking list.""")
co("""per_pizza = con.execute('''
    WITH usage AS (
      SELECT ig.ingredient_name AS ingredient,
             SUM(f.quantity * (0.5 + 0.5*p.size_rank)) AS portions_year
      FROM fact_sales_line f JOIN dim_pizza p USING(pizza_key)
      JOIN bridge_pizza_ingredient b ON b.pizza_type_id = p.pizza_type_id
      JOIN dim_ingredient ig ON ig.ingredient_key = b.ingredient_key
      GROUP BY ig.ingredient_name)
    SELECT ingredient,
           ROUND(portions_year * 1.0 / (SELECT SUM(quantity) FROM fact_sales_line), 4) AS per_pizza
    FROM usage ORDER BY per_pizza DESC''').df()

def stocking_list(forecast_pizzas):
    s = per_pizza.copy()
    s["portions_needed"] = (s.per_pizza * forecast_pizzas).round().astype(int)
    return s[["ingredient","portions_needed"]]

# e.g. plan for a forecast of 177 pizzas (a busy holiday Friday)
stocking_list(177).head(10)""")

md("## 7 · Extension points — your turn")
md("""The cleaned warehouse is built so you can extend cleanly. Suggested next steps:

- **Use the banded weather**: `dim_weather` groups conditions/temperature/precip — try it as a
  categorical feature instead of the raw continuous values.
- **Richer features**: lag features (yesterday's demand), rolling averages, cyclical encodings
  (sin/cos of hour & month), or daypart interactions.
- **Better hourly model**: hourly demand is non-linear (lunch & dinner peaks) — gradient boosting
  with one-hot hours is usually strongest.
- **New views**: publish your own aggregate tables back into the warehouse for the dashboard.

Write your experiments below.
""")
co("""# TODO (friend): your cleaning / feature ideas here
# Example scaffold — add a 7-day rolling average as a feature:
daily2 = daily[['date','pizzas_sold']].copy()
daily2['roll7'] = daily2.pizzas_sold.rolling(7, min_periods=1).mean()
daily2.tail()""")

co("""con.close()  # tidy up""")

nb['cells'] = c
nb.metadata['kernelspec'] = {"display_name":"Python 3","language":"python","name":"python3"}
path = os.path.join(OUT, "Pizza_Analysis.ipynb")
nbf.write(nb, path)
print("notebook written:", path, "| cells:", len(c))
