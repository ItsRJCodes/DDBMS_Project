"""
Step 1: create the DynamoDB table.

    python setup_table.py            # create (no-op if it already exists)
    python setup_table.py --delete   # delete the table (clean up to avoid charges)
"""
import sys

from dotenv import load_dotenv

load_dotenv()
import db  # noqa: E402

if __name__ == "__main__":
    if "--delete" in sys.argv:
        db.delete_table()
        print(f"Deleted table {db.TABLE_NAME} in {db.REGION}")
    else:
        created = db.create_table()
        state = "Created" if created else "Table already exists:"
        print(f"{state} {db.TABLE_NAME} in {db.REGION}" + (f" via {db.ENDPOINT}" if db.ENDPOINT else ""))
