"""
core/permissions.py - Role-based permissions for Order-to-Audit system.
"""

from django.core.exceptions import PermissionDenied
from core.models import Order

# Role constants – Django Group names
ROLE_SALESMAN = "salesman"
ROLE_ADMIN = "admin"
ROLE_PRODUCTION = "production"
ROLE_ACCOUNTANT = "accountant"
ROLE_OWNER = "owner"

# Mapping of role -> set of allowed action names
ROLE_ACTIONS = {
    ROLE_SALESMAN: {
        "place_order",
        "view_own_orders",
        "view_customers",
        "view_prices",
    },
    ROLE_ADMIN: {
        "view_all_orders",
        "edit_order",
        "approve_order",
        "reject_order",
        "create_invoice",
        "record_dispatch",
        "record_payment",
        "cancel_invoice",
        "download_invoice_pdf",
        "view_stock",
        "view_customers",
        "view_prices",
    },
    ROLE_PRODUCTION: {
        "record_purchase",
        "record_production_run",
        "record_expense",
        "view_stock",
        "view_production",
        "view_costing",
    },
    ROLE_ACCOUNTANT: {
        "view_reports",
        "view_all_orders",
        "view_stock",
        "view_costing",
        "download_invoice_pdf",
    },
    ROLE_OWNER: {
        "view_dashboard",
        "view_reports",
        "view_all_orders",
        "view_stock",
        "view_production",
        "view_costing",
        "download_invoice_pdf",
    },
}

# All actions that change state (used by tests)
STATE_CHANGING_ACTIONS = {
    "place_order",
    "edit_order",
    "approve_order",
    "reject_order",
    "create_invoice",
    "record_dispatch",
    "record_payment",
    "cancel_invoice",
    "record_purchase",
    "record_production_run",
    "record_expense",
}

ALL_ACTIONS = set().union(*ROLE_ACTIONS.values())


def get_roles(user) -> list:
    """Return a sorted list of the user's role names (groups that are in ROLE_ACTIONS).
    Returns [] for None, anonymous or inactive users, or users with no role groups.
    """
    if user is None:
        return []
    if getattr(user, "is_anonymous", False):
        return []
    if not getattr(user, "is_active", False):
        return []
    names = user.groups.values_list("name", flat=True)
    return sorted({name for name in names if name in ROLE_ACTIONS})


def can(user, action: str) -> bool:
    """Return True if the user is permitted to perform *action*.
    Superusers (active) are allowed all actions. Inactive or anonymous users are denied.
    """
    if user is None:
        return False
    if getattr(user, "is_anonymous", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    roles = get_roles(user)
    for role in roles:
        if action in ROLE_ACTIONS.get(role, set()):
            return True
    return False


def require(user, action: str) -> None:
    """Raise PermissionDenied if *user* cannot perform *action*.
    The exception message includes the username and the action.
    """
    if not can(user, action):
        username = getattr(user, "username", "AnonymousUser") if user else "None"
        raise PermissionDenied(f"User '{username}' is not permitted to perform '{action}'.")


def orders_for_user(user):
    """Return a queryset of Orders viewable by *user*.
    Superusers and anyone with ``view_all_orders`` see every order.  Users with
    ``view_own_orders`` see only the orders where they are the salesman.
    Otherwise a PermissionDenied is raised.
    """
    if can(user, "view_all_orders"):
        return Order.objects.all()
    if can(user, "view_own_orders"):
        return Order.objects.filter(salesman=user)
    raise PermissionDenied(f"User '{getattr(user, 'username', user)}' cannot view orders.")


def can_view_report(user) -> bool:
    """Return True if the user can view the reporting suite (accountant or owner)."""
    return can(user, "view_reports")