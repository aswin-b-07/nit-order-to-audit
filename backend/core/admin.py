from django.contrib import admin
from core.models import (
    Item,
    StockLedger,
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


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Admin configuration for Item (editable)."""

    list_display = ("name", "kind", "base_unit")
    list_filter = ("kind", "base_unit")
    search_fields = ("name",)


@admin.register(StockLedger)
class StockLedgerAdmin(admin.ModelAdmin):
    """Admin configuration for StockLedger (read-only: no add, change or delete)."""

    list_display = (
        "id",
        "item",
        "movement_type",
        "quantity",
        "value_paise",
        "source_type",
        "source_id",
        "created_at",
    )
    list_filter = ("movement_type", "source_type")
    search_fields = ("item__name", "note")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    """Admin configuration for Supplier (editable)."""

    list_display = ("name", "gstin")
    search_fields = ("name", "gstin")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    """Admin configuration for Purchase (read-only: no add, change or delete)."""

    list_display = (
        "id",
        "purchase_date",
        "supplier",
        "item",
        "quantity",
        "total_value_paise",
        "invoice_no",
        "created_at",
    )
    list_filter = ("purchase_date", "supplier")
    search_fields = ("invoice_no", "supplier__name", "item__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    """Admin configuration for BOM with BOMLine inline (editable)."""

    list_display = ("product", "expected_yield")
    inlines = [BOMLineInline]


class ProductionRunLineInline(admin.TabularInline):
    model = ProductionRunLine
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):
    """Admin configuration for ProductionRun (read-only with read-only lines)."""

    list_display = (
        "id",
        "bom",
        "batches",
        "expected_output",
        "actual_output",
        "yield_variance_bp",
        "yield_flagged",
        "material_cost_paise",
        "created_at",
    )
    list_filter = ("yield_flagged", "bom")
    inlines = [ProductionRunLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """Admin configuration for Customer (editable)."""

    list_display = ("name", "kind", "gstin", "state", "phone")
    list_filter = ("kind", "state")
    search_fields = ("name", "gstin", "phone")


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    """Admin configuration for ProductPrice (editable)."""

    list_display = ("product", "price_paise", "gst_rate_bp", "hsn_code")
    search_fields = ("product__name", "hsn_code")


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Admin configuration for Order (read-only with read-only lines)."""

    list_display = (
        "id",
        "customer",
        "salesman",
        "status",
        "created_at",
        "decided_by",
        "decided_at",
    )
    list_filter = ("status",)
    search_fields = ("customer__name", "salesman__username")
    inlines = [OrderLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
