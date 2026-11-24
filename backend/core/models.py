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
