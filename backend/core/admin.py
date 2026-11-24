from django.contrib import admin
from core.models import Item, StockLedger


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
