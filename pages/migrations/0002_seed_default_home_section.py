"""
Seeds a default "Featured Pieces" home section (latest active products,
site-wide) so the homepage shows a product grid out of the box, until
staff configure sections of their own from admin.
"""

from django.db import migrations


def seed_default_section(apps, schema_editor):
    HomeSection = apps.get_model("pages", "HomeSection")
    if not HomeSection.objects.exists():
        HomeSection.objects.create(
            title="Featured Pieces",
            source="latest",
            limit=8,
            is_active=True,
            display_order=0,
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_section, noop),
    ]
