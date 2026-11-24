"""
Management command to create the required groups and test users.
Idempotent – repeated runs do not create duplicates.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

PASSWORD = "Test@1234"

# Mapping of username -> group name
USER_GROUPS = {
    "salesman_a": "salesman",
    "salesman_b": "salesman",
    "admin1": "admin",
    "production1": "production",
    "accountant1": "accountant",
    "owner1": "owner",
}

class Command(BaseCommand):
    help = "Create the 5 role groups and 6 test users (idempotent)."

    def handle(self, *args, **options):
        # Ensure groups exist
        for grp_name in {"salesman", "admin", "production", "accountant", "owner"}:
            Group.objects.get_or_create(name=grp_name)

        User = get_user_model()
        created = []
        for username, grp_name in USER_GROUPS.items():
            user, created_now = User.objects.get_or_create(username=username)
            # If the user is newly created, set a password and make active
            if created_now:
                user.set_password(PASSWORD)
                user.is_active = True
                user.save()
                created.append(username)
            # Ensure user is in the correct group
            group = Group.objects.get(name=grp_name)
            user.groups.add(group)

        # Output a simple table for the user
        self.stdout.write(f"{'Username':<15} {'Role':<12} {'Password'}")
        self.stdout.write('-' * 40)
        for username, grp_name in USER_GROUPS.items():
            self.stdout.write(f"{username:<15} {grp_name:<12} {PASSWORD}")
        if created:
            self.stdout.write(f"Created users: {', '.join(created)}")
        else:
            self.stdout.write("All users already existed.")
