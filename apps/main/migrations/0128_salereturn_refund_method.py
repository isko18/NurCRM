# BE2-01: способ, которым вернули деньги по возврату.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0127_kassa_discount_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="salereturn",
            name="refund_method",
            field=models.CharField(blank=True, default="", max_length=16, verbose_name="Способ возврата денег"),
        ),
    ]
