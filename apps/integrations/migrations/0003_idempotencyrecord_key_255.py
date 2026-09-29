# 01-idempotent-checkout: клиент присылает ключ до 255 символов.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0002_idempotencyrecord"),
    ]

    operations = [
        migrations.AlterField(
            model_name="idempotencyrecord",
            name="key",
            field=models.CharField(max_length=255),
        ),
    ]
