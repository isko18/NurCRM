# 11-shift-returns-reporting: сколько денег по возврату отдали наличными и безналом.
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations, models

Z = Decimal("0.00")


def _q(v):
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def backfill(apps, schema_editor):
    """
    Старые возвраты: сумма выданных денег — по расходным движениям pos_sale_return
    этого чека в смене возврата (±2 мин), способ — refund_method или исходная оплата.
    """
    SaleReturn = apps.get_model("main", "SaleReturn")
    SalePayment = apps.get_model("main", "SalePayment")
    CashFlow = apps.get_model("construction", "CashFlow")

    for ret in SaleReturn.objects.select_related("sale").iterator():
        sale = ret.sale
        flows = CashFlow.objects.filter(
            source_kind="pos_sale_return", source_id=str(sale.id), type="expense",
            created_at__gte=ret.created_at - timedelta(minutes=2),
            created_at__lte=ret.created_at + timedelta(minutes=2),
        )
        if ret.shift_id:
            flows = flows.filter(shift_id=ret.shift_id)
        amount = _q(sum((f.amount or Z for f in flows), Z))
        if amount <= 0:
            continue

        rm = (ret.refund_method or "").strip().lower()
        if rm and rm not in ("original", "same"):
            cash = amount if rm == "cash" else Z
        else:
            lines = [
                (p.method, p.amount or Z)
                for p in SalePayment.objects.filter(sale_id=sale.id, amount__gt=0).exclude(method__in=["debt", "offset"])
            ]
            paid = sum((a for _, a in lines), Z)
            if paid > 0:
                cash = _q(amount * sum((a for m, a in lines if m == "cash"), Z) / paid)
            else:
                cash = amount if sale.payment_method in ("cash", "debt", None, "") else Z
        SaleReturn.objects.filter(pk=ret.pk).update(refund_cash=cash, refund_noncash=amount - cash)


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0130_product_deletion"),
        ("construction", "0031_alter_cashflow_source_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="salereturn",
            name="refund_cash",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12, verbose_name="Возвращено наличными"),
        ),
        migrations.AddField(
            model_name="salereturn",
            name="refund_noncash",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12, verbose_name="Возвращено безналом"),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
