"""
Move payment screenshots from disk into the database.

Two problems with the old disk-backed approach:

  1. hosted filesystems are ephemeral, so every deploy deleted the images
  2. they were served from a public static mount, meaning any shop's payment
     screenshots were readable by anyone holding the URL

Adds the binary columns and backfills any images still present on disk.
`transaction_screenshot_url` is left in place so rows whose file is already
gone keep their original value rather than silently changing meaning.

Idempotent: safe to run more than once.

    python -m migrations.004_screenshot_in_db
"""
import mimetypes
import os

from sqlalchemy import text

from app.database import engine

UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads")

COLUMNS = {
    "screenshot_data": "BYTEA",
    "screenshot_mime": "VARCHAR(100)",
}


def migrate() -> None:
    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'bills'
                    """
                )
            )
        }

        for column, column_type in COLUMNS.items():
            if column in existing:
                print(f"· bills.{column} already present")
                continue
            print(f"· adding bills.{column}")
            conn.execute(text(f"ALTER TABLE bills ADD COLUMN {column} {column_type}"))

        pending = conn.execute(
            text(
                """
                SELECT id, transaction_screenshot_url FROM bills
                WHERE transaction_screenshot_url IS NOT NULL
                  AND screenshot_data IS NULL
                """
            )
        ).fetchall()

        migrated = missing = 0
        for bill_id, path in pending:
            candidate = path
            if not os.path.isfile(candidate):
                # Stored paths are relative to the project root; fall back to
                # resolving the bare filename inside the uploads directory.
                candidate = os.path.join(UPLOADS_DIR, os.path.basename(path))
            if not os.path.isfile(candidate):
                missing += 1
                continue

            with open(candidate, "rb") as handle:
                data = handle.read()
            mime = mimetypes.guess_type(candidate)[0] or "application/octet-stream"

            conn.execute(
                text(
                    """
                    UPDATE bills
                    SET screenshot_data = :data, screenshot_mime = :mime
                    WHERE id = :id
                    """
                ),
                {"data": data, "mime": mime, "id": bill_id},
            )
            migrated += 1

        print(f"· moved {migrated} screenshot(s) into the database")
        if missing:
            print(
                f"· {missing} row(s) reference a file that no longer exists "
                "— they keep their original path and will 404 on download"
            )

    print("✓ migration complete")


if __name__ == "__main__":
    migrate()
