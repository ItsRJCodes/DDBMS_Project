# Amazon Items & Offers page on DynamoDB (Python / Flask)

An academic re-implementation of the **Amazon product detail page** described in the AWS
whitepaper *Modernizing the Amazon Database Infrastructure → Migration to Amazon DynamoDB –
Items and Offers*. The page is split into the paper's three components:

| Component         | What is shown on the page                         | DynamoDB row (`pk` / `sk`)              |
|-------------------|---------------------------------------------------|-----------------------------------------|
| **Item**          | title, brand, rating, bullets, details            | `ITEM#<asin>` / `METADATA`              |
| **Offer**         | price, M.R.P., seller, stock, delivery (buy box)  | `ITEM#<asin>` / `OFFER#<seller_id>`     |
| **Relationships** | variant swatches (Color, Configuration, Storage)  | `ITEM#<asin>` / `VARIANT#<other_asin>`  |
| Cart (extra)      | shopping cart lines                               | `CART#<session>` / `ITEM#<asin>#<seller>` |

All three components share one partition key, so **the whole product page is loaded with a single
`Query`**. Open the **"How this page was loaded from DynamoDB"** panel at the bottom of any product
page to see the rows that query returned.

---

## Project layout

```
dbms/
├── app.py            Flask routes (home, product page, cart, seller central)
├── db.py             All DynamoDB code (table creation, queries, updates)
├── setup_table.py    Step 1 – create the table
├── seed_data.py      Step 2 – load sample items, offers, relationships
├── templates/        Jinja2 HTML (Amazon-style UI)
├── static/style.css  Styling
├── tests/test_app.py Offline tests (moto fakes DynamoDB, no AWS cost)
├── requirements.txt
└── .env.example      Configuration template
```

---

## Step-by-step guide

### Step 0 — Prerequisites
- Python 3.10+ (`python3 --version`)
- An AWS account (you have access)

### Step 1 — Create an IAM user for the project
Don't use your root account keys.

1. AWS Console → **IAM** → **Users** → **Create user**, name it `dbms-project`.
2. **Attach policies directly** → choose **AmazonDynamoDBFullAccess** (fine for a class project).
3. Open the user → **Security credentials** → **Create access key** → *Local code*.
4. Copy the **Access key ID** and **Secret access key**. You only see the secret once.

### Step 2 — Give your computer the credentials
Install the AWS CLI (`brew install awscli` on macOS), then run:

```bash
aws configure
```

Enter the key ID, the secret, region `ap-south-1` (Mumbai, or any region you prefer), and output `json`.
This writes `~/.aws/credentials`, which `boto3` picks up automatically. **Never put the keys in your code.**

Check it works:

```bash
aws sts get-caller-identity
```

### Step 3 — Set up the Python environment
```bash
cd ~/Downloads/dbms
```
```bash
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```
```bash
pip install -r requirements.txt
```
```bash
cp .env.example .env
```
Edit `.env` if you chose a different region. Leave `DDB_ENDPOINT` empty to use real AWS.

### Step 4 — Create the DynamoDB table
```bash
python setup_table.py
```
This creates `ItemsAndOffers` with:
- **Partition key** `pk` (String), **sort key** `sk` (String)
- **On-demand** billing (you pay per request; this project's traffic costs well under ₹10)
- A **global secondary index** `EntityIndex` (`entity_type`, `title`). Only `METADATA` rows have
  `entity_type`, so the index holds items only (a *sparse index*). The home page uses it to list
  products with a `Query` instead of a full-table `Scan`.

Verify it in the console: **DynamoDB → Tables → ItemsAndOffers**.

### Step 5 — Load sample data
```bash
python seed_data.py
```
Expected output: `Wrote 110 rows to ItemsAndOffers: 12 items, 42 offers, 56 relationships`.

There are three product families (Echo Dot, Kindle Paperwhite, Fire TV Stick). Each variant
combination is its own ASIN with 3–4 seller offers. Some offers are out of stock, so you can see
the buy-box logic skip them. Browse the rows in **DynamoDB → Explore items**.

### Step 6 — Run the website
```bash
python app.py
```
Open http://127.0.0.1:8080. (Port 5000 is taken by AirPlay on macOS, so the app uses 8080.)

What to try:
1. **Home** — product cards, plus search in the top bar.
2. **Product page** (click a card) — the purple / red / blue tags mark the Item, Offer and
   Relationships sections. Click a color swatch or configuration to jump to the sibling ASIN.
3. **Add to Cart** — writes a `CART#…` row. Change the quantity or delete the line in the cart.
4. **Seller Central** (top right) — change an offer's price or stock. This runs `UpdateItem` with
   `ConditionExpression: attribute_exists(pk)` and bumps a `version` counter. Set a seller's price
   below the others and the product page's buy box switches to that seller.

### Step 7 — Run the tests (optional, offline)
```bash
python -m pytest -q
```
The tests use **moto**, which fakes DynamoDB in memory, so they never touch your AWS account.

### Step 8 — Clean up (avoid charges)
```bash
python setup_table.py --delete
```
Then deactivate or delete the IAM access key if you no longer need it.

---

## Running without AWS (DynamoDB Local)
To demo offline, run AWS's DynamoDB Local (needs Docker):
```bash
docker run -p 8000:8000 amazon/dynamodb-local
```
Set `DDB_ENDPOINT=http://localhost:8000` in `.env` and repeat Steps 4–6. Dummy credentials are fine.

---

## DynamoDB operations used (for your report)

| Feature                         | Operation                                                   | Where                    |
|---------------------------------|-------------------------------------------------------------|--------------------------|
| Load full product page          | `Query pk = ITEM#<asin>` (Item + Offers + Relationships)    | `db.get_item_page`       |
| List products                   | `Query` on GSI `EntityIndex` where `entity_type = ITEM`     | `db.list_items`          |
| Seller price/stock update       | `UpdateItem` + `ConditionExpression` + atomic `ADD version` | `db.update_offer`        |
| Only offers for an item         | `Query pk = … AND begins_with(sk, "OFFER#")`                | `db._refresh_min_price`  |
| Add to cart (increment qty)     | `UpdateItem … ADD qty :n` (atomic counter, upsert)          | `db.add_to_cart`         |
| Read cart                       | `Query pk = CART#<id>`                                      | `db.get_cart`            |
| Remove from cart                | `DeleteItem`                                                | `db.set_cart_qty`        |
| Bulk load                       | `BatchWriteItem` (via `batch_writer`)                       | `seed_data.seed`         |

**Why this design fits DynamoDB (vs. Oracle in the paper):** In a relational schema, the page
would join `items`, `offers`, `sellers` and `variants`. Here the access pattern ("show the page for
ASIN X") was designed first, and every row that pattern needs shares a partition key. Reads are a
single-partition `Query` with predictable latency at any scale, and there are no joins to tune.

## Deployment

The application is deployed using **Render** as the web hosting platform and **Amazon DynamoDB (AWS)** as the cloud database.

### Live Application

**Website:** https://ddbms-project.onrender.com

### Deployment Architecture

- **Frontend:** HTML, CSS, Jinja2 Templates
- **Backend:** Python, Flask
- **Application Server:** Gunicorn
- **Database:** Amazon DynamoDB
- **Cloud Hosting:** Render
- **Source Code Management:** GitHub

### Deployment Configuration

The application is deployed on Render using the following configuration:

| Configuration | Value |
|---|---|
| Hosting Platform | Render |
| Repository | `ItsRJCodes/DDBMS_Project` |
| Branch | `main` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --bind 0.0.0.0:$PORT app:app` |
| AWS Region | `ap-south-1` (Mumbai) |
| DynamoDB Table | `ItemsAndOffers` |

### Environment Variables

The following environment variables are configured in the Render dashboard:

| Variable | Description |
|---|---|
| `AWS_REGION` | AWS deployment region (`ap-south-1`) |
| `DDB_TABLE` | DynamoDB table name (`ItemsAndOffers`) |
| `DDB_ENDPOINT` | Empty for AWS cloud DynamoDB |
| `FLASK_SECRET_KEY` | Flask session security key |
| `AWS_ACCESS_KEY_ID` | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret access key |

**Security Note:** AWS credentials and application secrets are configured as environment variables in Render and are not stored in the GitHub repository.

### Deployment Steps

1. Push the application source code to the GitHub repository.
2. Connect the GitHub repository to Render.
3. Create a new Render Web Service.
4. Configure the Python runtime, build command and Gunicorn start command.
5. Add the required environment variables in Render.
6. Deploy the application.
7. Verify that the Flask application connects successfully to Amazon DynamoDB.

### Continuous Deployment

The Render service is connected to the GitHub repository. When updated code is pushed to the configured `main` branch, Render can automatically trigger a new deployment.

To update the deployed application:

```bash
git add .
git commit -m "Update application"
git push origin main
```

Render automatically builds and deploys the latest committed version.

### Production Database

The deployed application uses Amazon DynamoDB in the AWS Mumbai region.

The `ItemsAndOffers` table stores product metadata, seller offers, variant relationships and shopping cart records using a single-table design.

The application uses DynamoDB operations including:

- `Query` – Retrieve product information, offers and relationships.
- `UpdateItem` – Update seller prices, stock and cart quantities.
- `DeleteItem` – Remove shopping cart entries.
- `BatchWriteItem` – Load sample product data.

### Deployment Status

The application has been successfully deployed on Render and is accessible through its public URL.

**Live URL:** https://ddbms-project.onrender.com