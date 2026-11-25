"""
DynamoDB data-access layer for the Items and Offers service.

Single-table design (table name: ItemsAndOffers)
------------------------------------------------
Every row has a partition key `pk` and sort key `sk`.

  pk              sk                       entity        what it holds
  --------------  -----------------------  ------------  ---------------------------------------
  ITEM#<asin>     METADATA                 Item          title, brand, description, bullets, details
  ITEM#<asin>     OFFER#<seller_id>        Offer         seller, price, list price, stock, delivery
  ITEM#<asin>     VARIANT#<asin2>          Relationship  sibling variant (color / size / config)
  CART#<cart_id>  ITEM#<asin>#<seller_id>  CartLine      quantity + price snapshot

Because the Item, its Offers and its Relationships share one partition key,
the whole product detail page is loaded with ONE Query call.

Global secondary index `EntityIndex` (entity_type, title) lets the home page
list all items without a full-table Scan.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

TABLE_NAME = os.getenv("DDB_TABLE", "ItemsAndOffers")
REGION = os.getenv("AWS_REGION", "ap-south-1")
ENDPOINT = os.getenv("DDB_ENDPOINT") or None

ENTITY_INDEX = "EntityIndex"


def _resource():
    return boto3.resource("dynamodb", region_name=REGION, endpoint_url=ENDPOINT)


def table():
    return _resource().Table(TABLE_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Table creation
# --------------------------------------------------------------------------- #
def create_table(wait=True):
    """Create the ItemsAndOffers table (on-demand billing) with its GSI."""
    client = boto3.client("dynamodb", region_name=REGION, endpoint_url=ENDPOINT)
    try:
        client.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "entity_type", "AttributeType": "S"},
                {"AttributeName": "title", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": ENTITY_INDEX,
                    "KeySchema": [
                        {"AttributeName": "entity_type", "KeyType": "HASH"},
                        {"AttributeName": "title", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
        created = False
    if wait:
        client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    return created


def delete_table():
    client = boto3.client("dynamodb", region_name=REGION, endpoint_url=ENDPOINT)
    client.delete_table(TableName=TABLE_NAME)
    client.get_waiter("table_not_exists").wait(TableName=TABLE_NAME)


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #
def list_items(search=None):
    """Return all Item metadata rows via the GSI (Query, not Scan)."""
    kwargs = {
        "IndexName": ENTITY_INDEX,
        "KeyConditionExpression": Key("entity_type").eq("ITEM"),
    }
    items = []
    while True:
        resp = table().query(**kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # Show one card per product family (the default variant).
    items = [i for i in items if i.get("is_default_variant", True)]
    if search:
        s = search.lower()
        items = [i for i in items if s in i["title"].lower() or s in i.get("brand", "").lower()]

    # attach lowest offer price for the card
    for it in items:
        it["from_price"] = it.get("min_price")
    return items


def get_item_page(asin):
    """
    Load Item + Offers + Relationships for one ASIN with a single Query.
    Returns None if the item does not exist.
    """
    resp = table().query(
        KeyConditionExpression=Key("pk").eq(f"ITEM#{asin}"),
        ReturnConsumedCapacity="TOTAL",
    )
    rows = resp["Items"]
    item = next((r for r in rows if r["sk"] == "METADATA"), None)
    if item is None:
        return None

    offers = sorted(
        (r for r in rows if r["sk"].startswith("OFFER#")),
        key=lambda o: (o.get("stock", 0) <= 0, o["price"]),
    )
    variants = [r for r in rows if r["sk"].startswith("VARIANT#")]
    buy_box = next((o for o in offers if o.get("stock", 0) > 0), None)

    return {
        "item": item,
        "offers": offers,
        "buy_box": buy_box,
        "variants": variants,
        "variant_dims": _variant_dimensions(item, variants),
        "debug": {
            "key_condition": f"pk = ITEM#{asin}",
            "row_count": len(rows),
            "consumed_capacity": (resp.get("ConsumedCapacity") or {}).get("CapacityUnits"),
            "rows": [{"pk": r["pk"], "sk": r["sk"], "entity": r.get("kind")} for r in rows],
        },
    }


def _variant_dimensions(item, variants):
    """
    Build the swatch/button groups for the Relationships section.
    For each dimension (e.g. Color) list each value and which ASIN to link to:
    prefer the sibling that keeps the *other* dimensions the same as the current item.
    """
    current = item.get("variant_attrs", {})
    dims = []
    for dim in current:
        options = {}
        for v in variants:
            attrs = v["variant_attrs"]
            value = attrs.get(dim)
            if value is None:
                continue
            same_others = all(attrs.get(d) == current[d] for d in current if d != dim)
            if value not in options or same_others:
                options[value] = {
                    "value": value,
                    "asin": v["target_asin"],
                    "swatch": v.get("swatch") if dim == "Color" else None,
                    "exact": same_others,
                    "selected": value == current[dim] and same_others,
                }
        dims.append({"name": dim, "current": current[dim], "options": list(options.values())})
    return dims


# --------------------------------------------------------------------------- #
# Offers  (seller updates — the "Item Service ingesting updates" in the paper)
# --------------------------------------------------------------------------- #
def update_offer(asin, seller_id, price, stock):
    """
    Update price/stock of an existing offer. The ConditionExpression makes this
    fail instead of silently creating a new row if the offer does not exist.
    Also keeps METADATA.min_price in sync for the listing page.
    """
    price = Decimal(str(price))
    try:
        resp = table().update_item(
            Key={"pk": f"ITEM#{asin}", "sk": f"OFFER#{seller_id}"},
            UpdateExpression="SET price = :p, stock = :s, updated_at = :t ADD version :one",
            ConditionExpression="attribute_exists(pk)",
            ExpressionAttributeValues={
                ":p": price, ":s": int(stock), ":t": _now(), ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError(f"No offer from seller {seller_id} on {asin}")
        raise
    _refresh_min_price(asin)
    return resp["Attributes"]


def _refresh_min_price(asin):
    rows = table().query(
        KeyConditionExpression=Key("pk").eq(f"ITEM#{asin}") & Key("sk").begins_with("OFFER#")
    )["Items"]
    in_stock = [r["price"] for r in rows if r.get("stock", 0) > 0]
    table().update_item(
        Key={"pk": f"ITEM#{asin}", "sk": "METADATA"},
        UpdateExpression="SET min_price = :m",
        ExpressionAttributeValues={":m": min(in_stock) if in_stock else None},
    )


def list_all_offers():
    """For the seller portal dropdown: every (asin, seller) pair."""
    out = []
    # every variant, not just the default one shown on the home page
    resp = table().query(IndexName=ENTITY_INDEX, KeyConditionExpression=Key("entity_type").eq("ITEM"))
    for it in resp["Items"]:
        asin = it["asin"]
        offers = table().query(
            KeyConditionExpression=Key("pk").eq(f"ITEM#{asin}") & Key("sk").begins_with("OFFER#")
        )["Items"]
        for o in offers:
            out.append({**o, "title": it["title"], "variant_label": it.get("variant_label", "")})
    return sorted(out, key=lambda o: (o["title"], o["variant_label"], o["seller_name"]))


# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #
def new_cart_id():
    return uuid.uuid4().hex


def add_to_cart(cart_id, asin, seller_id, qty=1):
    page = get_item_page(asin)
    if page is None:
        raise ValueError("Unknown item")
    offer = next((o for o in page["offers"] if o["seller_id"] == seller_id), None)
    if offer is None:
        raise ValueError("Unknown offer")
    table().update_item(
        Key={"pk": f"CART#{cart_id}", "sk": f"ITEM#{asin}#{seller_id}"},
        UpdateExpression=(
            "ADD qty :q "
            "SET title = :t, price = :p, seller_name = :sn, asin = :a, seller_id = :sid, "
            "variant_label = :vl, image = :img, updated_at = :now"
        ),
        ExpressionAttributeValues={
            ":q": int(qty),
            ":t": page["item"]["title"],
            ":p": offer["price"],
            ":sn": offer["seller_name"],
            ":a": asin,
            ":sid": seller_id,
            ":vl": page["item"].get("variant_label", ""),
            ":img": page["item"].get("image", {}),
            ":now": _now(),
        },
    )


def get_cart(cart_id):
    rows = table().query(KeyConditionExpression=Key("pk").eq(f"CART#{cart_id}"))["Items"]
    subtotal = sum((r["price"] * r["qty"] for r in rows), Decimal("0"))
    count = sum(int(r["qty"]) for r in rows)
    return {"lines": rows, "subtotal": subtotal, "count": count}


def set_cart_qty(cart_id, asin, seller_id, qty):
    key = {"pk": f"CART#{cart_id}", "sk": f"ITEM#{asin}#{seller_id}"}
    if int(qty) <= 0:
        table().delete_item(Key=key)
    else:
        table().update_item(
            Key=key,
            UpdateExpression="SET qty = :q",
            ConditionExpression="attribute_exists(pk)",
            ExpressionAttributeValues={":q": int(qty)},
        )


def cart_count(cart_id):
    if not cart_id:
        return 0
    rows = table().query(
        KeyConditionExpression=Key("pk").eq(f"CART#{cart_id}"),
        ProjectionExpression="qty",
    )["Items"]
    return sum(int(r["qty"]) for r in rows)
def _all_offer_rows():
    """Fetch every offer record from DynamoDB."""
    kwargs = {
        "FilterExpression": "#kind = :offer",
        "ExpressionAttributeNames": {"#kind": "kind"},
        "ExpressionAttributeValues": {":offer": "OFFER"},
    }

    rows = []
    while True:
        response = table().scan(**kwargs)
        rows.extend(response.get("Items", []))

        if "LastEvaluatedKey" not in response:
            break

        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    return rows


def update_seller_details(old_seller_id, new_seller_id,
                          seller_name, ships_from, delivery, condition):
    """Update seller details across all offers, optionally changing seller ID."""

    old_seller_id = old_seller_id.strip()
    new_seller_id = new_seller_id.strip()
    seller_name = seller_name.strip()
    ships_from = ships_from.strip()
    delivery = delivery.strip()
    condition = condition.strip()

    if not all([old_seller_id, new_seller_id, seller_name,
                ships_from, delivery, condition]):
        raise ValueError("All seller fields are required.")

    rows = [
        r for r in _all_offer_rows()
        if r.get("seller_id") == old_seller_id
    ]

    if not rows:
        raise ValueError(f"Seller {old_seller_id} has no existing offers.")

    if old_seller_id != new_seller_id:
        # Check for collisions before migrating any records.
        for row in rows:
            response = table().get_item(
                Key={
                    "pk": row["pk"],
                    "sk": f"OFFER#{new_seller_id}"
                }
            )
            if "Item" in response:
                raise ValueError(
                    f"Seller ID {new_seller_id} already has an offer "
                    f"for product {row['asin']}."
                )

        # Copy each offer to its new key, then remove the old key.
        for row in rows:
            updated = dict(row)
            updated["sk"] = f"OFFER#{new_seller_id}"
            updated["seller_id"] = new_seller_id
            updated["seller_name"] = seller_name
            updated["ships_from"] = ships_from
            updated["delivery"] = delivery
            updated["condition"] = condition
            updated["updated_at"] = _now()
            updated["version"] = int(row.get("version", 1)) + 1

            table().put_item(
                Item=updated,
                ConditionExpression="attribute_not_exists(pk)"
            )

            table().delete_item(
                Key={"pk": row["pk"], "sk": row["sk"]}
            )

    else:
        # Same ID: update the seller details in-place.
        for row in rows:
            table().update_item(
                Key={"pk": row["pk"], "sk": row["sk"]},
                UpdateExpression=(
                    "SET seller_name = :name, ships_from = :ships, "
                    "delivery = :delivery, #cond = :condition, "
                    "updated_at = :now ADD version :one"
                ),
                ExpressionAttributeNames={"#cond": "condition"},
                ExpressionAttributeValues={
                    ":name": seller_name,
                    ":ships": ships_from,
                    ":delivery": delivery,
                    ":condition": condition,
                    ":now": _now(),
                    ":one": 1,
                },
                ConditionExpression="attribute_exists(pk)"
            )

    return len(rows)


def create_seller_offer(asin, seller_id, seller_name, ships_from,
                        delivery, condition, price, stock):
    """Create a new seller offer for an existing product."""

    asin = asin.strip()
    seller_id = seller_id.strip()
    seller_name = seller_name.strip()
    ships_from = ships_from.strip()
    delivery = delivery.strip()
    condition = condition.strip()

    if not all([asin, seller_id, seller_name, ships_from,
                delivery, condition]):
        raise ValueError("All seller fields are required.")

    try:
        price = Decimal(str(price))
        stock = int(stock)
    except Exception:
        raise ValueError("Enter a valid price and stock quantity.")

    if not price.is_finite() or price <= 0 or stock < 0:
        raise ValueError("Price must be positive and stock cannot be negative.")

    page = get_item_page(asin)
    if page is None:
        raise ValueError("Selected product does not exist.")

    key = {
        "pk": f"ITEM#{asin}",
        "sk": f"OFFER#{seller_id}"
    }

    try:
        table().put_item(
            Item={
                **key,
                "kind": "OFFER",
                "asin": asin,
                "seller_id": seller_id,
                "seller_name": seller_name,
                "ships_from": ships_from,
                "delivery": delivery,
                "condition": condition,
                "price": price,
                "list_price": page["item"].get("min_price") or price,
                "stock": stock,
                "version": 1,
                "updated_at": _now(),
            },
            ConditionExpression="attribute_not_exists(pk)"
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError(
                f"Seller {seller_id} already has an offer for this product."
            )
        raise

    _refresh_min_price(asin)