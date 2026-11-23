"""
Step 2: load sample Items, Offers and Relationships into DynamoDB.

    python seed_data.py

Each product *family* (e.g. Echo Dot) expands into one ASIN per variant
combination (Color x Configuration). Every ASIN gets:
  - 1 METADATA row (Item)
  - N OFFER#<seller> rows (Offers)
  - 1 VARIANT#<sibling-asin> row per family member (Relationships)
"""
import itertools
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()
import db  # noqa: E402

SELLERS = {
    "AMZN": {"seller_name": "Amazon.clone", "ships_from": "Amazon.clone", "delivery": "Tomorrow", "condition": "New"},
    "TECHD": {"seller_name": "TechDeals India", "ships_from": "Amazon.clone", "delivery": "in 2 days", "condition": "New"},
    "GADG": {"seller_name": "GadgetHub Retail", "ships_from": "GadgetHub Retail", "delivery": "in 4-5 days", "condition": "New"},
    "RENEW": {"seller_name": "Renewed Store", "ships_from": "Renewed Store", "delivery": "in 3 days", "condition": "Renewed"},
}

FAMILIES = [
    {
        "code": "ECHO",
        "title": "All-new Echo Dot (3rd Gen) - Smart speaker with Alexa",
        "brand": "Amazon",
        "category": "Smart Speakers",
        "kind": "speaker",
        "rating": Decimal("4.6"), "rating_count": 1028447, "badge": "#1 Best Seller",
        "dims": {
            "Color": {"Charcoal": "#3b3f45", "Heather Grey": "#b7b9bb", "Sandstone": "#d9cfc1"},
            "Configuration": {"Echo Dot": 0, "Echo Dot with Smart Plug": 1500},
        },
        "base_price": 3449, "list_price": 4499,
        "bullets": [
            "Our most popular voice controlled speaker, now with improved sound and a new design.",
            "Ask Alexa to play music, answer questions, read the news, check the weather, set alarms and more.",
            "Control compatible smart home devices with your voice.",
            "Stream songs from Amazon Music, Spotify, JioSaavn and more.",
            "Call almost anyone hands-free. Instantly drop in on other rooms in your home.",
        ],
        "details": {"Brand": "Amazon", "Connectivity": "Wi-Fi, Bluetooth", "Speaker": "1.6\"", "Weight": "300 g"},
        "description": "Echo Dot is our most popular voice-controlled speaker, now with a fabric design and improved speaker for richer and louder sound.",
    },
    {
        "code": "KNDL",
        "title": "Kindle Paperwhite - Now Waterproof with 2x the Storage",
        "brand": "Amazon",
        "category": "E-readers",
        "kind": "reader",
        "rating": Decimal("4.5"), "rating_count": 84213, "badge": "Amazon's Choice",
        "dims": {
            "Color": {"Black": "#1d1f22", "Twilight Blue": "#2a3d5c"},
            "Storage": {"8 GB": 0, "32 GB": 2500},
        },
        "base_price": 12999, "list_price": 14999,
        "bullets": [
            "The thinnest, lightest Kindle Paperwhite yet, with a flush-front design and 300 ppi glare-free display.",
            "Waterproof (IPX8) so you can read in the bath or by the pool.",
            "Weeks of battery life on a single charge.",
            "Holds thousands of books.",
        ],
        "details": {"Brand": "Amazon", "Display": "6\" 300 ppi", "Waterproof": "IPX8", "Weight": "182 g"},
        "description": "Kindle Paperwhite is purpose-built for reading, with a glare-free display that reads like real paper even in bright sunlight.",
    },
    {
        "code": "FTVS",
        "title": "Fire TV Stick with Alexa Voice Remote",
        "brand": "Amazon",
        "category": "Streaming Media Players",
        "kind": "stick",
        "rating": Decimal("4.4"), "rating_count": 212930, "badge": None,
        "dims": {"Configuration": {"Fire TV Stick": 0, "Fire TV Stick 4K": 1500}},
        "colors": "#2b2b2b",
        "base_price": 3999, "list_price": 5999,
        "bullets": [
            "Stream in Full HD or 4K with support for Dolby Vision, HDR and Dolby Atmos.",
            "Alexa Voice Remote lets you use your voice to search and launch shows.",
            "Thousands of channels, apps and Alexa skills.",
        ],
        "details": {"Brand": "Amazon", "Resolution": "1080p / 4K", "Connectivity": "Wi-Fi, HDMI", "Weight": "32 g"},
        "description": "Fire TV Stick lets you stream your favourite shows and movies on any HDMI TV.",
    },
]

# seller id -> price delta relative to the variant's base price (None = this seller has no offer)
SELLER_PRICING = [("AMZN", 0, 120), ("TECHD", -150, 6), ("GADG", 300, 25), ("RENEW", -900, 0)]


def build_rows():
    rows = []
    for fam in FAMILIES:
        dim_names = list(fam["dims"])
        combos = list(itertools.product(*[list(fam["dims"][d]) for d in dim_names]))
        members = []
        for idx, combo in enumerate(combos, start=1):
            attrs = dict(zip(dim_names, combo))
            asin = f"B0{fam['code']}{idx:04d}"
            color = fam["dims"].get("Color", {}).get(attrs.get("Color")) or fam.get("colors", "#333")
            extra = sum(fam["dims"][d][v] for d, v in attrs.items() if isinstance(fam["dims"][d][v], int))
            members.append({"asin": asin, "attrs": attrs, "color": color, "price": fam["base_price"] + extra})

        for i, m in enumerate(members):
            label = ", ".join(m["attrs"].values())
            offer_prices = []
            for j, (sid, delta, stock) in enumerate(SELLER_PRICING):
                # not every seller sells every variant: makes the data more realistic
                if sid == "RENEW" and i % 2:
                    continue
                price = Decimal(m["price"] + delta)
                rows.append({
                    "pk": f"ITEM#{m['asin']}", "sk": f"OFFER#{sid}", "kind": "OFFER",
                    "asin": m["asin"], "seller_id": sid, **SELLERS[sid],
                    "price": price,
                    "list_price": Decimal(fam["list_price"] + (m["price"] - fam["base_price"])),
                    "stock": stock, "version": 1, "updated_at": "seed",
                })
                if stock > 0:
                    offer_prices.append(price)

            rows.append({
                "pk": f"ITEM#{m['asin']}", "sk": "METADATA", "entity_type": "ITEM", "kind": "ITEM",
                "asin": m["asin"], "family": fam["code"],
                "title": fam["title"], "brand": fam["brand"], "category": fam["category"],
                "variant_attrs": m["attrs"], "variant_label": label,
                "is_default_variant": i == 0,
                "image": {"kind": fam["kind"], "color": m["color"]},
                "rating": fam["rating"], "rating_count": fam["rating_count"], "badge": fam["badge"],
                "bullets": fam["bullets"], "details": fam["details"], "description": fam["description"],
                "min_price": min(offer_prices) if offer_prices else None,
            })

            for sib in members:  # relationship rows, including a self-link
                swatch = fam["dims"].get("Color", {}).get(sib["attrs"].get("Color"))
                rows.append({
                    "pk": f"ITEM#{m['asin']}", "sk": f"VARIANT#{sib['asin']}", "kind": "VARIANT",
                    "target_asin": sib["asin"], "variant_attrs": sib["attrs"],
                    **({"swatch": swatch} if swatch else {}),
                })
    # Only METADATA rows carry `entity_type` (the GSI partition key), so
    # offers and variants never enter the index (a "sparse" GSI).
    return rows


def seed():
    rows = build_rows()
    with db.table().batch_writer() as batch:
        for r in rows:
            batch.put_item(Item=r)
    return rows


if __name__ == "__main__":
    rows = seed()
    items = sum(1 for r in rows if r["sk"] == "METADATA")
    offers = sum(1 for r in rows if r["sk"].startswith("OFFER#"))
    rels = sum(1 for r in rows if r["sk"].startswith("VARIANT#"))
    print(f"Wrote {len(rows)} rows to {db.TABLE_NAME}: {items} items, {offers} offers, {rels} relationships")
