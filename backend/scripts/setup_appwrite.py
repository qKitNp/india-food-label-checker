"""Create the Appwrite database and tables. Run once: uv run python -m scripts.setup_appwrite"""

from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.services.tables_db import TablesDB

from app.config import get_settings


def ignore_exists(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except AppwriteException as e:
        if e.code != 409:
            raise
        print(f"  exists: {args[1:3]}")


def main():
    s = get_settings()
    if not s.appwrite_project_id:
        raise SystemExit("Set APPWRITE_PROJECT_ID and APPWRITE_API_KEY in .env first")
    db = TablesDB(Client().set_endpoint(s.appwrite_endpoint).set_project(s.appwrite_project_id)
                  .set_key(s.appwrite_api_key))
    d = s.appwrite_database_id

    ignore_exists(db.create, d, "Better FSSAI")

    t = s.appwrite_products_table
    ignore_exists(db.create_table, d, t, "Products")
    ignore_exists(db.create_varchar_column, d, t, "barcode", 32, True)
    ignore_exists(db.create_varchar_column, d, t, "name", 255, False)
    ignore_exists(db.create_varchar_column, d, t, "brand", 128, False)
    ignore_exists(db.create_varchar_column, d, t, "source", 16, True)
    ignore_exists(db.create_boolean_column, d, t, "verified", True)
    ignore_exists(db.create_longtext_column, d, t, "payload", True)

    t = s.appwrite_counterparts_table
    ignore_exists(db.create_table, d, t, "Counterparts")
    ignore_exists(db.create_varchar_column, d, t, "barcode", 32, True)
    ignore_exists(db.create_varchar_column, d, t, "region", 4, True)
    ignore_exists(db.create_varchar_column, d, t, "source_url", 2048, True)
    ignore_exists(db.create_longtext_column, d, t, "payload", True)

    t = s.appwrite_scan_logs_table
    ignore_exists(db.create_table, d, t, "Scan logs")
    ignore_exists(db.create_varchar_column, d, t, "job_id", 32, True)
    ignore_exists(db.create_varchar_column, d, t, "status", 16, True)
    ignore_exists(db.create_varchar_column, d, t, "outcome", 16, False)
    ignore_exists(db.create_varchar_column, d, t, "product_key", 64, False)
    ignore_exists(db.create_integer_column, d, t, "duration_ms", True)
    ignore_exists(db.create_text_column, d, t, "error", False)
    ignore_exists(db.create_longtext_column, d, t, "steps", True)
    ignore_exists(db.create_longtext_column, d, t, "usage", False)
    ignore_exists(db.create_text_column, d, t, "llm", False)
    print("Done")


if __name__ == "__main__":
    main()
