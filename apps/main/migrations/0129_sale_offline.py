# BE2-10: продажа, сделанная кассой без связи и дослана позже.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0128_salereturn_refund_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="is_offline",
            field=models.BooleanField(default=False, verbose_name="Продажа без связи"),
        ),
        migrations.AddField(
            model_name="sale",
            name="received_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Принята сервером"),
        ),
    ]
