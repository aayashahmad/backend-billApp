"""
Carrying a customer's balance over from the shop's paper khata.

The arithmetic lives here rather than in the router because it moves real
money on a customer's account, and because changing an opening balance has
to unwind the old one first — applying the new figure on top would double
whatever was already there, quietly, on the one day a shop is least able to
notice.
"""

from datetime import datetime

from app.models import Customer

DUE = "due"
ADVANCE = "advance"
VALID_TYPES = (DUE, ADVANCE)


def _signed(amount, balance_type) -> float:
    """
    One number for a customer's position: positive is credit they hold,
    negative is debt they owe.

    The two sides are stored separately and never both set, so collapsing
    them to a single signed figure is the only way to add and subtract
    across the boundary without the sign logic appearing four times.
    """
    if not amount or not balance_type:
        return 0.0
    value = float(amount)
    return value if balance_type == ADVANCE else -value


def current_contribution(customer: Customer) -> float:
    """What this customer's existing opening balance is worth, signed."""
    return _signed(customer.opening_balance, customer.opening_balance_type)


def apply_opening_balance(
    customer: Customer,
    amount: float = None,
    balance_type: str = None,
    as_of: datetime = None,
    reference: str = None,
    note: str = None,
) -> None:
    """
    Sets or replaces the customer's opening balance and moves their totals.

    Passing amount=None clears it, which is how a shop undoes a migration
    typo — the old contribution is removed and nothing replaces it.
    """
    net = float(customer.advance_balance or 0) - float(customer.total_unpaid or 0)

    # Take the old opening balance back out before putting the new one in.
    net -= current_contribution(customer)

    if amount is None or float(amount) == 0:
        customer.opening_balance = None
        customer.opening_balance_type = None
        customer.opening_balance_date = None
        customer.opening_balance_ref = None
        customer.opening_balance_note = None
        customer.opening_balance_set_at = None
    else:
        value = round(abs(float(amount)), 2)
        kind = balance_type if balance_type in VALID_TYPES else DUE
        customer.opening_balance = value
        customer.opening_balance_type = kind
        customer.opening_balance_date = as_of
        customer.opening_balance_ref = (reference or "").strip() or None
        customer.opening_balance_note = (note or "").strip() or None
        customer.opening_balance_set_at = datetime.utcnow()
        net += _signed(value, kind)

    # Split back into the two fields the rest of the app reads. A customer
    # never holds credit and debt at once, so one of these is always zero.
    net = round(net, 2)
    if net >= 0:
        customer.advance_balance = net
        customer.total_unpaid = 0
    else:
        customer.advance_balance = 0
        customer.total_unpaid = -net
