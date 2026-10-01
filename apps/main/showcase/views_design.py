from __future__ import annotations

import copy
import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from PIL import Image, UnidentifiedImageError
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.integrations.events import emit_event
from apps.main.models import (
    Product,
    ProductPromotionTier,
    ShowcaseBanner,
    ShowcaseDesign,
    ShowcaseDesignVersion,
    ShowcaseMedia,
    ShowcaseOrder,
    ShowcaseOrderItem,
    ShowcasePromoBlock,
    ShowcaseStats,
    _cart_item_promotion,
    _money,
    get_default_showcase_design,
)
from apps.main.showcase.serializers_design import (
    ShowcaseBannerSerializer,
    ShowcaseDesignSerializer,
    ShowcaseDesignVersionSerializer,
    ShowcaseMediaSerializer,
    ShowcaseOrderCreateSerializer,
    ShowcaseOrderSerializer,
    ShowcasePromoBlockSerializer,
    check_theme_contrast,
    validate_cards_data,
    validate_layout_data,
    validate_theme_data,
)
from apps.users.models import Company


PREVIEW_SALT = "showcase_preview"
MAX_MEDIA_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MEDIA_TARGET_WIDTHS = [1920, 1080, 640]


def _get_user_company(request) -> Company:
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        raise PermissionDenied("Требуется авторизация.")
    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    if not company:
        raise PermissionDenied("У пользователя не найдена компания.")
    return company


def _is_owner_or_admin(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "role", None)
    if role in ("owner", "admin"):
        return True
    if getattr(user, "owned_company_id", None):
        return True
    return False


class IsShowcaseEditorPermission(permissions.BasePermission):
    message = "Редактировать витрину может только владелец или администратор компании."
    code = "permission_denied"

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        return _is_owner_or_admin(user)


class IsShowcaseStaffPermission(permissions.BasePermission):
    message = "Доступно только сотрудникам компании."
    code = "permission_denied"

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)


def _get_or_create_design(company: Company) -> ShowcaseDesign:
    design, _ = ShowcaseDesign.objects.get_or_create(
        company=company,
        defaults={
            "draft": get_default_showcase_design(),
            "published": get_default_showcase_design(),
            "version": 1,
        },
    )
    return design


def _deep_merge(source: dict, overrides: dict) -> dict:
    """Рекурсивное слияние словарей без перезаписи непереданных вложенных полей."""
    result = copy.deepcopy(source)
    for key, value in overrides.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ======================================================================
# SC-01, SC-02, SC-03, SC-06, SC-08: Design CRUD, Draft, Publish, Restore
# ======================================================================

class ShowcaseDesignAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def get(self, request):
        company = _get_user_company(request)
        design = _get_or_create_design(company)
        serializer = ShowcaseDesignSerializer(design)
        return Response(serializer.data)


class ShowcaseDraftUpdateAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def patch(self, request):
        company = _get_user_company(request)
        design = _get_or_create_design(company)
        data = request.data
        if not isinstance(data, dict):
            raise ValidationError({"detail": "Ожидается объект с настройками черновика."})

        draft = design.draft or get_default_showcase_design()

        # Валидация отдельных блоков
        if "theme" in data:
            data["theme"] = validate_theme_data(data["theme"])
        if "layout" in data:
            data["layout"] = validate_layout_data(data["layout"])
        if "cards" in data:
            data["cards"] = validate_cards_data(data["cards"])
        if "carousel" in data:
            if not isinstance(data["carousel"], dict):
                raise ValidationError({"carousel": "Ожидается объект."})
        if "brand" in data:
            if not isinstance(data["brand"], dict):
                raise ValidationError({"brand": "Ожидается объект."})
        if "footer" in data:
            if not isinstance(data["footer"], dict):
                raise ValidationError({"footer": "Ожидается объект."})

        # Слияние с текущим черновиком
        updated_draft = _deep_merge(draft, data)
        design.draft = updated_draft
        design.save(update_fields=["draft", "updated_at"])

        # Проверка контраста
        warnings = check_theme_contrast(updated_draft.get("theme", {}))

        return Response({"draft": design.draft, "warnings": warnings})


class ShowcaseDraftProductOrderAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def patch(self, request):
        company = _get_user_company(request)
        design = _get_or_create_design(company)
        order_list = request.data.get("order")
        if not isinstance(order_list, list):
            raise ValidationError({"order": "Ожидается список ID товаров."})

        draft = design.draft or get_default_showcase_design()
        if "layout" not in draft or not isinstance(draft["layout"], dict):
            draft["layout"] = {}
        draft["layout"]["product_order"] = [str(x) for x in order_list]

        design.draft = draft
        design.save(update_fields=["draft", "updated_at"])
        return Response({"draft": design.draft, "order": draft["layout"]["product_order"]})


class ShowcasePreviewLinkAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def post(self, request):
        company = _get_user_company(request)
        signer = TimestampSigner(salt=PREVIEW_SALT)
        token = signer.sign(str(company.slug))
        url = f"https://nurcrm.kg/catalog/{company.slug}?preview={token}"
        expires_at = (timezone.now() + timedelta(hours=24)).isoformat()
        return Response({"url": url, "expires_at": expires_at})


class ShowcasePublishAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def post(self, request):
        company = _get_user_company(request)
        idempotency_key = (
            request.headers.get("Idempotency-Key")
            or request.data.get("idempotency_key")
            or ""
        ).strip()

        if idempotency_key:
            cache_key = f"showcase_publish:{company.id}:{idempotency_key}"
            cached_resp = cache.get(cache_key)
            if cached_resp:
                return Response(cached_resp, status=status.HTTP_200_OK)

        design = _get_or_create_design(company)
        draft = copy.deepcopy(design.draft or get_default_showcase_design())

        # Собираем актуальные баннеры и промо-блоки в опубликованный снимок
        active_banners = ShowcaseBanner.objects.filter(company=company, active=True).order_by("position", "created_at")
        active_promos = ShowcasePromoBlock.objects.filter(company=company, active=True).order_by("position", "created_at")

        draft["banners"] = ShowcaseBannerSerializer(active_banners, many=True).data
        draft["promo_blocks"] = ShowcasePromoBlockSerializer(active_promos, many=True).data

        now = timezone.now()
        new_version = design.version + 1

        with transaction.atomic():
            design.published = draft
            design.version = new_version
            design.published_at = now
            design.save()

            ShowcaseDesignVersion.objects.create(
                company=company,
                version=new_version,
                snapshot=copy.deepcopy(draft),
                author=request.user,
                published_at=now,
            )

            # Ограничиваем историю 20 версиями
            excess_ids = list(
                ShowcaseDesignVersion.objects.filter(company=company)
                .order_by("-version")[20:]
                .values_list("id", flat=True)
            )
            if excess_ids:
                ShowcaseDesignVersion.objects.filter(id__in=excess_ids).delete()

        resp_data = {"version": new_version, "published_at": now.isoformat()}
        if idempotency_key:
            cache.set(f"showcase_publish:{company.id}:{idempotency_key}", resp_data, 86400 * 7)

        return Response(resp_data, status=status.HTTP_200_OK)


class ShowcaseDiscardAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def post(self, request):
        company = _get_user_company(request)
        design = _get_or_create_design(company)
        design.draft = copy.deepcopy(design.published or get_default_showcase_design())
        design.save(update_fields=["draft", "updated_at"])
        return Response({"draft": design.draft, "detail": "Черновик сброшен к опубликованной версии."})


class ShowcaseVersionsListAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def get(self, request):
        company = _get_user_company(request)
        versions = ShowcaseDesignVersion.objects.filter(company=company).order_by("-version")[:20]
        serializer = ShowcaseDesignVersionSerializer(versions, many=True)
        return Response(serializer.data)


class ShowcaseVersionRestoreAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def post(self, request, version: int):
        company = _get_user_company(request)
        ver = ShowcaseDesignVersion.objects.filter(company=company, version=version).first()
        if not ver:
            raise NotFound(detail=f"Версия {version} не найдена.", code="not_found")

        design = _get_or_create_design(company)
        design.draft = copy.deepcopy(ver.snapshot)
        design.save(update_fields=["draft", "updated_at"])
        return Response({"draft": design.draft, "detail": f"Версия {version} восстановлена в черновик."})


# ======================================================================
# SC-07: Showcase Media Upload and Delete
# ======================================================================

class ShowcaseMediaListCreateAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        company = _get_user_company(request)
        idempotency_key = (
            request.headers.get("Idempotency-Key")
            or request.data.get("idempotency_key")
            or ""
        ).strip()

        if idempotency_key:
            existing = ShowcaseMedia.objects.filter(
                company=company, idempotency_key=idempotency_key
            ).first()
            if existing:
                return Response(
                    {
                        "id": str(existing.id),
                        "urls": existing.urls,
                        "width": existing.width,
                        "height": existing.height,
                    },
                    status=status.HTTP_200_OK,
                )

        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError({"file": "Файл не передан."})

        if upload.size > MAX_MEDIA_SIZE_BYTES:
            raise ValidationError(
                {"file": "Размер файла превышает 5 МБ.", "detail": "Файл больше 5 МБ."},
                code="file_too_large",
            )

        try:
            upload.seek(0)
            img = Image.open(upload)
            img_format = (img.format or "").upper()
            if img_format not in ("JPEG", "JPG", "PNG", "WEBP"):
                raise ValidationError(
                    {"file": "Файл должен быть JPG, PNG или WebP."},
                    code="invalid_image_format",
                )
            orig_width, orig_height = img.size
        except (UnidentifiedImageError, OSError):
            raise ValidationError(
                {"file": "Некорректный файл изображения."},
                code="invalid_image",
            )

        media_id = uuid.uuid4()
        urls_dict = {}

        # Режим цвета
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")

        # Создаём размеры: 1920, 1080, 640
        for target_w in MEDIA_TARGET_WIDTHS:
            if orig_width > target_w:
                ratio = target_w / float(orig_width)
                target_h = max(1, int(round(orig_height * ratio)))
                resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            else:
                resized = img

            buf = io.BytesIO()
            resized.save(buf, format="WEBP", quality=85, method=6)
            buf.seek(0)

            path = f"showcase_media/{company.id}/{media_id.hex}_{target_w}.webp"
            saved_name = default_storage.save(path, ContentFile(buf.read()))
            urls_dict[str(target_w)] = request.build_absolute_uri(default_storage.url(saved_name))

        # Сохраняем оригинал/основной файл
        upload.seek(0)
        ext = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else "webp"
        main_path = f"showcase_media/{company.id}/{media_id.hex}.{ext}"
        saved_main = default_storage.save(main_path, upload)

        media = ShowcaseMedia.objects.create(
            id=media_id,
            company=company,
            file=saved_main,
            urls=urls_dict,
            width=orig_width,
            height=orig_height,
            content_type="image/webp",
            idempotency_key=idempotency_key or None,
        )

        return Response(
            {
                "id": str(media.id),
                "urls": media.urls,
                "width": media.width,
                "height": media.height,
            },
            status=status.HTTP_201_CREATED,
        )


class ShowcaseMediaDetailAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def delete(self, request, pk):
        company = _get_user_company(request)
        media = get_object_or_404(ShowcaseMedia, id=pk, company=company)

        # Проверка использования в опубликованных баннерах
        media_id_str = str(media.id)
        banner_in_use = ShowcaseBanner.objects.filter(
            company=company, active=True
        ).filter(Q(image=media) | Q(image_mobile=media)).exists()

        design = getattr(company, "showcase_design", None)
        in_design = False
        if design and design.published:
            pub = design.published
            brand = pub.get("brand") or {}
            if brand.get("logo") == media_id_str or brand.get("favicon") == media_id_str:
                in_design = True
            for b in pub.get("banners") or []:
                if str(b.get("image")) == media_id_str or str(b.get("image_mobile")) == media_id_str:
                    in_design = True
                    break

        if banner_in_use or in_design:
            return Response(
                {
                    "detail": "Изображение используется в опубликованной витрине или баннере и не может быть удалено.",
                    "code": "media_in_use",
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Удаление файлов из хранилища
        try:
            if media.file:
                media.file.delete(save=False)
            for w in MEDIA_TARGET_WIDTHS:
                path = f"showcase_media/{company.id}/{media.id.hex}_{w}.webp"
                if default_storage.exists(path):
                    default_storage.delete(path)
        except Exception:
            pass

        media.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ======================================================================
# SC-04: Banners CRUD & Reorder
# ======================================================================

class ShowcaseBannerListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ShowcaseBannerSerializer
    permission_classes = [IsShowcaseEditorPermission]
    pagination_class = None

    def get_queryset(self):
        company = _get_user_company(self.request)
        return ShowcaseBanner.objects.filter(company=company).order_by("position", "created_at")

    def perform_create(self, serializer):
        company = _get_user_company(self.request)
        serializer.save(company=company)


class ShowcaseBannerDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ShowcaseBannerSerializer
    permission_classes = [IsShowcaseEditorPermission]

    def get_queryset(self):
        company = _get_user_company(self.request)
        return ShowcaseBanner.objects.filter(company=company)


class ShowcaseBannerReorderAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def post(self, request):
        company = _get_user_company(request)
        order_list = request.data.get("order")
        if not isinstance(order_list, list):
            raise ValidationError({"order": "Ожидается список ID баннеров."})

        banners = {str(b.id): b for b in ShowcaseBanner.objects.filter(company=company)}
        updates = []
        for pos, banner_id in enumerate(order_list):
            bid = str(banner_id)
            if bid in banners:
                b = banners[bid]
                b.position = pos
                updates.append(b)

        if updates:
            ShowcaseBanner.objects.bulk_update(updates, ["position"])

        return Response({"status": "ok", "reordered": len(updates)})


# ======================================================================
# SC-05: Promo Blocks CRUD & Reorder
# ======================================================================

class ShowcasePromoBlockListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ShowcasePromoBlockSerializer
    permission_classes = [IsShowcaseEditorPermission]
    pagination_class = None

    def get_queryset(self):
        company = _get_user_company(self.request)
        return ShowcasePromoBlock.objects.filter(company=company).order_by("position", "created_at")

    def perform_create(self, serializer):
        company = _get_user_company(self.request)
        serializer.save(company=company)


class ShowcasePromoBlockDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ShowcasePromoBlockSerializer
    permission_classes = [IsShowcaseEditorPermission]

    def get_queryset(self):
        company = _get_user_company(self.request)
        return ShowcasePromoBlock.objects.filter(company=company)


class ShowcasePromoBlockReorderAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def post(self, request):
        company = _get_user_company(request)
        order_list = request.data.get("order")
        if not isinstance(order_list, list):
            raise ValidationError({"order": "Ожидается список ID промо-блоков."})

        blocks = {str(b.id): b for b in ShowcasePromoBlock.objects.filter(company=company)}
        updates = []
        for pos, block_id in enumerate(order_list):
            bid = str(block_id)
            if bid in blocks:
                b = blocks[bid]
                b.position = pos
                updates.append(b)

        if updates:
            ShowcasePromoBlock.objects.bulk_update(updates, ["position"])

        return Response({"status": "ok", "reordered": len(updates)})


# ======================================================================
# SC-09: Public Showcase Design API
# ======================================================================

def _resolve_promo_block_items(company: Company, promo_block: ShowcasePromoBlock) -> List[dict]:
    """Разрешает товары и скидки для промо-блока витрины."""
    source = promo_block.source or {}
    source_type = source.get("type", "promotions")
    ids = [str(x) for x in source.get("ids", []) if x]
    max_items = promo_block.max_items or 12

    items = []
    if source_type == "promotions":
        qs = (
            Product.objects.filter(company=company, stock=True)
            .prefetch_related("promotion_tiers", "images")
        )
        if ids:
            qs = qs.filter(Q(id__in=ids) | Q(promotion_tiers__id__in=ids))
        qs = qs.distinct()[:max_items]

        for p in qs:
            tiers = list(p.promotion_tiers.all())
            if not tiers and not ids:
                continue
            max_dp = max((t.discount_percent for t in tiers), default=Decimal("0"))
            min_amt = min((t.min_amount for t in tiers), default=Decimal("0"))
            first_img = p.images.first()
            img_url = first_img.image.url if (first_img and first_img.image) else None
            items.append({
                "id": str(p.id),
                "name": p.name,
                "price": str(p.price or "0.00"),
                "discount_percent": str(max_dp),
                "min_amount": str(min_amt),
                "image": img_url,
                "badge": f"-{int(max_dp)}%" if max_dp else None,
            })
    else:  # products
        qs = (
            Product.objects.filter(company=company)
            .prefetch_related("images")
        )
        if ids:
            qs = qs.filter(id__in=ids)
        qs = qs[:max_items]
        for p in qs:
            first_img = p.images.first()
            img_url = first_img.image.url if (first_img and first_img.image) else None
            items.append({
                "id": str(p.id),
                "name": p.name,
                "price": str(p.price or "0.00"),
                "image": img_url,
            })

    return items


class PublicCompanyShowcaseDesignAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, slug: str):
        company = get_object_or_404(Company, slug=slug)
        preview_token = request.query_params.get("preview")

        # Режим предпросмотра
        if preview_token:
            signer = TimestampSigner(salt=PREVIEW_SALT)
            try:
                val = signer.unsign(preview_token, max_age=86400)
                if val != company.slug:
                    raise BadSignature()
            except (BadSignature, SignatureExpired):
                return Response(
                    {"detail": "Ссылка предпросмотра недействительна или истекла.", "code": "invalid_preview_token"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            design = getattr(company, "showcase_design", None)
            draft = copy.deepcopy(design.draft if design else get_default_showcase_design())

            # В предпросмотре баннеры и промо-блоки берутся прямо из базы (живые)
            now = timezone.now()
            banners = ShowcaseBanner.objects.filter(company=company, active=True).order_by("position", "created_at")
            promo_blocks = ShowcasePromoBlock.objects.filter(company=company, active=True).order_by("position", "created_at")

            resolved_banners = ShowcaseBannerSerializer(banners, many=True).data
            resolved_promos = []
            for pb in promo_blocks:
                pdata = ShowcasePromoBlockSerializer(pb).data
                pdata["items"] = _resolve_promo_block_items(company, pb)
                resolved_promos.append(pdata)

            response_data = {
                "version": (design.version if design else 1),
                "theme": draft.get("theme", {}),
                "layout": draft.get("layout", {}),
                "cards": draft.get("cards", {}),
                "carousel": draft.get("carousel", {}),
                "brand": draft.get("brand", {}),
                "footer": draft.get("footer", {}),
                "banners": resolved_banners,
                "promo_blocks": resolved_promos,
                "preview": True,
            }
            resp = Response(response_data)
            resp["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return resp

        # Обычный публичный запрос: ETag и опубликованные настройки
        design = getattr(company, "showcase_design", None)
        version = design.version if design else 1
        etag = f'"v{version}"'

        if_none_match = request.headers.get("If-None-Match") or request.META.get("HTTP_IF_NONE_MATCH")
        if if_none_match and if_none_match.strip() == etag:
            response = HttpResponse(status=status.HTTP_304_NOT_MODIFIED)
            response["ETag"] = etag
            return response

        published = copy.deepcopy(design.published if design else get_default_showcase_design())
        now = timezone.now()

        # Фильтруем баннеры по периоду показа
        banners_qs = ShowcaseBanner.objects.filter(
            company=company,
            active=True,
        ).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gte=now),
        ).order_by("position", "created_at")
        active_banners = ShowcaseBannerSerializer(banners_qs, many=True).data

        # Разрешаем промо-блоки
        promos_qs = ShowcasePromoBlock.objects.filter(company=company, active=True).order_by("position", "created_at")
        active_promos = []
        for pb in promos_qs:
            items = _resolve_promo_block_items(company, pb)
            if not items:
                continue
            pb_data = ShowcasePromoBlockSerializer(pb).data
            pb_data["items"] = items
            active_promos.append(pb_data)

        response_data = {
            "version": version,
            "theme": published.get("theme", {}),
            "layout": published.get("layout", {}),
            "cards": published.get("cards", {}),
            "carousel": published.get("carousel", {}),
            "brand": published.get("brand", {}),
            "footer": published.get("footer", {}),
            "banners": active_banners,
            "promo_blocks": active_promos,
        }

        resp = Response(response_data)
        resp["ETag"] = etag
        resp["Cache-Control"] = "public, max-age=60"
        return resp


# ======================================================================
# SC-10: Orders from Showcase
# ======================================================================

class PublicCompanyShowcaseOrderCreateAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, slug: str):
        company = get_object_or_404(Company, slug=slug)
        idempotency_key = (
            request.headers.get("Idempotency-Key")
            or request.data.get("idempotency_key")
            or ""
        ).strip()

        if idempotency_key:
            existing = ShowcaseOrder.objects.filter(
                company=company, idempotency_key=idempotency_key
            ).first()
            if existing:
                return Response(
                    {
                        "id": str(existing.id),
                        "number": existing.number,
                        "status": existing.status,
                        "total": str(existing.total),
                        "created_at": existing.created_at.isoformat(),
                    },
                    status=status.HTTP_200_OK,
                )

        serializer = ShowcaseOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        items_input = data["items"]
        product_ids = [item["product"] for item in items_input]
        products = {
            str(p.id): p
            for p in Product.objects.filter(company=company, id__in=product_ids).prefetch_related("promotion_tiers")
        }

        # Проверка скрытых товаров
        design = getattr(company, "showcase_design", None)
        hidden_ids = set()
        if design and design.published:
            layout = design.published.get("layout") or {}
            hidden_ids = {str(x) for x in layout.get("hidden_products", [])}

        total_amount = Decimal("0.00")
        order_items_data = []

        for item in items_input:
            pid = str(item["product"])
            if pid not in products:
                raise ValidationError({"items": f"Товар {pid} не найден или недоступен."})
            if pid in hidden_ids:
                raise ValidationError({"items": f"Товар {pid} скрыт с витрины."})

            prod = products[pid]
            qty = Decimal(str(item["qty"]))
            unit_price = Decimal(str(prod.price or 0))

            line_discount = _cart_item_promotion(prod, unit_price, qty)[0]
            gross = _money(unit_price * qty)
            net = _money(gross - line_discount)
            total_amount += net

            order_items_data.append({
                "product": prod,
                "product_name": prod.name,
                "qty": qty,
                "price": unit_price,
                "discount": line_discount,
                "total": net,
            })

        customer_data = data["customer"]
        delivery_data = data.get("delivery") or {}

        with transaction.atomic():
            last_num = (
                ShowcaseOrder.objects.filter(company=company)
                .select_for_update()
                .aggregate(m=Max("number"))["m"]
                or 0
            )
            order_number = last_num + 1

            order = ShowcaseOrder.objects.create(
                company=company,
                number=order_number,
                status=ShowcaseOrder.Status.NEW,
                customer_name=customer_data["name"],
                customer_phone=customer_data["phone"],
                delivery_type=delivery_data.get("type", "pickup"),
                delivery_address=delivery_data.get("address", ""),
                source=data.get("source") or "showcase",
                comment=data.get("comment", ""),
                total=total_amount,
                idempotency_key=idempotency_key or None,
            )

            items_to_create = [
                ShowcaseOrderItem(
                    order=order,
                    product=it["product"],
                    product_name=it["product_name"],
                    qty=it["qty"],
                    price=it["price"],
                    discount=it["discount"],
                    total=it["total"],
                )
                for it in order_items_data
            ]
            ShowcaseOrderItem.objects.bulk_create(items_to_create)

        # Отправляем вебхук order.created
        webhook_data = {
            "id": str(order.id),
            "number": order.number,
            "status": order.status,
            "total": str(order.total),
            "source": order.source,
            "customer": {
                "name": order.customer_name,
                "phone": order.customer_phone,
            },
            "delivery": {
                "type": order.delivery_type,
                "address": order.delivery_address,
            },
            "comment": order.comment,
            "items": [
                {
                    "product": str(it.product_id) if it.product_id else None,
                    "product_name": it.product_name,
                    "qty": str(it.qty),
                    "price": str(it.price),
                    "discount": str(it.discount),
                    "total": str(it.total),
                }
                for it in order.items.all()
            ],
            "created_at": order.created_at.isoformat(),
        }
        try:
            emit_event(company.id, "order.created", webhook_data)
        except Exception:
            pass

        return Response(
            {
                "id": str(order.id),
                "number": order.number,
                "status": order.status,
                "total": str(order.total),
                "source": order.source,
                "created_at": order.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class ShowcaseOrderListAPIView(generics.ListAPIView):
    serializer_class = ShowcaseOrderSerializer
    permission_classes = [IsShowcaseStaffPermission]
    pagination_class = None

    def get_queryset(self):
        company = _get_user_company(self.request)
        qs = ShowcaseOrder.objects.filter(company=company).prefetch_related("items")
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        source_param = self.request.query_params.get("source")
        if source_param:
            qs = qs.filter(source=source_param)

        date_from = self.request.query_params.get("date_from")
        if date_from:
            df = parse_date(date_from)
            if df:
                qs = qs.filter(created_at__date__gte=df)

        date_to = self.request.query_params.get("date_to")
        if date_to:
            dt = parse_date(date_to)
            if dt:
                qs = qs.filter(created_at__date__lte=dt)

        return qs.order_by("-created_at")


class ShowcaseOrderDetailAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = ShowcaseOrderSerializer
    permission_classes = [IsShowcaseStaffPermission]

    def get_queryset(self):
        company = _get_user_company(self.request)
        return ShowcaseOrder.objects.filter(company=company).prefetch_related("items")

    def patch(self, request, *args, **kwargs):
        order = self.get_object()
        new_status = request.data.get("status")
        if new_status:
            if new_status not in ShowcaseOrder.Status.values:
                raise ValidationError(
                    {"status": f"Недопустимый статус. Допустимые: {', '.join(ShowcaseOrder.Status.values)}"}
                )
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])
        return Response(ShowcaseOrderSerializer(order).data)


# ======================================================================
# SC-11: Showcase Statistics & Tracking
# ======================================================================

class PublicCompanyShowcaseTrackAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, slug: str):
        company = get_object_or_404(Company, slug=slug)
        event_type = request.data.get("event")
        product_id = request.data.get("product_id")
        banner_id = request.data.get("banner_id")
        session_id = request.data.get("session_id") or ""

        today = timezone.localdate()

        # Дедупликация клика по баннеру в рамках сессии
        if event_type == "banner_click" and banner_id:
            bid = str(banner_id)
            sess_key = f"bclick:{company.id}:{bid}:{session_id or request.META.get('REMOTE_ADDR')}"
            if cache.get(sess_key):
                return Response({"status": "already_tracked"})
            cache.set(sess_key, 1, 86400)

        with transaction.atomic():
            stats, _ = ShowcaseStats.objects.select_for_update().get_or_create(
                company=company,
                date=today,
                defaults={
                    "views": 0,
                    "add_to_cart": 0,
                    "product_views": {},
                    "banner_clicks": {},
                },
            )

            if event_type == "view":
                stats.views += 1
            elif event_type == "add_to_cart":
                stats.add_to_cart += 1
            elif event_type == "product_view" and product_id:
                pid = str(product_id)
                pv = dict(stats.product_views or {})
                pv[pid] = pv.get(pid, 0) + 1
                stats.product_views = pv
            elif event_type == "banner_click" and banner_id:
                bid = str(banner_id)
                bc = dict(stats.banner_clicks or {})
                bc[bid] = bc.get(bid, 0) + 1
                stats.banner_clicks = bc

            stats.save()

        return Response({"status": "ok"})


class ShowcaseStatsAPIView(APIView):
    permission_classes = [IsShowcaseEditorPermission]

    def get(self, request):
        company = _get_user_company(request)
        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")

        today = timezone.localdate()
        date_from = parse_date(date_from_str) if date_from_str else (today - timedelta(days=30))
        date_to = parse_date(date_to_str) if date_to_str else today

        qs = ShowcaseStats.objects.filter(company=company, date__gte=date_from, date__lte=date_to)

        total_views = 0
        total_add_to_cart = 0
        combined_product_views: Dict[str, int] = {}
        combined_banner_clicks: Dict[str, int] = {}

        for st in qs:
            total_views += st.views
            total_add_to_cart += st.add_to_cart
            for pid, count in (st.product_views or {}).items():
                combined_product_views[pid] = combined_product_views.get(pid, 0) + count
            for bid, count in (st.banner_clicks or {}).items():
                combined_banner_clicks[bid] = combined_banner_clicks.get(bid, 0) + count

        # Заказы за период
        orders_count = ShowcaseOrder.objects.filter(
            company=company,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        ).count()

        # Разрешаем названия товаров
        product_views_list = []
        if combined_product_views:
            products_map = {
                str(p.id): p.name
                for p in Product.objects.filter(id__in=list(combined_product_views.keys()))
            }
            for pid, count in sorted(combined_product_views.items(), key=lambda x: -x[1])[:50]:
                product_views_list.append({
                    "id": pid,
                    "name": products_map.get(pid, ""),
                    "views": count,
                })

        # Разрешаем названия баннеров
        banner_clicks_list = []
        if combined_banner_clicks:
            banners_map = {
                str(b.id): b.title
                for b in ShowcaseBanner.objects.filter(id__in=list(combined_banner_clicks.keys()))
            }
            for bid, count in sorted(combined_banner_clicks.items(), key=lambda x: -x[1]):
                banner_clicks_list.append({
                    "id": bid,
                    "title": banners_map.get(bid, ""),
                    "clicks": count,
                })

        return Response({
            "views": total_views,
            "add_to_cart": total_add_to_cart,
            "orders": orders_count,
            "product_views": product_views_list,
            "banner_clicks": banner_clicks_list,
        })
