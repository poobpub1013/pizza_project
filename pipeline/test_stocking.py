"""Self-check for the ingredient order calculator: run `python3 pipeline/test_stocking.py`.

Guards the contract the dashboard's renderStock() depends on — every ingredient
carries a category, and every category has a grams-per-portion default (a missing
one silently falls back to 40 g in the browser).
"""
import json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(ROOT, "index.html")).read()
i = html.index("const DATA = ") + len("const DATA = ")
DATA, _ = json.JSONDecoder().raw_decode(html[i:])
st = DATA["stocking"]

assert "__DATA__" not in html, "index.html was not rendered from the template"
assert all(x.get("category") for x in st["ingredients"]), "ingredient missing a category"
missing = {x["category"] for x in st["ingredients"]} - set(st["grams_per_portion"])
assert not missing, f"no grams-per-portion default for: {missing}"

# order weight for a known pizza count must be positive and food-plausible
pizzas = 1000
grams = sum(x["per_pizza"] * pizzas * st["grams_per_portion"][x["category"]]
            for x in st["ingredients"])
per_pizza_g = grams / pizzas
assert 100 < per_pizza_g < 800, f"implausible {per_pizza_g:.0f} g of toppings per pizza"

# the planner UI must still wire the calculator up
for hook in ("initGrams()", "gramsFor(", "Order quantity"):
    assert hook in html, f"dashboard lost {hook}"

print(f"OK — {len(st['ingredients'])} ingredients, "
      f"{len(st['grams_per_portion'])} categories, {per_pizza_g:.0f} g raw per pizza")
