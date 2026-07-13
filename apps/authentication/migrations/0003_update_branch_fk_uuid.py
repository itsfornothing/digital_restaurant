"""
Migration 0003 — update User.branch FK after Branch PK changes to UUID (Task 10.1).

The stub Branch model in branches/0002 used a BigAutoField PK.
branches/0003 drops that table and recreates Branch with a UUID PK.
This migration drops the old integer FK on User.branch and re-creates it
as a UUIDField FK to point to the new Branch model.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0002_add_branch_fk"),
        ("branches", "0003_full_branch_table"),
    ]

    operations = [
        # Remove the old FK that pointed to the BigAutoField stub Branch
        migrations.RemoveField(
            model_name="user",
            name="branch",
        ),
        # Re-add the FK now that Branch has a UUID PK
        migrations.AddField(
            model_name="user",
            name="branch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="staff",
                to="branches.branch",
            ),
        ),
    ]
