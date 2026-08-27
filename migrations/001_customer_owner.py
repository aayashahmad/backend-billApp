"""
Scope customers to an owner.

`Base.metadata.create_all` only creates missing tables — it never alters an
existing one — so this change has to be applied explicitly.

What it does:
  1. adds customers.user_id (nullable at first, so existing rows survive)
  2. backfills every ownerless customer to OWNER_USER_ID
  3. makes user_id NOT NULL and adds the FK + index
  4. drops the global-unique phone index and replaces it with a
     (user_id, phone) composite — two shops may bill the same person

Idempotent: safe to run more than once.

    python -m migrations.001_customer_owner            # backfill to default
    python -m migrations.001_customer_owner --owner 4  # or an explicit owner
"""
import argparse
import sys

from sqlalchemy import text

from app.database import engine

# The account that inherits customers created before ownership existed.
DEFAULT_OWNER_USER_ID = 4


def _scalar(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def migrate(owner_user_id: int) -> None:
    with engine.begin() as conn:
        owner_exists = _scalar(
            conn, "SELECT count(*) FROM users WHERE id = :uid", uid=owner_user_id
        )
        if not owner_exists:
            raise SystemExit(
                f"User id {owner_user_id} does not exist — pass a valid --owner."
            )

        has_column = _scalar(
            conn,
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_name = 'customers' AND column_name = 'user_id'
            """,
        )

        if not has_column:
            print("· adding customers.user_id")
            conn.execute(text("ALTER TABLE customers ADD COLUMN user_id INTEGER"))
        else:
            print("· customers.user_id already present")

        orphaned = _scalar(
            conn, "SELECT count(*) FROM customers WHERE user_id IS NULL"
        )
        if orphaned:
            print(f"· assigning {orphaned} ownerless customer(s) to user {owner_user_id}")
            conn.execute(
                text("UPDATE customers SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": owner_user_id},
            )
        else:
            print("· no ownerless customers to backfill")

        # Only enforce NOT NULL once every row has an owner.
        conn.execute(text("ALTER TABLE customers ALTER COLUMN user_id SET NOT NULL"))

        has_fk = _scalar(
            conn,
            """
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_name = 'customers'
              AND constraint_name = 'fk_customers_user_id'
            """,
        )
        if not has_fk:
            print("· adding foreign key customers.user_id -> users.id")
            conn.execute(
                text(
                    """
                    ALTER TABLE customers
                    ADD CONSTRAINT fk_customers_user_id
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    """
                )
            )

        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_customers_user_id ON customers (user_id)")
        )

        # Phone was globally unique; it must now be unique per owner.
        print("· replacing global phone uniqueness with (user_id, phone)")
        conn.execute(text("DROP INDEX IF EXISTS ix_customers_phone"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_customers_phone ON customers (phone)")
        )

        has_composite = _scalar(
            conn,
            """
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_name = 'customers'
              AND constraint_name = 'uq_customers_user_phone'
            """,
        )
        if not has_composite:
            duplicates = _scalar(
                conn,
                """
                SELECT count(*) FROM (
                    SELECT user_id, phone FROM customers
                    GROUP BY user_id, phone HAVING count(*) > 1
                ) d
                """,
            )
            if duplicates:
                raise SystemExit(
                    f"{duplicates} (owner, phone) pair(s) are duplicated — "
                    "merge them before adding the unique constraint."
                )
            conn.execute(
                text(
                    """
                    ALTER TABLE customers
                    ADD CONSTRAINT uq_customers_user_phone UNIQUE (user_id, phone)
                    """
                )
            )
        else:
            print("· (user_id, phone) constraint already present")

    print("✓ migration complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner",
        type=int,
        default=DEFAULT_OWNER_USER_ID,
        help="user id that inherits pre-existing customers",
    )
    args = parser.parse_args()
    sys.exit(migrate(args.owner))
