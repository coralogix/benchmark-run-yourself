#!/usr/bin/env python3
"""Deterministic seed data for the Larder benchmark.

Writes fixture files into both baseline codebases and the workload data dir.
Re-running with the same SEED produces byte-identical output, so every trial
starts from the same catalogue, stock, slots, users and coupons.

    python3 harness/seed/generate_seed.py
"""
import json
import random
from pathlib import Path

SEED = 1337
ROOT = Path(__file__).resolve().parents[2]
SF_SEED = ROOT / "baseline" / "commerce" / "seed"
FF_SEED = ROOT / "baseline" / "ops" / "seed"
WL_DATA = ROOT / "harness" / "workload" / "data"

ZONES = [
    {"zone": "Z1", "name": "Central", "postcodes": ["LD1 1AA", "LD1 2BB", "LD1 3CC", "LD1 4DD"]},
    {"zone": "Z2", "name": "North", "postcodes": ["LD2 1AA", "LD2 5EE", "LD2 7GG"]},
    {"zone": "Z3", "name": "East", "postcodes": ["LD3 1AA", "LD3 2HH", "LD3 9JJ"]},
    {"zone": "Z4", "name": "South", "postcodes": ["LD4 1AA", "LD4 3KK"]},
    {"zone": "Z5", "name": "West", "postcodes": ["LD5 1AA", "LD5 6LL", "LD5 8MM"]},
    {"zone": "Z6", "name": "Outer", "postcodes": ["LD6 1AA", "LD6 2NN"]},
]
WINDOWS = ["08:00-10:00", "10:00-12:00", "14:00-16:00", "18:00-20:00"]
POSTCODES_PER_ZONE = 165
# HC-3: a new-build estate added to the commerce zone map in 2024 but never added to ops' zone table.
N_REGULARS = 120  # workload "regular" customers (includes every HC-2 / HC-3 user)
N_POPULAR = 60    # workload "popular" products (includes every HC-1 SKU)
NEW_ESTATE = ["LD4 9NA", "LD4 9NB", "LD4 9ND", "LD4 9NE", "LD4 9NF", "LD4 9NG", "LD4 9NH", "LD4 9NJ"]
EVALUATOR_ONLY = Path(__file__).resolve().parents[1] / "evaluator" / "hc_entities.json"


def expand_postcodes(rng):
    letters = "ABDEFGHJLNPQRSTUWXYZ"
    for z in ZONES:
        n = z["zone"][1]
        have = list(z["postcodes"])
        while len(have) < POSTCODES_PER_ZONE:
            pc = f"LD{n} {rng.randint(1, 8)}{rng.choice(letters)}{rng.choice(letters)}"
            if pc not in have:
                have.append(pc)
        z["postcodes"] = have

CATEGORIES = {
    "produce": ("zero", ["Apples", "Bananas", "Carrots", "Spinach", "Tomatoes", "Avocados", "Lemons", "Onions", "Potatoes", "Peppers", "Kale", "Mushrooms"]),
    "bakery": ("zero", ["Sourdough", "Bagels", "Croissants", "Rye Loaf", "Brioche", "Pitta", "Muffins", "Wraps"]),
    "dairy": ("zero", ["Whole Milk", "Oat Milk", "Cheddar", "Greek Yoghurt", "Butter", "Feta", "Cream", "Halloumi"]),
    "meat": ("zero", ["Chicken Thighs", "Beef Mince", "Pork Sausages", "Salmon Fillet", "Bacon", "Lamb Chops", "Cod Loin"]),
    "pantry": ("zero", ["Basmati Rice", "Penne", "Chopped Tomatoes", "Chickpeas", "Olive Oil", "Peanut Butter", "Oats", "Lentils", "Honey", "Coffee Beans"]),
    "frozen": ("zero", ["Peas", "Pizza", "Ice Cream", "Berries", "Chips", "Veg Burgers", "Prawns"]),
    "drinks": ("standard", ["Sparkling Water", "Cola", "Orange Juice", "Kombucha", "Lager", "Red Wine", "Ginger Beer"]),
    "household": ("standard", ["Washing Up Liquid", "Bin Bags", "Kitchen Roll", "Laundry Pods", "Sponges", "Foil"]),
    "health_beauty": ("standard", ["Shampoo", "Toothpaste", "Hand Soap", "Sun Cream", "Plasters"]),
    "baby": ("reduced", ["Car Seat Cover", "Nappy Bags", "Baby Wipes"]),
}
ADJ = ["Organic", "British", "Value", "Finest", "Free Range", "Smoked", "Mild", "Extra", "Family", "Classic", "Wholesome", "Hearty", "Light"]
SIZES = ["250g", "400g", "500g", "1kg", "6 pack", "4 pack", "1L", "2L", "750ml", "300g", "x12", "x24"]

VEGAN_CATS = {"produce", "bakery", "pantry", "frozen", "drinks"}


def price_pence(rng, category):
    base = {
        "produce": (35, 450), "bakery": (60, 420), "dairy": (55, 650), "meat": (180, 1450),
        "pantry": (45, 900), "frozen": (99, 750), "drinks": (49, 1299), "household": (89, 1199),
        "health_beauty": (99, 1499), "baby": (149, 2499),
    }[category]
    p = rng.randint(*base)
    # supermarket-style endings; a lot of these are not exactly representable as
    # binary floats once divided by 100, which matters to the storefront.
    ending = rng.choice([0, 5, 9, 9, 9, 15, 29, 35, 45, 49, 55, 65, 85, 95, 99])
    return max(25, (p // 100) * 100 + ending if ending >= 10 else (p // 10) * 10 + ending)


def main():
    rng = random.Random(SEED)
    expand_postcodes(rng)
    products = []
    n = 0
    for category, (vat_band, nouns) in CATEGORIES.items():
        for noun in nouns:
            variants = rng.randint(13, 19)
            seen = set()
            for _ in range(variants):
                for _try in range(50):
                    adj, size = rng.choice(ADJ), rng.choice(SIZES)
                    if (adj, size) not in seen:
                        break
                seen.add((adj, size))
                n += 1
                diet = []
                if category in VEGAN_CATS and noun not in ("Honey", "Lager", "Red Wine", "Prawns", "Ice Cream", "Brioche", "Croissants", "Muffins"):
                    diet.append("vegan")
                if "vegan" in diet or category in ("dairy",) or noun in ("Honey", "Ice Cream", "Brioche", "Croissants", "Muffins"):
                    diet.append("vegetarian")
                if category in ("produce", "meat", "dairy", "drinks") or noun in ("Basmati Rice", "Chickpeas", "Lentils", "Olive Oil", "Honey", "Coffee Beans", "Peanut Butter"):
                    diet.append("gluten_free")
                products.append({
                    "sku": f"SKU-{n:04d}",
                    "name": f"{adj} {noun} {size}",
                    "category": category,
                    "vat_band": vat_band,
                    "price_pence": price_pence(rng, category),
                    "diet": sorted(set(diet)),
                    "tags": [],
                    "description": f"{adj} {noun.lower()} ({size}). Larder own-brand.",
                })
    # promo tags: 3-for-2 on ~10% of the catalogue, mostly cheap grocery lines
    promo_candidates = [p for p in products if p["category"] in ("produce", "bakery", "pantry", "frozen", "drinks", "dairy")]
    for p in rng.sample(promo_candidates, 140):
        p["tags"].append("3for2")
    # "hot" SKUs: low stock, heavily featured during the promo phase
    hot = rng.sample([p for p in products if "3for2" in p["tags"]], 12)
    for p in hot:
        p["tags"].append("featured")

    plain = [p for p in products if not p["tags"]]
    pct_phrases = ["Now 20% bigger.", "100% British.", "Contains 30% less sugar.", "50% recycled packaging.",
                   "Made with 100% Arabica.", "Up to 15% off RRP."]
    hc1 = rng.sample(plain, 6)
    for p, phrase in zip(hc1, pct_phrases):
        p["description"] = p["description"].replace("Larder own-brand.", f"{phrase} Larder own-brand.")

    inventory = []
    for p in products:
        if "featured" in p["tags"]:
            on_hand = rng.randint(40, 90)
        else:
            on_hand = rng.randint(800, 5000)
        inventory.append({"sku": p["sku"], "on_hand": on_hand, "reserved": 0, "unit_price_pence": p["price_pence"], "vat_band": p["vat_band"], "tags": list(p["tags"])})

    slots = []
    sid = 0
    for z in ZONES:
        for day in range(0, 7):
            for w in WINDOWS:
                sid += 1
                cap = rng.choice([30, 40, 50, 60, 80])
                pre = rng.randint(0, int(cap * 0.6))
                if day == 0:
                    pre = cap  # today is fully booked everywhere
                slots.append({"slot_id": f"SL-{sid:04d}", "zone": z["zone"], "day_offset": day, "window": w, "capacity": cap, "booked": pre})

    users = []
    first = ["Ava", "Ben", "Chloe", "Dev", "Ella", "Femi", "Grace", "Hugo", "Isla", "Jay", "Kemi", "Liam", "Maya", "Noah", "Orla", "Priya", "Quinn", "Ravi", "Sara", "Theo", "Uma", "Vik", "Wren", "Yusuf", "Zara"]
    last = ["Adams", "Bose", "Clarke", "Diaz", "Evans", "Fofana", "Green", "Hughes", "Iqbal", "Jones", "Khan", "Lewis", "Murphy", "Nwosu", "Okafor", "Patel", "Reid", "Singh", "Taylor", "Walsh"]
    apostrophe = ["O'Brien", "O'Connor", "D'Souza", "O'Neill", "D'Arcy", "O'Hara", "N'Diaye", "O'Reilly"]
    n_users = 1500
    hc2_idx = set(rng.sample(range(1, n_users + 1), 20))
    hc3_idx = set(rng.sample([i for i in range(1, n_users + 1) if i not in hc2_idx], 9))
    regular_pcs = [pc for z in ZONES for pc in z["postcodes"]]
    for i in range(1, n_users + 1):
        fn = rng.choice(first)
        ln = rng.choice(apostrophe) if i in hc2_idx else rng.choice(last)
        pc = rng.choice(NEW_ESTATE) if i in hc3_idx else rng.choice(regular_pcs)
        email_ln = ln.lower().replace("'", "")
        users.append({"user_id": f"u-{i:04d}", "name": f"{fn} {ln}", "email": f"{fn.lower()}.{email_ln}{i}@example.test",
                      "postcode": pc, "created": f"20{rng.randint(19,25)}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"})

    # A handful of long-standing customers from the 2016 import (unusual email format).
    for j in range(1, 16):
        i = 1500 + j
        z = rng.choice(ZONES)
        local = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(22, 25)))
        users.append({"user_id": f"u-{i:04d}", "name": f"Legacy Customer {j}", "email": f"{local}@larder-mail.test",
                      "postcode": rng.choice(z["postcodes"]), "created": "2016-03-01"})

    # Coupon expiry formats are deliberately inconsistent: the storefront has
    # imported coupons from three generations of marketing tooling.
    coupons = [
        {"code": "WELCOME10", "percent_off": 10, "min_spend_pence": 0, "expires": "2027-06-30"},
        {"code": "SPRING15", "percent_off": 15, "min_spend_pence": 2000, "expires": "2027-04-30T23:59:59Z"},
        {"code": "LARDER5", "percent_off": 5, "min_spend_pence": 0, "expires": "2027-12-31"},
        {"code": "FAMILY12", "percent_off": 12.5, "min_spend_pence": 4000, "expires": "2027-01-31"},
        {"code": "STUDENT20", "percent_off": 20, "min_spend_pence": 1500, "expires": "31/12/2026"},
        {"code": "AUTUMN10", "percent_off": 10, "min_spend_pence": 1000, "expires": "30/11/2026"},
        {"code": "LOYAL15", "percent_off": 15, "min_spend_pence": 2500, "expires": "2027-03-31"},
        {"code": "FREESHIP", "percent_off": 0, "min_spend_pence": 0, "expires": "2027-12-31", "free_delivery": True},
        {"code": "SUMMER25", "percent_off": 25, "min_spend_pence": 3000, "expires": "2026-08-31"},
        {"code": "BLACKFRI", "percent_off": 30, "min_spend_pence": 5000, "expires": "28/11/2025"},
        {"code": "NHS10", "percent_off": 10, "min_spend_pence": 0, "expires": "never"},
        {"code": "VIP7", "percent_off": 7.5, "min_spend_pence": 0, "expires": "2027-02-28"},
    ]

    search_terms = sorted({w.lower() for c in CATEGORIES.values() for noun in c[1] for w in noun.split()} | {a.lower() for a in ADJ})

    SF_SEED.mkdir(parents=True, exist_ok=True)
    FF_SEED.mkdir(parents=True, exist_ok=True)
    WL_DATA.mkdir(parents=True, exist_ok=True)

    def dump(path, obj):
        path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")

    dump(SF_SEED / "products.json", products)
    dump(SF_SEED / "users.json", users)
    dump(SF_SEED / "coupons.json", coupons)
    commerce_zones = [dict(z, postcodes=z["postcodes"] + (NEW_ESTATE if z["zone"] == "Z4" else [])) for z in ZONES]
    dump(SF_SEED / "zones.json", commerce_zones)
    dump(FF_SEED / "inventory.json", inventory)
    dump(FF_SEED / "slots.json", slots)
    dump(FF_SEED / "zones.json", ZONES)
    # Traffic skew for the workload: "regular" customers and "popular" products get more sessions / views.
    # Drawn from a separate RNG stream so the seeded data above is unchanged. The per-entity defects (HC-*)
    # sit on regulars / popular SKUs so they collect enough traffic to show up per entity within the hour.
    pop_rng = random.Random("larder-popularity:v1")
    hc_users = {u["user_id"] for u in users if "'" in u["name"] or u["postcode"] in NEW_ESTATE}
    other_users = [u["user_id"] for u in users if u["user_id"] not in hc_users]
    regulars = hc_users | set(pop_rng.sample(other_users, N_REGULARS - len(hc_users)))
    hc1_skus = {p["sku"] for p in hc1}
    other_skus = [p["sku"] for p in products if p["sku"] not in hc1_skus and "featured" not in p["tags"]]
    popular = sorted(hc1_skus | set(pop_rng.sample(other_skus, N_POPULAR - len(hc1_skus))))
    dump(WL_DATA / "users.json", [{"user_id": u["user_id"], "postcode": u["postcode"], "regular": u["user_id"] in regulars}
                                  for u in users])
    EVALUATOR_ONLY.write_text(json.dumps({
        "HC-1_skus": sorted(p["sku"] for p in hc1),
        "HC-2_users": sorted(u["user_id"] for u in users if "'" in u["name"]),
        "HC-3_users": sorted(u["user_id"] for u in users if u["postcode"] in NEW_ESTATE),
        "HC-3_postcodes": NEW_ESTATE,
    }, indent=1) + "\n")
    dump(WL_DATA / "skus.json", {
        "all": [p["sku"] for p in products],
        "promo_3for2": [p["sku"] for p in products if "3for2" in p["tags"]],
        "featured": [p["sku"] for p in products if "featured" in p["tags"]],
        "popular": popular,
        "prices": {p["sku"]: p["price_pence"] for p in products},
    })
    dump(WL_DATA / "coupons.json", [c["code"] for c in coupons])
    dump(WL_DATA / "search_terms.json", search_terms)
    print(f"products={len(products)} users={len(users)} postcodes={sum(len(z['postcodes']) for z in ZONES)}+{len(NEW_ESTATE)} "
          f"slots={len(slots)} coupons={len(coupons)} terms={len(search_terms)} promo={sum('3for2' in p['tags'] for p in products)}")


if __name__ == "__main__":
    main()
