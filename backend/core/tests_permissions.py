from django.test import TestCase
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied

from core.permissions import (
    ROLE_ACTIONS,
    STATE_CHANGING_ACTIONS,
    ALL_ACTIONS,
    get_roles,
    can,
    require,
    orders_for_user,
    can_view_report,
)
from core.models import Customer, Order

User = get_user_model()


class PermissionsTestCase(TestCase):
    def setUp(self):
        # Seed the default users and groups
        call_command("seed_users", verbosity=0)
        # Fetch users by username for convenience
        self.salesman_a = User.objects.get(username="salesman_a")
        self.admin1 = User.objects.get(username="admin1")
        self.production1 = User.objects.get(username="production1")
        self.accountant1 = User.objects.get(username="accountant1")
        self.owner1 = User.objects.get(username="owner1")

    def test_role_matrix(self):
        role_map = {
            self.salesman_a: "salesman",
            self.admin1: "admin",
            self.production1: "production",
            self.accountant1: "accountant",
            self.owner1: "owner",
        }
        for user, role in role_map.items():
            for action in ALL_ACTIONS:
                expected = action in ROLE_ACTIONS[role]
                self.assertEqual(
                    can(user, action), expected,
                    f"Role {role} permission mismatch for action {action}",
                )

    def test_read_only_roles_state_changing(self):
        # Accountant and owner should have no overlap with state‑changing actions
        for role in ["accountant", "owner"]:
            self.assertTrue(
                set(ROLE_ACTIONS[role]).isdisjoint(STATE_CHANGING_ACTIONS),
                f"Role {role} should not intersect state‑changing actions",
            )
        # Verify the expected count of state‑changing actions
        self.assertEqual(len(STATE_CHANGING_ACTIONS), 11)
        # Union of state‑changing actions for salesman, admin, production
        union_roles = set()
        for role in ["salesman", "admin", "production"]:
            union_roles.update(set(ROLE_ACTIONS[role]) & set(STATE_CHANGING_ACTIONS))
        self.assertEqual(set(STATE_CHANGING_ACTIONS), union_roles)

    def test_denied_users(self):
        # Anonymous (None) and completely anonymous user
        for user in [None, AnonymousUser()]:
            for action in ALL_ACTIONS:
                self.assertFalse(can(user, action))
        # Inactive salesman
        self.salesman_a.is_active = False
        self.salesman_a.save()
        for action in ALL_ACTIONS:
            self.assertFalse(can(self.salesman_a, action))
        # User without any group
        nogroup = User.objects.create_user("nogroup", password="x")
        for action in ALL_ACTIONS:
            self.assertFalse(can(nogroup, action))

    def test_active_superuser(self):
        root = User.objects.create_superuser("root", "r@x.com", "x")
        for action in ALL_ACTIONS:
            self.assertTrue(can(root, action))
        # Any other string should also be allowed for a superuser
        self.assertTrue(can(root, "anything_else"))

    def test_require_raises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            require(self.salesman_a, "approve_order")
        # Should not raise for an admin
        try:
            require(self.admin1, "approve_order")
        except PermissionDenied:
            self.fail("require raised PermissionDenied for admin when it should not")

    def test_orders_for_user(self):
        salesman_b = User.objects.get(username="salesman_b")
        cust = Customer.objects.create(
            name="Test Retailer", kind="RETAILER", state="Tamil Nadu", state_code="33"
        )
        order_a = Order.objects.create(customer=cust, salesman=self.salesman_a)
        Order.objects.create(customer=cust, salesman=salesman_b)
        # Salesman A sees only their own order
        own = orders_for_user(self.salesman_a)
        self.assertEqual(own.count(), 1)
        self.assertEqual(own.first().id, order_a.id)
        # Admin, accountant and owner see both orders
        self.assertEqual(orders_for_user(self.admin1).count(), 2)
        self.assertEqual(orders_for_user(self.accountant1).count(), 2)
        self.assertEqual(orders_for_user(self.owner1).count(), 2)
        # Production has no order access
        with self.assertRaises(PermissionDenied):
            orders_for_user(self.production1)

    def test_seed_users_twice(self):
        # setUp already ran seed_users once; run it again
        call_command("seed_users", verbosity=0)
        roles = ["salesman", "admin", "production", "accountant", "owner"]
        self.assertEqual(Group.objects.filter(name__in=roles).count(), 5)
        usernames = ["salesman_a", "salesman_b", "admin1",
                     "production1", "accountant1", "owner1"]
        self.assertEqual(User.objects.filter(username__in=usernames).count(), 6)
        self.assertTrue(self.client.login(username="salesman_a", password="Test@1234"))

    def test_get_roles(self):
        self.assertEqual(get_roles(self.salesman_a), ["salesman"])
        self.assertEqual(get_roles(None), [])
        # Inactive user
        self.salesman_a.is_active = False
        self.salesman_a.save()
        self.assertEqual(get_roles(self.salesman_a), [])
        # User without group
        nogroup = User.objects.create_user("nogroup2", password="x")
        self.assertEqual(get_roles(nogroup), [])

    def test_can_view_report(self):
        self.assertTrue(can_view_report(self.accountant1))
        self.assertTrue(can_view_report(self.owner1))
        self.assertFalse(can_view_report(self.salesman_a))
        self.assertFalse(can_view_report(self.admin1))
        self.assertFalse(can_view_report(self.production1))
