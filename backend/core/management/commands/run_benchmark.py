from datetime import date
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import (
    BOM,
    Customer,
    Expense,
    Invoice,
    Item,
    Payment,
    ProductionRun,
    Purchase,
    Supplier,
)
from core.services import (
    approve_order,
    create_invoice,
    get_balance,
    invoice_outstanding,
    place_order,
    record_dispatch,
    record_expense,
    record_payment,
    record_production_run,
    record_purchase,
    run_costing,
)


class Command(BaseCommand):
    help = "Run benchmark steps 1-8 through existing service functions only."

    def handle(self, *args, **options):
        # Safety check: if benchmark was already run, exit without modifying DB
        if (
            Purchase.objects.exists()
            or ProductionRun.objects.exists()
            or Invoice.objects.exists()
        ):
            self.stdout.write(
                "Benchmark already run. To rerun: delete db.sqlite3, run migrate, then seed_benchmark, then run_benchmark."
            )
            return

        User = get_user_model()
        admin, _ = User.objects.get_or_create(
            username="admin", defaults={"is_staff": True}
        )
        salesman, _ = User.objects.get_or_create(username="salesman")
        supplier, _ = Supplier.objects.get_or_create(
            name="Benchmark Supplier", defaults={"gstin": "33AABBCCDDEE1Z5"}
        )

        flour = Item.objects.get(name="Flour")
        oil = Item.objects.get(name="Cooking oil")
        spice = Item.objects.get(name="Salt and spice mix")
        pouch = Item.objects.get(name="200 g pouch")
        carton = Item.objects.get(name="Carton")
        murukku = Item.objects.get(name="Murukku 200 g")
        bom = BOM.objects.get(product=murukku)
        retailer_a = Customer.objects.get(name="Retailer A")
        distributor_b = Customer.objects.get(name="Distributor B")

        # Step 1: Purchase 60 kg flour at Rs 45/kg
        record_purchase(
            flour,
            "60",
            "kg",
            "45",
            supplier,
            purchase_date=date.today(),
            invoice_no="INV-BM-01",
            user=admin,
        )
        self.stdout.write("1. Purchase 60 kg flour at Rs 45/kg.")

        # Step 2 & 3: Production run of Murukku 200 g
        run = record_production_run(
            bom,
            188,
            {
                flour: {"qty": 40000},
                oil: {"qty": 10500},
                spice: {"qty": 1200},
                pouch: {"qty": 194, "wastage": 6},
                carton: {"qty": 9},
            },
            user=admin,
        )
        self.stdout.write(
            "2. Production run of Murukku 200 g: issue flour 40 kg, oil 10.5 L, spice 1.2 kg."
        )
        self.stdout.write(
            "3. Actual output: 188 good, 6 pouches damaged, 9 cartons used."
        )

        # Step 4: Order for Retailer A (Tamil Nadu): 150 packets
        order_a = place_order(retailer_a, salesman, [(murukku, 150)])
        self.stdout.write(
            "4. Order for Retailer A (Tamil Nadu): 150 packets (place_order)."
        )

        # Step 5: Approve, create_invoice, record_dispatch
        approve_order(order_a, user=admin)
        inv_a = create_invoice(order_a, admin, invoice_date=date.today())
        record_dispatch(inv_a, user=admin)
        self.stdout.write("5. Approve, create_invoice, record_dispatch.")

        # Step 6: Order for Distributor B (Karnataka): 120 packets (blocked for shortage)
        order_b = place_order(distributor_b, salesman, [(murukku, 120)])
        self.stdout.write("6. Order for Distributor B (Karnataka): 120 packets.")
        try:
            approve_order(order_b, user=admin)
        except ValueError:
            available = get_balance(murukku)
            shortage = 120 - available
            self.stdout.write(
                f"Distributor B: shortage of {shortage} packets ({available} available)"
            )

        # Step 7: Payment of Rs 4,000 from Retailer A
        record_payment(inv_a, 400000, date.today(), Payment.Mode.UPI, user=admin)
        self.stdout.write("7. Payment of Rs 4,000 from Retailer A.")

        # Step 8: Expenses: labour Rs 1,200, gas/power Rs 600 on the run
        record_expense(
            run, Expense.Kind.LABOUR, 120000, note="Shift labor", user=admin
        )
        record_expense(
            run, Expense.Kind.GAS_POWER, 60000, note="Gas power", user=admin
        )
        self.stdout.write(
            "8. Expenses: labour Rs 1,200, gas/power Rs 600 on the run."
        )

        # Print balances and metrics
        costing = run_costing(run)
        bal_flour = get_balance(flour)
        bal_oil = get_balance(oil)
        bal_pouch = get_balance(pouch)
        bal_carton = get_balance(carton)
        bal_murukku = get_balance(murukku)
        inv_total = inv_a.total_paise
        inv_outstanding = invoice_outstanding(inv_a)
        cost_per_pkt = costing["cost_per_packet_paise"]
        margin_bp_val = costing["margin_bp"]

        self.stdout.write(f"flour {bal_flour} g")
        self.stdout.write(f"oil {bal_oil} ml")
        self.stdout.write(f"pouches {bal_pouch}")
        self.stdout.write(f"cartons {bal_carton}")
        self.stdout.write(f"finished goods {bal_murukku}")
        self.stdout.write(f"invoice total {inv_total}")
        self.stdout.write(f"outstanding {inv_outstanding}")
        self.stdout.write(f"cost per packet {cost_per_pkt}")
        self.stdout.write(f"margin bp {margin_bp_val}")
