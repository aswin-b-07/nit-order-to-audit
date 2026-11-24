from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Union, List, Tuple
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from core.models import (
    StockLedger,
    Item,
    Supplier,
    Purchase,
    BOM,
    BOMLine,
    ProductionRun,
    ProductionRunLine,
    Customer,
    ProductPrice,
    Order,
    OrderLine,
)

UNIT_CONVERSIONS = {
    "kg": Decimal("1000"),
    "g": Decimal("1"),
    "l": Decimal("1000"),
    "ml": Decimal("1"),
    "pcs": Decimal("1"),
}

VALID_PURCHASE_UNITS = {
    "g": {"kg", "g"},
    "ml": {"l", "ml"},
    "pcs": {"pcs"},
}


def get_balance(item: Union[Item, int]) -> int:
    """Return integer sum of ledger quantity for that item (0 if no rows)."""
    total = StockLedger.objects.filter(item=item).aggregate(total=Sum("quantity"))["total"]
    return total if total is not None else 0


def get_value(item: Union[Item, int]) -> int:
    """Return sum of value_paise in the ledger for that item (0 if none)."""
    total = StockLedger.objects.filter(item=item).aggregate(total=Sum("value_paise"))["total"]
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


def value_for_issue(item: Union[Item, int], quantity_base: int) -> int:
    """
    Calculate the weighted-average cost in paise of issuing quantity_base.
    Formula: round-half-up of (quantity_base * get_value(item) / get_balance(item)), computed with Decimal.
    Raises ValueError if the balance is 0 or less, or if quantity_base is more than the balance.
    """
    if quantity_base < 0:
        raise ValueError(f"Quantity to issue cannot be negative ({quantity_base}).")

    balance = get_balance(item)
    if balance <= 0:
        raise ValueError(
            f"Item balance must be greater than 0 to issue stock (current balance: {balance})."
        )

    if quantity_base > balance:
        raise ValueError(
            f"Cannot issue quantity {quantity_base}; exceeds current balance {balance}."
        )

    value = get_value(item)
    cost = (Decimal(quantity_base) * Decimal(value)) / Decimal(balance)
    return int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def record_purchase(
    item: Item,
    amount: Union[str, Decimal, int],
    unit: str,
    rate_rupees: Union[str, Decimal, int],
    supplier: Supplier,
    purchase_date,
    invoice_no: str = "",
    user=None,
) -> Purchase:
    """
    Record an inward stock purchase from a supplier.
    Converts amount to base unit, calculates total value in paise using round-half-up,
    creates Purchase record, and appends a PURCHASE row to StockLedger.
    """
    if item.kind == Item.Kind.FINISHED:
        raise ValueError(f"Cannot record purchase for finished good '{item.name}'.")

    if isinstance(amount, float) or isinstance(rate_rupees, float):
        raise ValueError("Float amounts or rates are not permitted; use Decimal or string.")

    if not isinstance(unit, str):
        raise ValueError(f"Unit must be a string, got {type(unit).__name__}")

    clean_unit = unit.strip().lower()
    allowed_units = VALID_PURCHASE_UNITS.get(item.base_unit, set())
    if clean_unit not in allowed_units:
        raise ValueError(
            f"Unit '{unit}' does not match item base unit '{item.base_unit}'."
        )

    quantity = to_base(amount, unit)

    try:
        d_amount = Decimal(str(amount).strip())
        d_rate = Decimal(str(rate_rupees).strip())
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid amount or rate_rupees value.")

    raw_paise = d_amount * d_rate * Decimal("100")
    total_value_paise = int(raw_paise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    with transaction.atomic():
        purchase = Purchase.objects.create(
            supplier=supplier,
            item=item,
            quantity=quantity,
            total_value_paise=total_value_paise,
            invoice_no=invoice_no,
            purchase_date=purchase_date,
            created_by=user,
        )
        StockLedger.objects.create(
            item=item,
            movement_type=StockLedger.MovementType.PURCHASE,
            quantity=quantity,
            value_paise=total_value_paise,
            source_type="Purchase",
            source_id=purchase.id,
            created_by=user,
            note=f"Purchase from {supplier.name}",
        )
        return purchase


def variance_bp(actual: int, standard: int) -> int:
    """
    Calculate variance in basis points: ((actual - standard) * 10000 / standard).
    Rounded using ROUND_HALF_UP.
    Raises ValueError if standard <= 0.
    """
    if standard <= 0:
        raise ValueError(f"standard must be strictly positive, got {standard}")

    d_actual = Decimal(actual)
    d_standard = Decimal(standard)
    res = ((d_actual - d_standard) * Decimal(10000)) / d_standard
    return int(res.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def is_flagged(variance_bp_value: int, tolerance_bp: int) -> bool:
    """True when abs(variance_bp_value) > tolerance_bp (strictly greater)."""
    return abs(variance_bp_value) > tolerance_bp


def record_production_run(
    bom: BOM,
    actual_output: int,
    lines: dict,
    batches: int = 1,
    tolerance_percent=None,
    user=None,
) -> ProductionRun:
    """
    Execute a production run for a BOM.
    Issues raw materials/packaging from stock at weighted average cost,
    records production output for finished goods, computes variances and flags,
    and creates the ProductionRun record inside a single atomic transaction.
    """
    if batches < 1:
        raise ValueError("batches must be at least 1.")

    if actual_output < 0:
        raise ValueError("actual_output cannot be negative.")

    if tolerance_percent is None:
        tolerance_percent = getattr(settings, "VARIANCE_TOLERANCE_PERCENT", 3)
    tolerance_bp = int(Decimal(str(tolerance_percent)) * 100)

    # Validate lines against BOM items
    bom_lines_list = list(bom.lines.select_related("item").all())
    lines_by_item_id = {}
    for key, spec in lines.items():
        if isinstance(key, Item):
            lines_by_item_id[key.id] = (key, spec)
        elif isinstance(key, int):
            lines_by_item_id[key] = (key, spec)
        else:
            raise ValueError(f"Invalid key in lines dictionary: {key}")

    bom_item_ids = {bl.item_id for bl in bom_lines_list}
    if set(lines_by_item_id.keys()) != bom_item_ids:
        raise ValueError("lines does not contain exactly the BOM's items.")

    # Validate line quantities and wastage
    for bl in bom_lines_list:
        _, spec = lines_by_item_id[bl.item_id]
        if not isinstance(spec, dict):
            raise ValueError(f"Line specification for '{bl.item.name}' must be a dict.")
        qty = spec.get("qty")
        wastage = spec.get("wastage", 0)
        if qty is None or qty < 0:
            raise ValueError(f"Quantity for '{bl.item.name}' cannot be negative.")
        if wastage < 0 or wastage > qty:
            raise ValueError(f"Wastage for '{bl.item.name}' must be between 0 and qty.")

    expected_output = bom.expected_yield * batches
    yield_variance_bp = variance_bp(actual_output, expected_output)
    yield_flagged = is_flagged(yield_variance_bp, tolerance_bp)

    with transaction.atomic():
        # Create ProductionRun first to obtain an id for ledger source_id
        run = ProductionRun.objects.create(
            bom=bom,
            batches=batches,
            expected_output=expected_output,
            actual_output=actual_output,
            tolerance_bp=tolerance_bp,
            yield_variance_bp=yield_variance_bp,
            yield_flagged=yield_flagged,
            material_cost_paise=0,
            created_by=user,
        )

        total_material_cost_paise = 0

        for bl in bom_lines_list:
            item = bl.item
            _, spec = lines_by_item_id[bl.item_id]
            qty = spec.get("qty")
            wastage = spec.get("wastage", 0)

            standard_qty = bl.quantity * batches
            line_var_bp = variance_bp(qty, standard_qty)
            line_flagged = is_flagged(line_var_bp, tolerance_bp)

            if qty > 0:
                cost = value_for_issue(item, qty)
                StockLedger.objects.create(
                    item=item,
                    movement_type=StockLedger.MovementType.PROD_ISSUE,
                    quantity=-qty,
                    value_paise=-cost,
                    source_type="ProductionRun",
                    source_id=run.id,
                    created_by=user,
                    note=f"Production issue for run #{run.id}",
                )
            else:
                cost = 0

            ProductionRunLine.objects.create(
                run=run,
                item=item,
                standard_qty=standard_qty,
                actual_qty=qty,
                wastage_qty=wastage,
                variance_bp=line_var_bp,
                flagged=line_flagged,
                cost_paise=cost,
            )

            total_material_cost_paise += cost

        # Create the finished-goods StockLedger row if actual_output > 0
        if actual_output > 0:
            StockLedger.objects.create(
                item=bom.product,
                movement_type=StockLedger.MovementType.PROD_OUTPUT,
                quantity=actual_output,
                value_paise=total_material_cost_paise,
                source_type="ProductionRun",
                source_id=run.id,
                created_by=user,
                note=f"Production output for run #{run.id}",
            )

        run.material_cost_paise = total_material_cost_paise
        run.save()

        return run


def order_total_paise(order: Order) -> int:
    """Return total order value in paise: sum of (quantity * unit_price_paise) over lines."""
    return sum(line.quantity * line.unit_price_paise for line in order.lines.all())


def place_order(
    customer: Customer,
    salesman,
    lines: List[Tuple[Item, int]],
) -> Order:
    """
    Place a sales order.
    lines is a list of (product_item, quantity).
    Validates at least one line, each quantity >= 1, each product is FINISHED
    and has a ProductPrice, and no product appears twice.
    Creates Order and OrderLine instances within a single atomic transaction.
    """
    if not lines:
        raise ValueError("Order must have at least one line.")

    seen_products = set()
    validated_lines = []

    for product_item, quantity in lines:
        if quantity < 1:
            raise ValueError(f"Quantity must be at least 1, got {quantity} for '{product_item.name}'.")

        if product_item.kind != Item.Kind.FINISHED:
            raise ValueError(f"Product '{product_item.name}' is not a finished good.")

        try:
            price_obj = ProductPrice.objects.get(product=product_item)
        except ProductPrice.DoesNotExist:
            raise ValueError(f"Product '{product_item.name}' does not have a configured price.")

        if product_item.pk in seen_products:
            raise ValueError(f"Duplicate product '{product_item.name}' in order lines.")
        seen_products.add(product_item.pk)

        validated_lines.append((product_item, quantity, price_obj.price_paise))

    with transaction.atomic():
        order = Order.objects.create(
            customer=customer,
            salesman=salesman,
            status=Order.Status.SUBMITTED,
        )
        for prod, qty, unit_price in validated_lines:
            OrderLine.objects.create(
                order=order,
                product=prod,
                quantity=qty,
                unit_price_paise=unit_price,
            )
        return order


def stock_report(order: Order) -> list:
    """
    Generate stock availability report for lines in an order.
    Returns list of dicts: {"product", "ordered", "available", "shortage"}.
    """
    report = []
    for line in order.lines.select_related("product").all():
        available = get_balance(line.product)
        shortage = max(line.quantity - available, 0)
        report.append({
            "product": line.product,
            "ordered": line.quantity,
            "available": available,
            "shortage": shortage,
        })
    return report


def update_order_line(
    order: Order,
    product: Item,
    quantity: int,
    unit_price_paise: int = None,
    user=None,
) -> OrderLine:
    """
    Update quantity and optionally unit price on an order line.
    Only permitted when order status is SUBMITTED.
    """
    if order.status != Order.Status.SUBMITTED:
        raise ValueError(
            f"Cannot update line for order #{order.id} with status '{order.status}'. "
            "Only SUBMITTED orders can be modified."
        )

    if quantity < 1:
        raise ValueError(f"Quantity must be at least 1, got {quantity}.")

    try:
        line = order.lines.get(product=product)
    except OrderLine.DoesNotExist:
        raise ValueError(f"Product '{product.name}' is not in order #{order.id}.")

    line.quantity = quantity
    if unit_price_paise is not None:
        if unit_price_paise < 0:
            raise ValueError("unit_price_paise cannot be negative.")
        line.unit_price_paise = unit_price_paise
    line.save()
    return line


def approve_order(order: Order, user=None) -> Order:
    """
    Approve an order if stock is sufficient.
    Only allowed from SUBMITTED status.
    Raises ValueError detailing shortage if any product is insufficient.
    """
    if order.status != Order.Status.SUBMITTED:
        raise ValueError(
            f"Cannot approve order #{order.id} with status '{order.status}'. "
            "Only SUBMITTED orders can be approved."
        )

    report = stock_report(order)
    shortages = [r for r in report if r["shortage"] > 0]
    if shortages:
        shortage_msgs = [f"{r['product'].name} short by {r['shortage']}" for r in shortages]
        raise ValueError(f"Insufficient stock: {', '.join(shortage_msgs)}")

    order.status = Order.Status.APPROVED
    order.decided_by = user
    order.decided_at = timezone.now()
    order.save()
    return order


def reject_order(order: Order, user=None, reason: str = "") -> Order:
    """
    Reject an order with a reason.
    Only allowed from SUBMITTED status.
    """
    if order.status != Order.Status.SUBMITTED:
        raise ValueError(
            f"Cannot reject order #{order.id} with status '{order.status}'. "
            "Only SUBMITTED orders can be rejected."
        )

    if not reason or not reason.strip():
        raise ValueError("Reject reason must not be empty.")

    order.status = Order.Status.REJECTED
    order.reject_reason = reason.strip()
    order.decided_by = user
    order.decided_at = timezone.now()
    order.save()
    return order
