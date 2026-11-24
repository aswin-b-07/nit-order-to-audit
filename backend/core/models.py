from django.conf import settings
from django.db import models


class Item(models.Model):
    """An inventory item tracked by the system."""

    class Kind(models.TextChoices):
        RAW = "RAW", "Raw material"
        PACKAGING = "PACKAGING", "Packaging"
        FINISHED = "FINISHED", "Finished good"

    class BaseUnit(models.TextChoices):
        G = "g", "g"
        ML = "ml", "ml"
        PCS = "pcs", "pcs"

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    base_unit = models.CharField(max_length=3, choices=BaseUnit.choices)

    def __str__(self):
        return self.name


class StockLedger(models.Model):
    """
    Append-only ledger of stock movements.
    Stock balance is the sum of quantity for an item.
    """

    class MovementType(models.TextChoices):
        OPENING = "OPENING", "Opening"
        PURCHASE = "PURCHASE", "Purchase"
        PROD_ISSUE = "PROD_ISSUE", "Production Issue"
        PROD_OUTPUT = "PROD_OUTPUT", "Production Output"
        DISPATCH = "DISPATCH", "Dispatch"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="ledger",
    )
    movement_type = models.CharField(max_length=12, choices=MovementType.choices)
    quantity = models.IntegerField(help_text="Stock in (+), stock out (-), in base units")
    value_paise = models.IntegerField(
        default=0,
        help_text="Total value of this movement in paise, same sign as quantity",
    )
    source_type = models.CharField(max_length=30, blank=True)
    source_id = models.PositiveIntegerField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    def save(self, *args, **kwargs):
        # Append-only: prevent updating existing rows
        if self.pk is not None:
            raise RuntimeError("StockLedger rows cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Append-only: prevent deleting rows
        raise RuntimeError("StockLedger rows cannot be deleted.")

    def __str__(self):
        return f"{self.item.name} ({self.movement_type}: {self.quantity} {self.item.base_unit})"


class Supplier(models.Model):
    """A vendor or supplier providing raw materials or packaging."""

    name = models.CharField(max_length=100, unique=True)
    gstin = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return self.name


class Purchase(models.Model):
    """Purchase record for inward stock from a supplier."""

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    quantity = models.IntegerField(help_text="In base units")
    total_value_paise = models.IntegerField(help_text="Total value in paise")
    invoice_no = models.CharField(max_length=50, blank=True)
    purchase_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Purchase {self.id} - {self.item.name} ({self.quantity})"


class BOM(models.Model):
    """Bill of Materials specifying recipe components for a finished product."""

    product = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        unique=True,
    )
    expected_yield = models.PositiveIntegerField(help_text="Packets per batch")

    def __str__(self):
        return f"BOM: {self.product.name}"


class BOMLine(models.Model):
    """Component quantity required per batch for a BOM."""

    bom = models.ForeignKey(
        BOM,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
    )
    quantity = models.IntegerField(help_text="Base units per batch, must be > 0")

    class Meta:
        unique_together = ("bom", "item")

    def __str__(self):
        return f"{self.bom} - {self.item.name} ({self.quantity})"


class ProductionRun(models.Model):
    """Execution of a production batch against a BOM."""

    bom = models.ForeignKey(
        BOM,
        on_delete=models.PROTECT,
    )
    batches = models.PositiveIntegerField(default=1)
    expected_output = models.IntegerField()
    actual_output = models.IntegerField()
    tolerance_bp = models.IntegerField()
    yield_variance_bp = models.IntegerField()
    yield_flagged = models.BooleanField()
    material_cost_paise = models.IntegerField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ProductionRun #{self.id} for {self.bom.product.name}"


class ProductionRunLine(models.Model):
    """Materials issued and tracked during a production run."""

    run = models.ForeignKey(
        ProductionRun,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
    )
    standard_qty = models.IntegerField()
    actual_qty = models.IntegerField()
    wastage_qty = models.IntegerField(
        default=0,
        help_text="The damaged/scrap part of actual_qty",
    )
    variance_bp = models.IntegerField()
    flagged = models.BooleanField()
    cost_paise = models.IntegerField()

    def __str__(self):
        return f"{self.run} - {self.item.name}"


class Customer(models.Model):
    """Customer profile for retailers or distributors."""

    class Kind(models.TextChoices):
        RETAILER = "RETAILER", "Retailer"
        DISTRIBUTOR = "DISTRIBUTOR", "Distributor"

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=15, choices=Kind.choices)
    gstin = models.CharField(max_length=15, blank=True)
    state = models.CharField(max_length=40)
    state_code = models.CharField(max_length=2)
    phone = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return self.name


class ProductPrice(models.Model):
    """Catalog selling price and GST details for finished products."""

    product = models.OneToOneField(
        Item,
        on_delete=models.PROTECT,
    )
    price_paise = models.PositiveIntegerField(help_text="Price per piece before tax")
    gst_rate_bp = models.PositiveIntegerField(
        default=500,
        help_text="GST rate in basis points (500 = 5%)",
    )
    hsn_code = models.CharField(max_length=8)

    def __str__(self):
        return f"{self.product.name} ({self.price_paise} paise)"


class Order(models.Model):
    """Sales order placed by a customer through a salesman."""

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        INVOICED = "INVOICED", "Invoiced"
        DISPATCHED = "DISPATCHED", "Dispatched"

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    salesman = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.SUBMITTED,
    )
    reject_reason = models.CharField(max_length=200, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order #{self.id} - {self.customer.name} ({self.status})"


class OrderLine(models.Model):
    """Line item on a sales order specifying ordered product and quantity."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
    )
    quantity = models.PositiveIntegerField(help_text="Quantity in pieces")
    unit_price_paise = models.PositiveIntegerField(help_text="Unit price in paise")

    class Meta:
        unique_together = ("order", "product")

    def __str__(self):
        return f"{self.order} - {self.product.name} x {self.quantity}"


class Invoice(models.Model):
    """Tax invoice generated for an approved sales order."""

    class SupplyType(models.TextChoices):
        INTRA = "INTRA", "Intra-state"
        INTER = "INTER", "Inter-state"

    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        CANCELLED = "CANCELLED", "Cancelled"

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="invoice",
    )
    number = models.CharField(max_length=30, unique=True)
    fy = models.CharField(max_length=7)
    seq = models.PositiveIntegerField()
    invoice_date = models.DateField()
    customer_name = models.CharField(max_length=100)
    customer_gstin = models.CharField(max_length=15, blank=True)
    customer_state_code = models.CharField(max_length=2)
    supply_type = models.CharField(max_length=5, choices=SupplyType.choices)
    taxable_paise = models.IntegerField(default=0)
    cgst_paise = models.IntegerField(default=0)
    sgst_paise = models.IntegerField(default=0)
    igst_paise = models.IntegerField(default=0)
    total_paise = models.IntegerField(default=0)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ISSUED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("fy", "seq")

    def __str__(self):
        return f"Invoice {self.number}"


class InvoiceLine(models.Model):
    """Line item in a tax invoice."""

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
    )
    description = models.CharField(max_length=100)
    hsn_code = models.CharField(max_length=8)
    quantity = models.PositiveIntegerField()
    unit_price_paise = models.IntegerField()
    gst_rate_bp = models.IntegerField()
    taxable_paise = models.IntegerField()
    cgst_paise = models.IntegerField()
    sgst_paise = models.IntegerField()
    igst_paise = models.IntegerField()

    def __str__(self):
        return f"{self.invoice.number} - {self.description}"


class Payment(models.Model):
    """Payment received against an issued invoice."""

    class Mode(models.TextChoices):
        CASH = "CASH", "Cash"
        UPI = "UPI", "UPI"
        BANK = "BANK", "Bank"
        CHEQUE = "CHEQUE", "Cheque"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount_paise = models.PositiveIntegerField()
    payment_date = models.DateField()
    mode = models.CharField(max_length=10, choices=Mode.choices)
    reference = models.CharField(max_length=50, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment of {self.amount_paise} paise for {self.invoice.number}"



