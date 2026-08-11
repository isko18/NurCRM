"""
Остатки склада: согласованность StockBalance.qty и WarehouseProduct.quantity при проведении.

Проверяется, что после продажи остаток уменьшается ровно на проданное количество —
в том числе когда один товар встречается в документе несколькими строками.
"""
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.warehouse import models
from apps.warehouse import services

User = get_user_model()


class StockConsistencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="stock@example.com", password="testpass123", first_name="S", last_name="T"
        )
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        self.company = Company.objects.create(name="Stock Co", owner=self.user)
        self.branch = Branch.objects.create(company=self.company, name="Main")
        self.wh = models.Warehouse.objects.create(
            name="Склад", company=self.company, branch=self.branch,
            status=models.Warehouse.Status.active,
        )
        self.product = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh,
            name="Товар", code="S001", unit="шт", is_weight=False,
            purchase_price=Decimal("100.00"), price=Decimal("150.00"),
            quantity=Decimal("100.000"),
        )
        models.StockBalance.objects.create(
            warehouse=self.wh, product=self.product, qty=Decimal("100.000")
        )
        self.client_cp = models.Counterparty.objects.create(
            name="Клиент", phone="+996700000030", type=models.Counterparty.Type.CLIENT,
        )

    def _sale(self, *qtys):
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.client_cp,
            payment_kind=models.Document.PaymentKind.CREDIT,  # без кассового этапа
        )
        for qty in qtys:
            models.DocumentItem.objects.create(
                document=doc, product=self.product,
                qty=Decimal(qty), price=Decimal("150.00"),
            )
        services.post_document(doc)
        return doc

    def _on_hand(self):
        bal = models.StockBalance.objects.get(warehouse=self.wh, product=self.product)
        self.product.refresh_from_db()
        return Decimal(bal.qty), Decimal(self.product.quantity)

    def test_sale_decrements_balance_and_product_quantity(self):
        """Одна строка: 100 − 10 = 90 и в остатке, и в карточке товара."""
        self._sale("10.000")
        bal_qty, prod_qty = self._on_hand()
        self.assertEqual(bal_qty, Decimal("90.000"))
        self.assertEqual(prod_qty, Decimal("90.000"))

    def test_sale_with_same_product_in_two_lines_decrements_full_qty(self):
        """Один товар двумя строками: 100 − 10 − 5 = 85, а не 95."""
        self._sale("10.000", "5.000")
        bal_qty, prod_qty = self._on_hand()
        self.assertEqual(bal_qty, Decimal("85.000"))
        self.assertEqual(prod_qty, Decimal("85.000"))

    def test_sale_of_full_stock_in_two_lines_is_allowed_exactly_once(self):
        """Две строки ровно на весь остаток проходят и обнуляют склад."""
        self._sale("60.000", "40.000")
        bal_qty, prod_qty = self._on_hand()
        self.assertEqual(bal_qty, Decimal("0.000"))
        self.assertEqual(prod_qty, Decimal("0.000"))

    def test_sale_over_stock_in_two_lines_is_rejected(self):
        """Суммарно строки не должны продавать больше, чем есть на складе."""
        with self.assertRaises(ValueError):
            self._sale("60.000", "50.000")

    def test_double_post_of_stale_instance_does_not_decrement_twice(self):
        """
        Двойное проведение (двойной клик / ретрай) не должно списывать остаток дважды.

        Второй запрос работает со своим экземпляром документа, у которого в памяти
        ещё status=DRAFT, поэтому проверка «уже проведён» обязана читать статус из БД.
        """
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.client_cp,
            payment_kind=models.Document.PaymentKind.CREDIT,
        )
        models.DocumentItem.objects.create(
            document=doc, product=self.product, qty=Decimal("10.000"), price=Decimal("150.00"),
        )
        stale = models.Document.objects.get(pk=doc.pk)  # копия «второго запроса»

        services.post_document(doc)
        with self.assertRaises(ValueError):
            services.post_document(stale)

        bal_qty, prod_qty = self._on_hand()
        self.assertEqual(bal_qty, Decimal("90.000"))
        self.assertEqual(prod_qty, Decimal("90.000"))
        self.assertEqual(models.StockMove.objects.filter(document=doc).count(), 1)

    def test_recalc_totals_on_document_without_items(self):
        """Пересчёт пустого документа не должен падать."""
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.client_cp,
        )
        services.recalc_document_totals(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.total, Decimal("0.00"))
