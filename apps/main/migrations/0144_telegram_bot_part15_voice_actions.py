from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    # ТЗ ч.15 (голос и действия ИИ в боте). Ветка от 0143: на проде часть 14
    # (0144_fix_offset…/0145) ещё не выложена, слияние — в 0146_merge.

    dependencies = [
        ("main", "0143_marketsaleemployeepayprofile_per_item_amount_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotsettings",
            name="voice_language",
            field=models.CharField(
                choices=[("auto", "Как у владельца"), ("ru", "Русский"), ("ky", "Кыргызский")],
                default="auto",
                max_length=8,
                verbose_name="Язык голосового ответа",
            ),
        ),
        migrations.AddField(
            model_name="telegrambotsettings",
            name="ai_min_markup_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("20.00"),
                max_digits=6,
                verbose_name="Минимальная наценка при приходе по накладной, %",
            ),
        ),
        migrations.AddField(
            model_name="telegrambotsettings",
            name="ai_owner_actions_enabled",
            field=models.BooleanField(default=True, verbose_name="ИИ может предлагать изменения товаров"),
        ),
    ]
