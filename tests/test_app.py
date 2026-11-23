"""Offline tests: moto fakes DynamoDB in memory, so no AWS account is touched."""
import os
import sys

import pytest
from moto import mock_aws

os.environ.update(AWS_REGION="ap-south-1", DDB_TABLE="ItemsAndOffers-test", DDB_ENDPOINT="",
                  AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db  # noqa: E402
import seed_data  # noqa: E402
from app import app  # noqa: E402


@pytest.fixture
def client():
    with mock_aws():
        db.create_table()
        seed_data.seed()
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c


def test_home_lists_one_card_per_family(client):
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="card"') == 3
    assert "Echo Dot (3rd Gen)" in html


def test_product_page_loads_item_offers_relationships_in_one_query(client):
    page = db.get_item_page("B0ECHO0001")
    assert page["item"]["variant_attrs"] == {"Color": "Charcoal", "Configuration": "Echo Dot"}
    assert len(page["offers"]) == 4
    assert page["buy_box"]["seller_id"] == "TECHD"  # cheapest in-stock offer
    assert len(page["variants"]) == 6
    colors = [o["value"] for o in page["variant_dims"][0]["options"]]
    assert colors == ["Charcoal", "Heather Grey", "Sandstone"]

    html = client.get("/dp/B0ECHO0001").get_data(as_text=True)
    assert "Add to Cart" in html and "Other sellers on Amazon" in html
    assert "VARIANT#B0ECHO0002" in html  # debug panel lists the relationship rows


def test_out_of_stock_offer_never_wins_buy_box(client):
    page = db.get_item_page("B0ECHO0001")
    renewed = next(o for o in page["offers"] if o["seller_id"] == "RENEW")
    assert renewed["price"] < page["buy_box"]["price"]  # cheaper, but stock = 0


def test_unknown_item_is_404(client):
    assert client.get("/dp/NOPE").status_code == 404


def test_cart_add_update_delete(client):
    client.post("/cart/add", data={"asin": "B0KNDL0001", "seller_id": "AMZN", "qty": 2})
    client.post("/cart/add", data={"asin": "B0KNDL0001", "seller_id": "AMZN", "qty": 1})
    html = client.get("/cart").get_data(as_text=True)
    assert "Subtotal (3 items)" in html

    client.post("/cart/update", data={"asin": "B0KNDL0001", "seller_id": "AMZN", "qty": 5})
    assert "Subtotal (5 items)" in client.get("/cart").get_data(as_text=True)

    client.post("/cart/update", data={"asin": "B0KNDL0001", "seller_id": "AMZN", "qty": 5, "delete": 1})
    assert "Your Amazon Cart is empty" in client.get("/cart").get_data(as_text=True)


def test_seller_update_changes_buy_box_and_bumps_version(client):
    resp = client.post("/seller", data={"offer": "B0FTVS0001|GADG", "price": "999", "stock": "4"},
                       follow_redirects=True)
    assert "version 2" in resp.get_data(as_text=True)
    page = db.get_item_page("B0FTVS0001")
    assert page["buy_box"]["seller_id"] == "GADG"
    assert page["item"]["min_price"] == 999


def test_update_of_missing_offer_is_rejected_by_condition(client):
    with pytest.raises(ValueError):
        db.update_offer("B0FTVS0001", "NOBODY", 10, 1)
    assert len(db.get_item_page("B0FTVS0001")["offers"]) == 4  # no phantom row created
