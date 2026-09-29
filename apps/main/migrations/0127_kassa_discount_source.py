# BE2-06/07: ручная скидка кассира отдельно от итоговой, источник скидки строки.
from decimal import Decimal

from django.db import migrations, models

SOURCE_CHOICES = [("none", "Нет"), ("manual", "Ручная"), ("promotion", "Акция")]


def _fields(model_name):
    return [
        migrations.AddField(
            model_name=model_name,
            name="manual_discount",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=12, verbose_name="Ручная скидка кассира"
            ),
        ),
        migrations.AddField(
            model_name=model_name,
            name="discount_source",
            field=models.CharField(
                choices=SOURCE_CHOICES, default="none", max_length=16, verbose_name="Источник скидки"
            ),
        ),
        migrations.AddField(
            model_name=model_name,
            name="promotion_id",
            field=models.UUIDField(blank=True, null=True, verbose_name="Ступень акции"),
        ),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0126_rental_rentalitem_and_more"),
    ]

    operations = _fields("cartitem") + _fields("saleitem")
