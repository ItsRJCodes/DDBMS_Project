"""
Flask web app: Amazon-style product detail page backed by DynamoDB.

Run:  python app.py      then open http://127.0.0.1:8080
"""
import os

from dotenv import load_dotenv

load_dotenv()  # must run before importing db so env vars are visible

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for  # noqa: E402

import db  # noqa: E402

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")


def _cart_id(create=False):
    if "cart_id" not in session and create:
        session["cart_id"] = db.new_cart_id()
    return session.get("cart_id")


@app.context_processor
def inject_globals():
    try:
        count = db.cart_count(_cart_id())
    except Exception:
        count = 0
    return {"cart_count": count, "table_name": db.TABLE_NAME, "region": db.REGION}


@app.template_filter("money")
def money(value):
    if value is None:
        return "—"
    return f"₹{value:,.2f}"


@app.route("/")
def home():
    q = request.args.get("q", "").strip()
    return render_template("index.html", items=db.list_items(q or None), q=q)


@app.route("/dp/<asin>")
def product(asin):
    page = db.get_item_page(asin)
    if page is None:
        abort(404)
    return render_template("product.html", **page)


@app.post("/cart/add")
def cart_add():
    asin = request.form["asin"]
    seller_id = request.form["seller_id"]
    qty = int(request.form.get("qty", 1))
    try:
        db.add_to_cart(_cart_id(create=True), asin, seller_id, qty)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("product", asin=asin))
    flash("Added to Cart", "success")
    return redirect(url_for("cart"))


@app.route("/cart")
def cart():
    cid = _cart_id()
    data = db.get_cart(cid) if cid else {"lines": [], "subtotal": 0, "count": 0}
    return render_template("cart.html", **data)


@app.post("/cart/update")
def cart_update():
    cid = _cart_id()
    if cid:
        qty = 0 if request.form.get("delete") else int(request.form["qty"])
        db.set_cart_qty(cid, request.form["asin"], request.form["seller_id"], qty)
    return redirect(url_for("cart"))


@app.route("/seller", methods=["GET", "POST"])
def seller():
    if request.method == "POST":
        action = request.form.get("action")

        try:
            if action == "update_offer":
                asin, seller_id = request.form["offer"].split("|")
                updated = db.update_offer(
                    asin, seller_id,
                    request.form["price"],
                    request.form["stock"]
                )
                flash(
                    f"Offer updated: {updated['seller_name']} — "
                    f"{money(updated['price'])}, stock {updated['stock']}",
                    "success"
                )

            elif action == "update_seller":
                count = db.update_seller_details(
                    request.form["old_seller_id"],
                    request.form["new_seller_id"],
                    request.form["seller_name"],
                    request.form["ships_from"],
                    request.form["delivery"],
                    request.form["condition"]
                )
                flash(f"Seller details updated across {count} offers.", "success")

            elif action == "add_seller":
                db.create_seller_offer(
                    request.form["asin"],
                    request.form["seller_id"],
                    request.form["seller_name"],
                    request.form["ships_from"],
                    request.form["delivery"],
                    request.form["condition"],
                    request.form["price"],
                    request.form["stock"]
                )
                flash("New seller offer added successfully.", "success")

            else:
                flash("Invalid action.", "error")

        except (ValueError, KeyError) as e:
            flash(str(e), "error")

        return redirect(url_for("seller"))

    offers = db.list_all_offers()
    sellers = {}

    for offer in offers:
        sellers[offer["seller_id"]] = offer

    return render_template(
        "seller.html",
        offers=offers,
        sellers=sorted(sellers.values(), key=lambda s: s["seller_id"]),
        products=db.list_items()
    )


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    # Port 5000 is used by AirPlay on macOS, so default to 8080.
    app.run(debug=True, port=int(os.getenv("PORT", 8080)))
