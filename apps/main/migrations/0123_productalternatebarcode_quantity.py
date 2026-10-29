# Generated manually on 2026-09-17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0122_clientdebtbulkpayment'),
    ]

    operations = [
        migrations.AddField(
            model_name='productalternatebarcode',
            name='quantity',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Количество в упаковке'),
        ),
    ]
