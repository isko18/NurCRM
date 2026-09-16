# Generated manually on 2026-09-17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0044_warehousecashconfirmationsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehouseproductalternatebarcode',
            name='quantity',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Количество в упаковке'),
        ),
    ]
