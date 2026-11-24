from decimal import Decimal, InvalidOperation
from typing import Union
from django.db.models import Sum
from core.models import StockLedger, Item

UNIT_CONVERSIONS = {
    "kg": Decimal("1000"),
    "g": Decimal("1"),
    "l": Decimal("1000"),
    "ml": Decimal("1"),
    "pcs": Decimal("1"),
}


def get_balance(item: Union[Item, int]) -> int:
    """Return integer sum of ledger quantity for that item (0 if no rows)."""
    total = StockLedger.objects.filter(item=item).aggregate(total=Sum("quantity"))["total"]
    return total if total is not None else 0


def to_base(amount: Union[str, Decimal, int], unit: str) -> int:
    """
    Convert an amount to its base unit as an integer.
    Supported units: kg->g (x1000), g->g, l->ml (x1000), ml->ml, pcs->pcs.
    Accepts amount as string or Decimal. Never uses float.
    Raises ValueError if result is not a whole number or unit is unknown.
    """
    if isinstance(amount, float):
        raise ValueError("Float amounts are not permitted; use Decimal or string.")

    if not isinstance(unit, str):
        raise ValueError(f"Unit must be a string, got {type(unit).__name__}")

    clean_unit = unit.strip().lower()
    if clean_unit not in UNIT_CONVERSIONS:
        raise ValueError(f"Unknown unit: '{unit}'")

    if isinstance(amount, Decimal):
        d_amount = amount
    elif isinstance(amount, (str, int)):
        try:
            d_amount = Decimal(str(amount).strip())
        except (InvalidOperation, TypeError):
            raise ValueError(f"Invalid amount value: {amount}")
    else:
        raise ValueError(f"Amount must be a string or Decimal, got {type(amount).__name__}")

    base_val = d_amount * UNIT_CONVERSIONS[clean_unit]

    if base_val != base_val.to_integral_value():
        raise ValueError(
            f"Converted amount ({base_val}) for unit '{unit}' is not a whole number."
        )

    return int(base_val)
