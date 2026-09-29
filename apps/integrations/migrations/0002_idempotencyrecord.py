# BE2-11: ключи идемпотентности операций кассы.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0001_initial"),
        ("users", "0058_company_market_spheres"),
    ]

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("key", models.CharField(max_length=128)),
                ("scope", models.CharField(help_text="Метод и адрес запроса", max_length=255)),
                ("body_hash", models.CharField(max_length=64)),
                ("state", models.CharField(
                    choices=[("in_progress", "Выполняется"), ("done", "Готово")], default="in_progress", max_length=16
                )),
                ("status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("response_body", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("company", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="idempotency_records", to="users.company"
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("company", "key"), name="uniq_idempotency_company_key"),
                ],
            },
        ),
    ]
