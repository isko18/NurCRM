"""Проверка тел запросов для баннеров, блоков акций, страниц, товаров и категорий витрины."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from django.utils.dateparse import parse_datetime

from apps.main.showcase import design_schema as ds
from apps.main.showcase.design_schema import (
    Bool, Color, Ctx, Enum, I18n, Int, Link, ListOf, Media, Obj, UUIDItem, _err,
)
from apps.main.showcase.sanitize import sanitize_body

# Ключи ответа, которые фронт может прислать обратно — молча игнорируем.
_READ_ONLY = {"id", "created_at", "updated_at", "image_urls", "image_mobile_urls", "items", "products_count", "name"}


class _DateTime(ds.Field):
    nullable = True

    def check(self, value, path, ctx):
        dt = parse_datetime(str(value)) if isinstance(value, str) else None
        if dt is None:
            _err(path, "Ожидается дата и время ISO 8601, например 2026-10-01T00:00:00+06:00.")
        if dt.tzinfo is None:
            from django.utils import timezone

            dt = timezone.make_aware(dt)
        return dt


def _clean(spec: Dict[str, ds.Field], data: Any, company, partial: bool, required=()) -> dict:
    if not isinstance(data, dict):
        _err(None, "Ожидается объект.")
    ctx = Ctx(company)
    out = {}
    for key, value in data.items():
        if key in _READ_ONLY:
            continue
        f = spec.get(key)
        if f is None:
            _err(key, "Неизвестное поле.", "unknown_field")
        out[key] = f.patch(None, value, key, ctx)
    if not partial:
        for key in required:
            if key not in out or out[key] in (None, "", {}):
                _err(key, "Обязательное поле.", "required")
    return out


# ---------------------------------------------------------------- баннеры

BANNER_SPEC = {
    "title": I18n(120),
    "subtitle": I18n(200),
    "button_text": I18n(40),
    "image": Media(),
    "image_mobile": Media(),
    "link": Link(),
    "place": Enum(("hero", "inline", "sidebar", "popup")),
    "inline_after_row": Int(1, 50, nullable=True),
    "starts_at": _DateTime(),
    "ends_at": _DateTime(),
    "active": Bool(),
    "position": Int(0, 100000),
}


def clean_banner(data, company, partial=False, instance=None) -> dict:
    out = _clean(BANNER_SPEC, data, company, partial)
    starts = out.get("starts_at", getattr(instance, "starts_at", None))
    ends = out.get("ends_at", getattr(instance, "ends_at", None))
    if starts and ends and ends <= starts:
        _err("ends_at", "Конец показа должен быть позже начала.")
    return out


# ---------------------------------------------------------------- блоки акций

PROMO_SOURCE_TYPES = ("promotions", "products", "category", "new", "on_sale")


class _PromoSource(ds.Field):
    def check(self, value, path, ctx):
        if not isinstance(value, dict):
            _err(path, 'Ожидается {"type": ..., "ids": [...]}.')
        stype = value.get("type")
        if stype not in PROMO_SOURCE_TYPES:
            _err(f"{path}.type", f"Недопустимый источник. Допустимые: {', '.join(PROMO_SOURCE_TYPES)}.")
        for k in value:
            if k not in ("type", "ids", "id"):
                _err(f"{path}.{k}", "Неизвестное поле.", "unknown_field")
        ids = ListOf(UUIDItem(), max_items=200, unique=True).patch(None, value.get("ids") or [], f"{path}.ids", ctx)
        if value.get("id"):
            one = UUIDItem().check(value["id"], f"{path}.id", ctx)
            if one not in ids:
                ids.insert(0, one)
        if stype in ("products", "category") and not ids:
            _err(f"{path}.ids", "Укажите товары или категорию.", "required")
        return {"type": stype, "ids": ids}


PROMO_SPEC = {
    "title": I18n(120),
    "source": _PromoSource(),
    "style": Enum(("carousel", "grid", "hero_product")),
    "show_timer": Bool(),
    "max_items": Int(1, 48),
    "position": Int(0, 100000),
    "active": Bool(),
    "background": Color(nullable=True),
    "title_color": Color(nullable=True),
}


def clean_promo(data, company, partial=False) -> dict:
    return _clean(PROMO_SPEC, data, company, partial)


# ---------------------------------------------------------------- страницы

PAGE_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PAGE_BODY_MAX = 20000


class _Body(ds.Field):
    def check(self, value, path, ctx):
        if isinstance(value, str):
            value = {"ru": value}
        if not isinstance(value, dict):
            _err(path, 'Ожидается текст по языкам: {"ru": "...", "ky": "..."}.')
        out = {}
        for lang, txt in value.items():
            if lang not in ds.LANGUAGES:
                _err(f"{path}.{lang}", "Неизвестный язык.")
            txt = ds.clean_text(txt, f"{path}.{lang}", PAGE_BODY_MAX)
            out[lang] = sanitize_body(txt)
        return out


PAGE_SPEC = {
    "slug": ds.Text(50, regex=PAGE_SLUG_RE, regex_msg="Адрес страницы: латиница в нижнем регистре, цифры и дефис."),
    "title": I18n(120),
    "body": _Body(),
    "show_in_footer": Bool(),
    "position": Int(0, 100000),
}


def clean_page(data, company, partial=False) -> dict:
    return _clean(PAGE_SPEC, data, company, partial, required=("slug", "title"))


# ---------------------------------------------------------------- товары / категории

PRODUCT_SETTINGS_SPEC = {
    "hidden": Bool(),
    "pinned": Bool(),
    "badge": Enum(("hit", "sale", "new"), nullable=True),
    "sort_order": Int(1, 1000000, nullable=True),
}

CATEGORY_SETTINGS_SPEC = {
    "hidden": Bool(),
    "image": Media(),
    "title_override": I18n(80, nullable=True),
    "sort_order": Int(1, 1000000, nullable=True),
}


def clean_product_settings(data, company) -> dict:
    return _clean(PRODUCT_SETTINGS_SPEC, data, company, partial=True)


def clean_category_settings(data, company) -> dict:
    return _clean(CATEGORY_SETTINGS_SPEC, data, company, partial=True)
