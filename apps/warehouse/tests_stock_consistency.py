"""
Остатки склада: согласованность StockBalance.qty и WarehouseProduct.quantity при проведении.

Проверяется, что после продажи остаток уменьшается ровно на проданное количество —
в том числе когда один товар встречается в документе несколькими строками.
"""
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

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

    def test_edit_with_stale_instance_cannot_revert_posted_status(self):
        """
        Реальный инцидент SALE-20260808-0041: PUT ушёл одновременно с проведением.

        Экземпляр документа в запросе на редактирование прочитан до того, как
        проведение закоммитилось, поэтому в памяти статус ещё DRAFT. Полное
        сохранение возвращало документу DRAFT, а созданные движения оставались:
        товар списан, а документ выглядит черновиком.
        """
        from apps.warehouse import serializers_documents

        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE,
            warehouse_from=self.wh,
            counterparty=self.client_cp,
            payment_kind=models.Document.PaymentKind.CREDIT,
        )
        models.DocumentItem.objects.create(
            document=doc, product=self.product, qty=Decimal("10.000"), price=Decimal("150.00"),
        )
        # копия «параллельного запроса на редактирование», прочитанная до проведения
        stale = models.Document.objects.get(pk=doc.pk)
        self.assertEqual(stale.status, models.Document.Status.DRAFT)

        services.post_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)

        ser = serializers_documents.DocumentSerializer()
        with self.assertRaises(DRFValidationError) as ctx:
            ser.update(stale, {"comment": "правка вдогонку", "doc_type": doc.doc_type})
        self.assertIn("проведенный", str(ctx.exception))

        doc.refresh_from_db()
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        self.assertEqual(doc.moves.count(), 1)
        bal_qty, prod_qty = self._on_hand()
        self.assertEqual(bal_qty, Decimal("90.000"))
        self.assertEqual(prod_qty, Decimal("90.000"))

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

    def test_on_hand_ignores_stale_card_quantity_when_balance_exists(self):
        """Регистр нулевой, карточка мусорная: остаток для проверки — только регистр (без max)."""
        from apps.warehouse import stock as stock_service

        models.WarehouseProduct.objects.filter(pk=self.product.pk).update(quantity=Decimal("50.000"))
        models.StockBalance.objects.filter(warehouse=self.wh, product=self.product).update(qty=Decimal("0.000"))

        self.assertEqual(
            stock_service.get_on_hand(warehouse=self.wh, product=self.product), Decimal("0.000")
        )
        with self.assertRaises(ValueError):
            self._sale("1.000")

    def test_product_serializer_changed_quantity_posts_inventory(self):
        """Изменённое в форме количество проводится документом INVENTORY, а не пишется в карточку."""
        from apps.warehouse.serializers import WarehouseProductSerializer

        ser = WarehouseProductSerializer(
            instance=self.product,
            data={"quantity": "99.000", "price": "170.00"},
            partial=True,
            context={"warehouse": self.wh},
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal("99.000"))
        self.assertEqual(self.product.price, Decimal("170.00"))
        bal = models.StockBalance.objects.get(warehouse=self.wh, product=self.product)
        self.assertEqual(bal.qty, Decimal("99.000"))
        doc = models.Document.objects.get(doc_type=models.Document.DocType.INVENTORY)
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        self.assertEqual(doc.moves.get().qty_delta, Decimal("-1.000"))


# ---------------------------------------------------------------------------
# Единый источник остатка (stock-single-source-of-truth.md, §6)
# ---------------------------------------------------------------------------

import csv as _csv  # noqa: E402
import io as _io  # noqa: E402
import os as _os  # noqa: E402
import tempfile as _tempfile  # noqa: E402

from django.core.management import call_command  # noqa: E402
from django.db import transaction  # noqa: E402
from django.db.models import Sum  # noqa: E402
from django.urls import reverse  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from apps.warehouse import stock as stock_service  # noqa: E402


class SingleSourceOfTruthTests(TestCase):
    """Сценарии §6: StockBalance — единственный источник, карточка — копия, всё через StockMove."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="sst-owner@example.com", password="testpass123", first_name="O", last_name="W"
        )
        Company = apps.get_model("users", "Company")
        Branch = apps.get_model("users", "Branch")
        self.company = Company.objects.create(name="SST Co", owner=self.owner)
        self.branch = Branch.objects.create(company=self.company, name="Main")
        self.wh = models.Warehouse.objects.create(
            name="Склад 1", company=self.company, branch=self.branch,
            status=models.Warehouse.Status.active,
        )
        self.wh2 = models.Warehouse.objects.create(
            name="Склад 2", company=self.company, branch=self.branch,
            status=models.Warehouse.Status.active,
        )
        self.product = self._product("Товар", code="SST1", qty="100")
        self.client_cp = models.Counterparty.objects.create(
            name="Клиент", phone="+996700000077", type=models.Counterparty.Type.CLIENT,
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.owner)

    # ---------------------------------------------------------------- helpers
    def _product(self, name, *, code=None, qty="0", warehouse=None, barcode=None):
        """Товар с начальным остатком через opening (инвариант с самого начала)."""
        p = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=warehouse or self.wh,
            name=name, code=code, barcode=barcode, unit="шт", is_weight=False,
            purchase_price=Decimal("100.00"), price=Decimal("150.00"),
            quantity=Decimal(qty),
        )
        stock_service.init_opening_for_pair(warehouse=p.warehouse, product=p)
        return p

    def _doc(self, doc_type, *lines, warehouse=None, **extra):
        doc = models.Document.objects.create(
            doc_type=doc_type,
            warehouse_from=warehouse or self.wh,
            counterparty=extra.pop("counterparty", self.client_cp),
            payment_kind=extra.pop("payment_kind", models.Document.PaymentKind.CREDIT),
            **extra,
        )
        for product, qty in lines:
            models.DocumentItem.objects.create(
                document=doc, product=product, qty=Decimal(qty), price=Decimal("150.00"),
            )
        return doc

    def _sale(self, qty, product=None, **kw):
        doc = self._doc(models.Document.DocType.SALE, (product or self.product, qty))
        services.post_document(doc, allow_duplicate=True, **kw)
        return doc

    def _bal(self, product=None, warehouse=None):
        product = product or self.product
        return models.StockBalance.objects.get(warehouse=warehouse or product.warehouse, product=product).qty

    def _card(self, product=None):
        product = product or self.product
        return models.WarehouseProduct.objects.get(pk=product.pk).quantity

    def assertInvariant(self):
        """§6.13: для каждого регистра StockBalance == Σ StockMove, карточка == регистр своего склада."""
        for bal in models.StockBalance.objects.select_related("product"):
            moves = models.StockMove.objects.filter(
                warehouse_id=bal.warehouse_id, product_id=bal.product_id
            ).aggregate(s=Sum("qty_delta"))["s"] or Decimal("0")
            self.assertEqual(bal.qty, moves, f"регистр ≠ Σ движений для {bal.product.name}")
            if bal.product.warehouse_id == bal.warehouse_id:
                self.assertEqual(self._card(bal.product), bal.qty, f"карточка ≠ регистр для {bal.product.name}")

    def tearDown(self):
        # инвариант должен выполняться после любого сценария
        self.assertInvariant()

    # -------------------------------------------------------------- scenarios
    def test_01_sale_decrements_card_and_balance_with_one_move(self):
        doc = self._sale("10")
        self.assertEqual(self._bal(), Decimal("90.000"))
        self.assertEqual(self._card(), Decimal("90.000"))
        moves = list(models.StockMove.objects.filter(document=doc))
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].qty_delta, Decimal("-10.000"))
        self.assertEqual(moves[0].source_kind, models.StockMove.SourceKind.DOCUMENT)

    def test_02_patch_different_quantity_posts_inventory(self):
        """Форма редактирования: новое количество → проведённая инвентаризация с движением."""
        self._sale("10")
        url = reverse("warehouse-product-detail", kwargs={"product_uuid": self.product.pk})
        resp = self.api.patch(url, {"quantity": 50, "price": "130"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal("130.00"))
        self.assertEqual(self._card(), Decimal("50.000"))
        self.assertEqual(self._bal(), Decimal("50.000"))
        inv = models.Document.objects.get(doc_type=models.Document.DocType.INVENTORY)
        self.assertEqual(inv.moves.get().qty_delta, Decimal("-40.000"))

    def test_02b_crud_endpoint_also_posts_inventory(self):
        url = reverse("warehouse-product-detail-crud", kwargs={"pk": self.product.pk})
        resp = self.api.patch(url, {"quantity": "7"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._card(), Decimal("7.000"))
        self.assertEqual(self._bal(), Decimal("7.000"))

    def test_03_patch_same_quantity_is_ignored_price_updated(self):
        self._sale("10")
        url = reverse("warehouse-product-detail", kwargs={"product_uuid": self.product.pk})
        resp = self.api.patch(url, {"price": "120", "quantity": "90.000"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        p = models.WarehouseProduct.objects.get(pk=self.product.pk)
        self.assertEqual(p.price, Decimal("120.00"))
        self.assertEqual(p.quantity, Decimal("90.000"))
        self.assertEqual(self._bal(), Decimal("90.000"))

    def test_03b_update_does_not_overwrite_quantity_with_stale_instance(self):
        """update_fields: устаревший экземпляр (прочитан до продажи) не затирает списание."""
        from apps.warehouse.serializers import WarehouseProductSerializer

        stale = models.WarehouseProduct.objects.get(pk=self.product.pk)  # quantity=100 в памяти
        self._sale("10")
        ser = WarehouseProductSerializer(
            instance=stale, data={"name": "Товар новый"}, partial=True, context={"warehouse": self.wh},
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        p = models.WarehouseProduct.objects.get(pk=self.product.pk)
        self.assertEqual(p.name, "Товар новый")
        self.assertEqual(p.quantity, Decimal("90.000"))

    def test_04_stock_adjustment_then_sale(self):
        self._sale("10")
        url = reverse("warehouse-product-stock-adjustment", kwargs={"product_uuid": self.product.pk})
        resp = self.api.post(url, {"fact_qty": "50", "comment": "Пересчёт на полке"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["qty_before"], "90.000")
        self.assertEqual(resp.data["qty_after"], "50.000")
        self.assertEqual(resp.data["delta"], "-40.000")
        doc = models.Document.objects.get(pk=resp.data["document_id"])
        self.assertEqual(doc.doc_type, models.Document.DocType.INVENTORY)
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        self.assertEqual(doc.number, resp.data["document_number"])
        self.assertEqual(doc.comment, "Пересчёт на полке")
        # без денежного эффекта
        self.assertFalse(models.MoneyDocument.objects.filter(source_document=doc).exists())
        self.assertFalse(models.CashApprovalRequest.objects.filter(document=doc).exists())

        self._sale("1")
        self.assertEqual(self._card(), Decimal("49.000"))
        self.assertEqual(self._bal(), Decimal("49.000"))

    def test_04b_stock_adjustment_validation_and_access(self):
        url = reverse("warehouse-product-stock-adjustment", kwargs={"product_uuid": self.product.pk})
        for bad in ("-1", "abc", "", None, "1.5"):
            resp = self.api.post(url, {"fact_qty": bad} if bad is not None else {}, format="json")
            self.assertEqual(resp.status_code, 400, (bad, resp.data))
        # факт 0 допустим
        resp = self.api.post(url, {"fact_qty": "0"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self._card(), Decimal("0.000"))

        # чужая компания → 404
        other = User.objects.create_user(email="sst-other@example.com", password="x")
        Company = apps.get_model("users", "Company")
        Company.objects.create(name="Other Co", owner=other)
        c2 = APIClient()
        c2.force_authenticate(user=other)
        resp = c2.post(url, {"fact_qty": "5"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.data)

        # внешний агент компании (без company) → 403
        agent = User.objects.create_user(email="sst-agent403@example.com", password="x")
        models.CompanyWarehouseAgent.objects.create(
            company=self.company, user=agent, status=models.CompanyWarehouseAgent.Status.ACTIVE,
        )
        c3 = APIClient()
        c3.force_authenticate(user=agent)
        resp = c3.post(url, {"fact_qty": "5"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(self._card(), Decimal("0.000"))

    def test_05_inventory_sets_exact_fact(self):
        p = self._product("Инв", code="INV1", qty="5")
        # карточка «мусорная» (10) — не должна влиять
        models.WarehouseProduct.objects.filter(pk=p.pk).update(quantity=Decimal("10.000"))
        doc = self._doc(models.Document.DocType.INVENTORY, (p, "8"))
        services.post_document(doc)
        self.assertEqual(self._bal(p), Decimal("8.000"))
        self.assertEqual(self._card(p), Decimal("8.000"))
        mv = models.StockMove.objects.get(document=doc)
        self.assertEqual(mv.qty_delta, Decimal("3.000"))

    def test_06_sale_over_balance_rejected_nothing_written(self):
        p = self._product("Мало", code="LOW1", qty="5")
        moves_before = models.StockMove.objects.count()
        with self.assertRaises(ValueError) as ctx:
            self._sale("10", product=p, allow_negative=False)
        self.assertIn("Недостаточно", str(ctx.exception))
        self.assertEqual(self._bal(p), Decimal("5.000"))
        self.assertEqual(self._card(p), Decimal("5.000"))
        self.assertEqual(models.StockMove.objects.count(), moves_before)

    def test_06b_sale_via_api_returns_400_insufficient(self):
        p = self._product("Мало API", code="LOW2", qty="5")
        doc = self._doc(models.Document.DocType.SALE, (p, "10"))
        resp = self.api.post(reverse("warehouse-document-post", kwargs={"pk": doc.pk}), {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Недостаточно", resp.data["detail"])
        self.assertEqual(self._bal(p), Decimal("5.000"))

    def _agent(self, email="sst-agent@example.com"):
        agent = User.objects.create_user(email=email, password="testpass123")
        models.CompanyWarehouseAgent.objects.create(
            company=self.company, user=agent, status=models.CompanyWarehouseAgent.Status.ACTIVE,
            assigned_warehouse=self.wh,
        )
        return agent

    def _issue_to_agent(self, agent, product, qty):
        cart = models.AgentRequestCart.objects.create(
            company=self.company, branch=self.branch, agent=agent, warehouse=self.wh,
        )
        models.AgentRequestItem.objects.create(cart=cart, product=product, quantity_requested=Decimal(qty))
        cart.dispatch_by_owner(self.owner)
        return cart

    def test_07_issue_to_agent_creates_move(self):
        p = self._product("Агенту", code="AG1", qty="10")
        agent = self._agent()
        cart = self._issue_to_agent(agent, p, "3")
        self.assertEqual(self._bal(p), Decimal("7.000"))
        self.assertEqual(self._card(p), Decimal("7.000"))
        mv = models.StockMove.objects.get(product=p, source_kind=models.StockMove.SourceKind.AGENT_ISSUE)
        self.assertEqual(mv.qty_delta, Decimal("-3.000"))
        self.assertEqual(mv.source_id, cart.pk)
        self.assertIsNone(mv.document_id)
        self.assertEqual(
            models.AgentStockBalance.objects.get(agent=agent, warehouse=self.wh, product=p).qty,
            Decimal("3.000"),
        )

    def test_08_return_from_agent_creates_move(self):
        p = self._product("Возврат", code="AG2", qty="10")
        agent = self._agent()
        self._issue_to_agent(agent, p, "3")
        ret = models.AgentReturnCart.objects.create(
            company=self.company, branch=self.branch, agent=agent, warehouse=self.wh,
        )
        models.AgentReturnItem.objects.create(cart=ret, product=p, quantity_returned=Decimal("2"))
        ret.receive_by_owner(self.owner)
        self.assertEqual(self._bal(p), Decimal("9.000"))
        self.assertEqual(self._card(p), Decimal("9.000"))
        mv = models.StockMove.objects.get(product=p, source_kind=models.StockMove.SourceKind.AGENT_RETURN)
        self.assertEqual(mv.qty_delta, Decimal("2.000"))
        self.assertEqual(mv.source_id, ret.pk)
        self.assertEqual(
            models.AgentStockBalance.objects.get(agent=agent, warehouse=self.wh, product=p).qty,
            Decimal("1.000"),
        )

    def test_09_agent_personal_document_post_unpost_keeps_card(self):
        p = self._product("Личный", code="AG3", qty="10")
        agent = self._agent()
        self._issue_to_agent(agent, p, "3")
        card_before = self._card(p)
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.SALE, warehouse_from=self.wh, agent=agent,
            payment_kind=models.Document.PaymentKind.CREDIT,
        )
        models.DocumentItem.objects.create(document=doc, product=p, qty=Decimal("1"), price=Decimal("150"))
        services.post_document(doc)
        self.assertEqual(self._card(p), card_before)
        services.unpost_document(doc)
        self.assertEqual(self._card(p), card_before)
        self.assertEqual(self._bal(p), card_before)
        self.assertEqual(
            models.AgentStockBalance.objects.get(agent=agent, warehouse=self.wh, product=p).qty,
            Decimal("3.000"),
        )

    def test_10_create_product_with_quantity_posts_initial_inventory(self):
        url = reverse("warehouse-products", kwargs={"warehouse_uuid": self.wh.pk})
        resp = self.api.post(url, {"name": "Новый", "quantity": "20", "price": "10", "warehouse": str(self.wh.pk)}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["quantity"], "20.000")
        p = models.WarehouseProduct.objects.get(pk=resp.data["id"])
        self.assertEqual(self._bal(p), Decimal("20.000"))
        doc = models.Document.objects.get(items__product=p)
        self.assertEqual(doc.doc_type, models.Document.DocType.INVENTORY)
        self.assertEqual(doc.status, models.Document.Status.POSTED)
        self.assertEqual(doc.comment, stock_service.INITIAL_STOCK_COMMENT)
        self.assertFalse(models.MoneyDocument.objects.filter(source_document=doc).exists())

    def test_11_upsert_by_barcode_does_not_change_quantity(self):
        p = self._product("Штрих", code="BC1", qty="7", barcode="4600000000011")
        url = reverse("warehouse-products", kwargs={"warehouse_uuid": self.wh.pk})
        resp = self.api.post(url, {"name": "Штрих", "barcode": "4600000000011", "quantity": "5", "warehouse": str(self.wh.pk)}, format="json")
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertEqual(resp.data["product_id"], str(p.pk))
        self.assertEqual(self._card(p), Decimal("7.000"))
        # без quantity upsert обновляет карточку, но не остаток
        resp = self.api.post(url, {"name": "Штрих 2", "barcode": "4600000000011", "price": "99", "warehouse": str(self.wh.pk)}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        p.refresh_from_db()
        self.assertEqual(p.name, "Штрих 2")
        self.assertEqual(p.quantity, Decimal("7.000"))
        self.assertEqual(self._bal(p), Decimal("7.000"))

    def test_12_transfer_does_not_merge_by_name(self):
        src = self._product("Чай", code="TEA-SRC", qty="10")
        other_tea = self._product("Чай", qty="4", warehouse=self.wh2)  # code сгенерируется, без штрихкода
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER, warehouse_from=self.wh, warehouse_to=self.wh2,
        )
        models.DocumentItem.objects.create(document=doc, product=src, qty=Decimal("3"), price=Decimal("1"))
        services.post_document(doc)
        self.assertEqual(self._bal(src), Decimal("7.000"))
        self.assertEqual(self._bal(other_tea), Decimal("4.000"))
        self.assertEqual(self._card(other_tea), Decimal("4.000"))
        new_tea = models.WarehouseProduct.objects.exclude(pk=other_tea.pk).get(warehouse=self.wh2, name="Чай")
        self.assertEqual(self._bal(new_tea), Decimal("3.000"))
        self.assertEqual(self._card(new_tea), Decimal("3.000"))

    def test_13_invariant_after_mixed_flow_with_unpost_storno(self):
        doc = self._sale("10")
        self._sale("5")
        services.unpost_document(doc)
        self.assertEqual(self._bal(), Decimal("95.000"))
        self.assertEqual(self._card(), Decimal("95.000"))
        # сторно: исходные движения сохранены (отвязаны от документа), документ без движений
        doc.refresh_from_db()
        self.assertEqual(doc.moves.count(), 0)
        hist = models.StockMove.objects.filter(source_id=doc.pk).order_by("created_at")
        self.assertEqual(sorted(m.qty_delta for m in hist), [Decimal("-10.000"), Decimal("10.000")])
        # повторное проведение
        services.post_document(doc, allow_duplicate=True)
        self.assertEqual(self._bal(), Decimal("85.000"))
        self.assertEqual(doc.moves.count(), 1)
        self.assertInvariant()

    # ------------------------------------------------- lazy opening / reconcile
    def test_lazy_opening_for_product_without_balance(self):
        """Товар с остатком только в карточке: первая продажа инициализирует регистр (opening)."""
        p = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh, name="Старый",
            code="OLD1", unit="шт", is_weight=False, price=Decimal("10"), quantity=Decimal("30"),
        )
        self.assertFalse(models.StockBalance.objects.filter(product=p).exists())
        self.assertEqual(stock_service.get_on_hand(warehouse=self.wh, product=p), Decimal("30.000"))
        self._sale("5", product=p)
        self.assertEqual(self._bal(p), Decimal("25.000"))
        self.assertEqual(self._card(p), Decimal("25.000"))
        opening = models.StockMove.objects.get(product=p, source_kind=models.StockMove.SourceKind.OPENING)
        self.assertEqual(opening.qty_delta, Decimal("30.000"))
        self.assertIsNone(opening.document_id)

    def test_reconcile_command_report_init_and_decisions(self):
        # 1) без регистра; 2) карточка < регистра; 3) регистр без движений
        no_bal = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh, name="БезРегистра",
            code="R1", unit="шт", is_weight=False, quantity=Decimal("12"),
        )
        drift = self._product("Нори", code="R2", qty="6183")
        models.WarehouseProduct.objects.filter(pk=drift.pk).update(quantity=Decimal("0"))
        raw = models.WarehouseProduct.objects.create(
            company=self.company, branch=self.branch, warehouse=self.wh, name="БезДвижений",
            code="R3", unit="шт", is_weight=False, quantity=Decimal("4"),
        )
        models.StockBalance.objects.create(warehouse=self.wh, product=raw, qty=Decimal("4"))

        out = _io.StringIO()
        call_command("reconcile_warehouse_stock", "--company", str(self.company.pk), stdout=out, stderr=_io.StringIO())
        rows = {r["product_id"]: r for r in _csv.DictReader(_io.StringIO(out.getvalue()))}
        self.assertEqual(rows[str(no_bal.pk)]["issues"], "no_balance")
        self.assertEqual(rows[str(drift.pk)]["issues"], "card_ne_balance")
        self.assertEqual(rows[str(raw.pk)]["issues"], "balance_ne_moves")
        self.assertNotIn(str(self.product.pk), rows)
        # отчёт ничего не пишет
        self.assertFalse(models.StockBalance.objects.filter(product=no_bal).exists())

        # без --apply — только план
        call_command("reconcile_warehouse_stock", "--init-opening", stdout=_io.StringIO())
        self.assertFalse(models.StockBalance.objects.filter(product=no_bal).exists())

        for _ in range(2):  # идемпотентно
            call_command("reconcile_warehouse_stock", "--init-opening", "--apply", stdout=_io.StringIO())
        self.assertEqual(self._bal(no_bal), Decimal("12.000"))
        self.assertEqual(
            models.StockMove.objects.filter(product=no_bal, source_kind="opening").count(), 1
        )
        self.assertEqual(
            models.StockMove.objects.filter(product=raw, source_kind="opening").get().qty_delta,
            Decimal("4.000"),
        )

        # решение владельца по «Нори»: верное число 0
        fd, path = _tempfile.mkstemp(suffix=".csv")
        with _os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"product_id,qty,comment\n{drift.pk},0,пересчёт\n")
        try:
            call_command("reconcile_warehouse_stock", "--decisions", path, stdout=_io.StringIO())
            self.assertEqual(self._bal(drift), Decimal("6183.000"))
            call_command("reconcile_warehouse_stock", "--decisions", path, "--apply", stdout=_io.StringIO())
            call_command("reconcile_warehouse_stock", "--decisions", path, "--apply", stdout=_io.StringIO())
        finally:
            _os.remove(path)
        self.assertEqual(self._bal(drift), Decimal("0.000"))
        self.assertEqual(self._card(drift), Decimal("0.000"))
        self.assertEqual(
            models.Document.objects.filter(
                doc_type=models.Document.DocType.INVENTORY, items__product=drift,
                status=models.Document.Status.POSTED,
            ).count(),
            1,
        )

        out = _io.StringIO()
        call_command("reconcile_warehouse_stock", "--company", str(self.company.pk), stdout=out, stderr=_io.StringIO())
        self.assertEqual(len(list(_csv.DictReader(_io.StringIO(out.getvalue())))), 0)

    def test_check_stock_consistency_task(self):
        from apps.warehouse.tasks import check_stock_consistency

        self.assertEqual(check_stock_consistency()["card_ne_balance"], 0)
        models.WarehouseProduct.objects.filter(pk=self.product.pk).update(quantity=Decimal("1"))
        counts = check_stock_consistency()
        self.assertEqual(counts["card_ne_balance"], 1)
        models.WarehouseProduct.objects.filter(pk=self.product.pk).update(quantity=Decimal("100"))
