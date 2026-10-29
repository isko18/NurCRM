# BE2-02: несколько видов магазина у компании.
from django.db import migrations, models


def fill_spheres(apps, schema_editor):
    Company = apps.get_model("users", "Company")
    for c in Company.objects.exclude(market_sphere__isnull=True).exclude(market_sphere=""):
        Company.objects.filter(pk=c.pk).update(market_spheres=[c.market_sphere])


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0057_company_market_sphere_alter_user_role_companyaddon"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="market_spheres",
            field=models.JSONField(blank=True, default=list, verbose_name="Виды магазина"),
        ),
        migrations.RunPython(fill_spheres, migrations.RunPython.noop),
    ]
