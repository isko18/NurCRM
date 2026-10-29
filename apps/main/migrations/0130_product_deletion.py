# BE2-19: журнал удалённых товаров для быстрой синхронизации каталога кассы.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0129_sale_offline"),
        ("users", "0058_company_market_spheres"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductDeletion",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("product_id", models.UUIDField()),
                ("deleted_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("company", models.ForeignKey(
                    db_constraint=False, on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name="product_deletions", to="users.company",
                )),
            ],
            options={
                "indexes": [models.Index(fields=["company", "deleted_at"], name="main_proddel_company_idx")],
            },
        ),
    ]
