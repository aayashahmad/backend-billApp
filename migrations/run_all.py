"""
Apply every automatic migration, in filename order.

`Base.metadata.create_all` in app/main.py creates missing *tables* but never
alters an existing one. So a database created before a column was added keeps
the old shape forever, and the first query touching the new column fails with
UndefinedColumn — a 500 that looks nothing like a schema problem from the
client side. Deploys have to apply migrations explicitly; this is the runner
that does it.

Migrations whose `migrate()` takes required arguments are SKIPPED: those carry
a decision a deploy cannot make on its own (001 backfills pre-existing
customers to an owner id, and guessing wrong writes bad ownership rows). Run
those by hand:

    python -m migrations.001_customer_owner --owner <id>

Every migration is idempotent, so re-running on each deploy is a no-op once
applied. Exits non-zero on the first failure, so a broken migration fails the
deploy instead of leaving the service up on a half-applied schema.

    python -m migrations.run_all
"""
import importlib
import inspect
import pkgutil
import sys


def _migration_modules():
    """Migration module names (NNN_name), in numeric filename order."""
    package = importlib.import_module("migrations")
    names = [
        name
        for _, name, _ in pkgutil.iter_modules(package.__path__)
        if name[:3].isdigit()
    ]
    return sorted(names)


def _requires_arguments(func) -> bool:
    return any(
        param.default is inspect.Parameter.empty
        and param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for param in inspect.signature(func).parameters.values()
    )


def main() -> int:
    for name in _migration_modules():
        # import_module, not `import`: module names start with a digit, which
        # the import statement rejects as a syntax error.
        module = importlib.import_module(f"migrations.{name}")
        migrate = getattr(module, "migrate", None)

        if migrate is None:
            print(f"— {name}: no migrate(), skipping")
            continue

        if _requires_arguments(migrate):
            print(f"— {name}: needs arguments, skipping (run it manually)")
            continue

        print(f"▶ {name}")
        try:
            migrate()
        except Exception as exc:  # noqa: BLE001 — deploy must see the reason
            print(f"✗ {name} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    print("✓ migrations up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
