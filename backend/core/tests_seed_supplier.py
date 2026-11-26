from django.test import TestCase
from django.core.management import call_command
from core.models import Supplier


class SeedSupplierTestCase(TestCase):
    def test_seed_benchmark_creates_single_supplier(self):
        # Run seed_benchmark twice
        call_command("seed_benchmark", verbosity=0)
        call_command("seed_benchmark", verbosity=0)

        # Exactly one supplier named "Sri Murugan Traders" exists
        qs = Supplier.objects.filter(name="Sri Murugan Traders")
        self.assertEqual(qs.count(), 1)
        supplier = qs.first()
        self.assertEqual(supplier.name, "Sri Murugan Traders")
