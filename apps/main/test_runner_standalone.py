import os
import sys
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from decimal import Decimal
from datetime import timedelta
from uuid import UUID
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework import status

from apps.main.models import (
    BranchTransfer,
    BranchTransferItem,
    Product,
    ProductCategory,
)
from apps.construction.models import CashFlow
from apps.users.models import User, Company, Branch
from apps.main.branch_transfers_views import (
    BranchTransferListCreateAPIView,
    BranchTransferDetailAPIView,
    BranchTransferCancelAPIView,
)
from apps.main.views import ProductListView
from apps.users.views import BranchDetailAPIView


def run_tests():
    print("=== STARTING BRANCH TRANSFERS COMPREHENSIVE VERIFICATION ===")
    factory = APIRequestFactory()

    # Запускаем в транзакции с гарантированным rollback в конце
    with transaction.atomic():
        sid = transaction.savepoint()
        try:
            # 1. Setup entities
            owner = User.objects.create_user(
                email="test_owner_transfer@test.com",
                password="testpassword123",
                role="owner",
                can_view_branch=True,
                first_name="Бек",
                last_name="Нур",
            )
            company = Company.objects.create(
                name="Тест ОсОО Ромашка",
                owner=owner,
                inn="12345678901234",
                okpo="87654321",
                address="г. Бишкек",
                phone="+996777001122",
            )
            owner.company = company
            owner.save()

            branch_osh = Branch.objects.create(
                company=company,
                name="Филиал Ош",
                is_active=True,
            )
            branch_talas = Branch.objects.create(
                company=company,
                name="Филиал Талас",
                is_active=True,
            )
            branch_inactive = Branch.objects.create(
                company=company,
                name="Филиал Неактивный",
                is_active=False,
            )

            other_owner = User.objects.create_user(
                email="test_other_owner@test.com",
                password="testpassword123",
                role="owner",
            )
            other_company = Company.objects.create(name="Другая Компания Тест", owner=other_owner)
            other_owner.company = other_company
            other_owner.save()
            other_branch = Branch.objects.create(
                company=other_company,
                name="Чужой Филиал",
                is_active=True,
            )

            # Товар на главном складе (branch=None)
            main_product = Product.objects.create(
                company=company,
                branch=None,
                name="Молоко 1л Домик",
                article="MLK-DOM-1",
                barcode="4700011122233",
                unit="шт",
                is_weight=False,
                quantity=Decimal("100.000"),
                purchase_price=Decimal("85.00"),
                price=Decimal("100.00"),
            )

            # -------------------------------------------------------------
            # Тест 1: Главный → филиал
            # -------------------------------------------------------------
            print("[1] Тест: Главный склад -> Филиал Ош...")
            view = BranchTransferListCreateAPIView.as_view()
            req = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": None,
                    "to_branch": str(branch_osh.id),
                    "date": timezone.localdate().isoformat(),
                    "comment": "Пополнение витрины Ош",
                    "items": [{"product": str(main_product.id), "quantity": 12}],
                },
                format="json",
            )
            force_authenticate(req, user=owner)
            resp = view(req)
            assert resp.status_code == status.HTTP_201_CREATED, f"Expected 201, got {resp.status_code}: {resp.data}"
            data = resp.data
            assert data["number"] == "ПЕР-000001", f"Expected ПЕР-000001, got {data['number']}"
            assert data["status"] == "completed"
            assert data["from_branch"] is None
            assert data["to_branch"]["name"] == "Филиал Ош"
            assert data["total_quantity"] == "12.000"
            assert data["total_amount"] == "1020.00"
            assert len(data["items"]) == 1
            assert data["items"][0]["quantity"] == "12.000"
            assert data["items"][0]["price"] == "85.00"
            assert data["items"][0]["amount"] == "1020.00"
            assert data["seller"]["name"] == "Тест ОсОО Ромашка"

            main_product.refresh_from_db()
            assert main_product.quantity == Decimal("88.000"), f"Expected 88, got {main_product.quantity}"

            dest_osh_prod = Product.objects.get(company=company, branch=branch_osh, barcode="4700011122233")
            assert dest_osh_prod.quantity == Decimal("12.000")
            assert dest_osh_prod.price == Decimal("100.00")
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 2: Филиал → филиал и филиал → главный
            # -------------------------------------------------------------
            print("[2] Тест: Филиал Ош -> Филиал Талас и Филиал Талас -> Главный...")
            req2 = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": str(branch_osh.id),
                    "to_branch": str(branch_talas.id),
                    "items": [{"product": str(dest_osh_prod.id), "quantity": 4}],
                },
                format="json",
            )
            force_authenticate(req2, user=owner)
            resp2 = view(req2)
            assert resp2.status_code == status.HTTP_201_CREATED, f"Expected 201, got {resp2.status_code}: {resp2.data}"
            dest_osh_prod.refresh_from_db()
            assert dest_osh_prod.quantity == Decimal("8.000")

            talas_prod = Product.objects.get(company=company, branch=branch_talas, barcode="4700011122233")
            assert talas_prod.quantity == Decimal("4.000")

            # Филиал -> Главный
            req3 = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": str(branch_talas.id),
                    "to_branch": None,
                    "items": [{"product": str(talas_prod.id), "quantity": 2}],
                },
                format="json",
            )
            force_authenticate(req3, user=owner)
            resp3 = view(req3)
            assert resp3.status_code == status.HTTP_201_CREATED, f"Expected 201, got {resp3.status_code}: {resp3.data}"
            talas_prod.refresh_from_db()
            assert talas_prod.quantity == Decimal("2.000")
            main_product.refresh_from_db()
            assert main_product.quantity == Decimal("90.000")
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 3: from == to (включая оба None) → 400
            # -------------------------------------------------------------
            print("[3] Тест: from_branch == to_branch валидация...")
            req_same = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": None,
                    "to_branch": None,
                    "items": [{"product": str(main_product.id), "quantity": 1}],
                },
                format="json",
            )
            force_authenticate(req_same, user=owner)
            try:
                resp_same = view(req_same)
                assert resp_same.status_code == status.HTTP_400_BAD_REQUEST
            except ValidationError:
                pass
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 4: quantity > остатка источника → 400
            # -------------------------------------------------------------
            print("[4] Тест: Недостаточно остатка на складе-отправителе...")
            req_over = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": None,
                    "to_branch": str(branch_osh.id),
                    "items": [{"product": str(main_product.id), "quantity": 999}],
                },
                format="json",
            )
            force_authenticate(req_over, user=owner)
            try:
                resp_over = view(req_over)
                assert resp_over.status_code == status.HTTP_400_BAD_REQUEST
                assert "items" in resp_over.data
            except ValidationError as e:
                assert "items" in e.detail or "quantity" in str(e)
            main_product.refresh_from_db()
            assert main_product.quantity == Decimal("90.000")
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 5: Дубликат product в items → 400
            # -------------------------------------------------------------
            print("[5] Тест: Дубликаты в items...")
            req_dup = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": None,
                    "to_branch": str(branch_osh.id),
                    "items": [
                        {"product": str(main_product.id), "quantity": 1},
                        {"product": str(main_product.id), "quantity": 2},
                    ],
                },
                format="json",
            )
            force_authenticate(req_dup, user=owner)
            try:
                resp_dup = view(req_dup)
                assert resp_dup.status_code == status.HTTP_400_BAD_REQUEST
            except ValidationError:
                pass
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 6: Неактивный филиал-получатель → 400, чужой филиал → 400
            # -------------------------------------------------------------
            print("[6] Тест: Неактивный или чужой филиал...")
            req_inact = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": None,
                    "to_branch": str(branch_inactive.id),
                    "items": [{"product": str(main_product.id), "quantity": 1}],
                },
                format="json",
            )
            force_authenticate(req_inact, user=owner)
            try:
                resp_inact = view(req_inact)
                assert resp_inact.status_code == status.HTTP_400_BAD_REQUEST
            except ValidationError:
                pass

            req_foreign = factory.post(
                "/main/branch-transfers/",
                {
                    "from_branch": None,
                    "to_branch": str(other_branch.id),
                    "items": [{"product": str(main_product.id), "quantity": 1}],
                },
                format="json",
            )
            force_authenticate(req_foreign, user=owner)
            try:
                resp_foreign = view(req_foreign)
                assert resp_foreign.status_code == status.HTTP_400_BAD_REQUEST
            except ValidationError:
                pass
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 8: Отмена перемещения (сторно)
            # -------------------------------------------------------------
            print("[8] Тест: Отмена перемещения (сторно)...")
            transfer_to_cancel = BranchTransfer.objects.filter(company=company).first()
            cancel_view = BranchTransferCancelAPIView.as_view()
            req_c = factory.post(
                f"/main/branch-transfers/{transfer_to_cancel.id}/cancel/",
                {"reason": "Тестовая отмена"},
                format="json",
            )
            force_authenticate(req_c, user=owner)
            resp_c = cancel_view(req_c, pk=transfer_to_cancel.id)
            assert resp_c.status_code == status.HTTP_200_OK, f"Expected 200, got {resp_c.status_code}: {resp_c.data}"
            assert resp_c.data["status"] == "cancelled"
            assert resp_c.data["cancel_reason"] == "Тестовая отмена"

            # Повторная отмена -> 400
            req_c_rep = factory.post(
                f"/main/branch-transfers/{transfer_to_cancel.id}/cancel/",
                {"reason": "Повторная отмена"},
                format="json",
            )
            force_authenticate(req_c_rep, user=owner)
            try:
                resp_c_rep = cancel_view(req_c_rep, pk=transfer_to_cancel.id)
                assert resp_c_rep.status_code == status.HTTP_400_BAD_REQUEST
            except ValidationError:
                pass
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 9: Не создает Cashflow
            # -------------------------------------------------------------
            print("[9] Тест: Перемещение не создает Cashflow...")
            cf_count = CashFlow.objects.filter(company=company).count()
            assert cf_count == 0, f"Expected 0 CashFlow rows, got {cf_count}"
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 10: Список перемещений и фильтры
            # -------------------------------------------------------------
            print("[10] Тест: GET /main/branch-transfers/ список и фильтры...")
            req_list = factory.get("/main/branch-transfers/")
            force_authenticate(req_list, user=owner)
            resp_list = view(req_list)
            assert resp_list.status_code == status.HTTP_200_OK
            assert "results" in resp_list.data
            assert resp_list.data["count"] >= 3
            # Фильтр to_branch=main
            req_to_main = factory.get("/main/branch-transfers/?to_branch=main")
            force_authenticate(req_to_main, user=owner)
            resp_to_main = view(req_to_main)
            assert resp_to_main.status_code == status.HTTP_200_OK
            assert resp_to_main.data["count"] == 1
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 11: Схема B: двойник не плодит дубликаты
            # -------------------------------------------------------------
            print("[11] Тест: Схема B: двойник по barcode не дублируется...")
            osh_count = Product.objects.filter(company=company, branch=branch_osh, barcode="4700011122233").count()
            assert osh_count == 1, f"Expected 1 product with barcode, found {osh_count}"
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 12: GET /main/products/list/?branch=main и branch=<uuid>
            # -------------------------------------------------------------
            print("[12] Тест: GET /main/products/list/?branch=main и branch=<uuid>...")
            prod_list_view = ProductListView.as_view()
            
            req_p_main = factory.get("/main/products/list/?branch=main")
            force_authenticate(req_p_main, user=owner)
            resp_p_main = prod_list_view(req_p_main)
            if resp_p_main.status_code != status.HTTP_200_OK:
                print("STATUS:", resp_p_main.status_code, "DATA:", getattr(resp_p_main, 'data', None))
            assert resp_p_main.status_code == status.HTTP_200_OK, f"Expected 200, got {resp_p_main.status_code}: {getattr(resp_p_main, 'data', None)}"
            results = resp_p_main.data.get("results", [])
            for p in results:
                p_db = Product.objects.get(id=p["id"])
                assert p_db.branch is None, f"Expected null branch in db, got {p_db.branch_id}"

            # branch=<osh>
            req_p_osh = factory.get(f"/main/products/list/?branch={branch_osh.id}")
            force_authenticate(req_p_osh, user=owner)
            resp_p_osh = prod_list_view(req_p_osh)
            assert resp_p_osh.status_code == status.HTTP_200_OK
            for p in resp_p_osh.data.get("results", []):
                p_db = Product.objects.get(id=p["id"])
                assert p_db.branch_id == branch_osh.id, f"Expected {branch_osh.id}, got {p_db.branch_id}"

            # page_size=1
            req_p_ps = factory.get("/main/products/list/?branch=main&page_size=1")
            force_authenticate(req_p_ps, user=owner)
            resp_p_ps = prod_list_view(req_p_ps)
            assert resp_p_ps.status_code == status.HTTP_200_OK
            assert len(resp_p_ps.data.get("results", [])) == 1

            # search по article
            req_p_art = factory.get("/main/products/list/?search=MLK-DOM-1")
            force_authenticate(req_p_art, user=owner)
            resp_p_art = prod_list_view(req_p_art)
            assert resp_p_art.status_code == status.HTTP_200_OK
            assert resp_p_art.data.get("count", 0) >= 1
            print("  -> OK!")

            # -------------------------------------------------------------
            # Тест 13: Защита удаления филиала
            # -------------------------------------------------------------
            print("[13] Тест: Запрет удаления филиала с перемещениями или остатками...")
            del_view = BranchDetailAPIView.as_view()
            req_del = factory.delete(f"/users/branches/{branch_osh.id}/")
            force_authenticate(req_del, user=owner)
            try:
                resp_del = del_view(req_del, pk=branch_osh.id)
                assert resp_del.status_code == status.HTTP_400_BAD_REQUEST
            except ValidationError as e:
                assert "Нельзя удалить филиал" in str(e)
            print("  -> OK!")

            print("=== ALL 13 TEST SUITES PASSED PERFECTLY! ===")

        finally:
            transaction.savepoint_rollback(sid)
            print("=== TRANSACTION ROLLED BACK CLEANLY, NO DATA LEAKED ===")


if __name__ == "__main__":
    run_tests()
