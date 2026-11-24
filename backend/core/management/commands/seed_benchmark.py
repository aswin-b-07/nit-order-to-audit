from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Item, StockLedger


class Command(BaseCommand):
    help = "Seed initial benchmark items and opening stock ledger entries."

    def handle(self, *args, **options):
        items_data = [
            # RAW
            {
                "name": "Flour",
                "kind": Item.Kind.RAW,
                "base_unit": Item.BaseUnit.G,
                "quantity": 100000,
                "value_paise": 420000,  # 100 kg at Rs 42/kg
            },
            {
                "name": "Cooking oil",
                "kind": Item.Kind.RAW,
                "base_unit": Item.BaseUnit.ML,
                "quantity": 30000,
                "value_paise": 420000,  # 30 L at Rs 140/L
            },
            {
                "name": "Salt and spice mix",
                "kind": Item.Kind.RAW,
                "base_unit": Item.BaseUnit.G,
                "quantity": 5000,
                "value_paise": 100000,  # 5 kg at Rs 200/kg
            },
            # PACKAGING
            {
                "name": "200 g pouch",
                "kind": Item.Kind.PACKAGING,
                "base_unit": Item.BaseUnit.PCS,
                "quantity": 500,
                "value_paise": 125000,  # Rs 2.50 each
            },
            {
                "name": "Carton",
                "kind": Item.Kind.PACKAGING,
                "base_unit": Item.BaseUnit.PCS,
                "quantity": 30,
                "value_paise": 45000,  # Rs 15 each
            },
            # FINISHED
            {
                "name": "Murukku 200 g",
                "kind": Item.Kind.FINISHED,
                "base_unit": Item.BaseUnit.PCS,
                "quantity": 50,
                "value_paise": 0,  # opening cost not given; add this as a comment
            },
        ]

        with transaction.atomic():
            for row in items_data:
                item, _ = Item.objects.get_or_create(
                    name=row["name"],
                    defaults={
                        "kind": row["kind"],
                        "base_unit": row["base_unit"],
                    },
                )

                # Ensure opening ledger row is not duplicated if run twice
                if not StockLedger.objects.filter(
                    item=item,
                    movement_type=StockLedger.MovementType.OPENING,
                    source_type="Opening",
                ).exists():
                    StockLedger.objects.create(
                        item=item,
                        movement_type=StockLedger.MovementType.OPENING,
                        quantity=row["quantity"],
                        value_paise=row["value_paise"],
                        source_type="Opening",
                        note="Opening stock benchmark",
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created opening stock for '{item.name}': {row['quantity']} {item.base_unit}"
                        )
                    )
                else:
                    self.stdout.write(
                        f"Opening stock for '{item.name}' already exists. Skipping."
                    )
