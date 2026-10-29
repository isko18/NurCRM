import logging
import re
from decimal import Decimal
from typing import List, Optional
from apps.main.telegram_bot.services import telegram_api

logger = logging.getLogger("telegram_bot.photo")


def format_amount(val) -> str:
    """Сумма без лишних нулей и без «сом»: 660 066, 45,50 (ТЗ ч.9, 1.1)."""
    return format_price_display(val)[: -len(" сом")]


def format_qty(val) -> str:
    """Количество без хвостовых нулей: 6, 140, 1,5, 0,25 (ТЗ ч.9, 1.1)."""
    if val is None:
        return "0"
    try:
        d = Decimal(str(val))
    except Exception:
        return str(val)
    if d == d.to_integral():
        return f"{int(d):,}".replace(",", " ")
    s = format(d.normalize(), "f")
    whole, _, frac = s.partition(".")
    return f"{int(whole):,}".replace(",", " ") + "," + frac


def format_price_display(val) -> str:
    """Форматирует цену без лишних нулей: 120 сом, 1 900 сом, 45,50 сом."""
    if val is None:
        return "0 сом"
    try:
        d = Decimal(str(val))
        if d == d.to_integral():
            return f"{int(d):,}".replace(",", " ") + " сом"
        s = f"{d:,.2f}".replace(",", " ")
        if s.endswith(".00"):
            return s[:-3] + " сом"
        return s.replace(".", ",") + " сом"
    except Exception:
        return f"{val} сом"


def extract_base_product_name(name: str) -> str:
    """Удаляет из названия единицы объёма/веса/тары (напр. 'Coca-Cola 1,5 л' -> 'Coca-Cola')."""
    if not name:
        return ""
    # Отрезаем хвостики вида '1.5 л', '500 мл', '1 кг', '250г', 'шт.'
    cleaned = re.sub(
        r"(?:[\s,]+(?:[\d]+(?:[.,]\d+)?\s*(?:л|мл|кг|г|шт|m|l|kg|g|бут|уп|пач)\.?|оверсайз|\d+/\d+).*)$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned if len(cleaned) >= 2 else name.strip()


def find_mentioned_products(text: str, candidate_products: list, is_owner: bool = False) -> list:
    """
    Находит в тексте ответа названия товаров из каталога.
    Сортирует по порядку их упоминания в тексте.
    Ограничивает 5 товарами, у которых есть фото и которые есть в наличии (для покупателя).
    """
    if not text or not candidate_products:
        return []

    norm_text = text.lower()
    matches = []

    for prod in candidate_products:
        # Проверяем наличие фото
        image_url = getattr(prod, "image_url", None)
        file_id = getattr(prod, "telegram_photo_file_id", None)
        if not (image_url or file_id):
            continue

        # Покупателю не показываем товары не в наличии
        if not is_owner:
            qty = getattr(prod, "quantity", 0) or 0
            # Если есть варианты, проверяем есть ли хоть один с остатком
            variants = getattr(prod, "_prefetched_objects_cache", {}).get("variants")
            if variants is not None:
                has_stock = any((v.quantity or 0) > 0 for v in variants if getattr(v, "is_active", True))
            else:
                has_stock = qty > 0 or getattr(prod, "kind", "") == "service"
            if not has_stock:
                continue

        p_name = (getattr(prod, "name", "") or "").strip().lower()
        if not p_name:
            continue

        base_name = extract_base_product_name(p_name).lower()

        # Ищем вхождение полного названия или базового названия
        idx = norm_text.find(p_name)
        if idx == -1 and base_name and len(base_name) >= 3:
            idx = norm_text.find(base_name)

        if idx != -1:
            matches.append((idx, prod))

    # Сортируем по индексу появления в тексте
    matches.sort(key=lambda x: x[0])

    # Убираем дубликаты
    seen_ids = set()
    result = []
    for _, p in matches:
        if p.id not in seen_ids:
            seen_ids.add(p.id)
            result.append(p)
            if len(result) >= 5:
                break
    return result


def send_single_product_photo(settings, chat_id: str, product, reply_markup: dict = None) -> bool:
    """Отправляет фото одного товара с подписью и сохраняет file_id."""
    token = settings.token
    if not token or not chat_id or not product:
        return False

    price_str = format_price_display(product.price)
    caption = f"<b>{product.name}</b>\n💰 {price_str} · ✅ в наличии"

    file_id = getattr(product, "telegram_photo_file_id", None)
    photo_payload = None

    if file_id:
        photo_payload = file_id
    elif product.image_url:
        prepared = telegram_api.prepare_photo_for_telegram(product.image_url)
        photo_payload = prepared if prepared is not None else product.image_url

    if not photo_payload:
        return False

    try:
        res = telegram_api.send_photo(
            token=token,
            chat_id=chat_id,
            photo=photo_payload,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        if res.get("ok"):
            photos = res.get("result", {}).get("photo", [])
            if photos:
                new_file_id = photos[-1].get("file_id")
                if new_file_id and new_file_id != file_id:
                    product.telegram_photo_file_id = new_file_id
                    product.save(update_fields=["telegram_photo_file_id"])
            return True
        else:
            logger.warning("Failed to send product photo for %s: %s", product.id, res)
    except Exception as exc:
        logger.warning("Exception sending product photo for %s: %s", product.id, exc)
    return False


def send_product_photos_for_text(settings, chat_id: str, text: str, candidate_products: list, is_owner: bool = False) -> None:
    """
    Отправляет фотографии товаров, упомянутых в тексте ответа:
    - 1 товар -> sendPhoto с подписью
    - 2-5 товаров -> sendMediaGroup (альбом)
    """
    if not getattr(settings, "send_product_photos", True):
        return

    try:
        mentioned = find_mentioned_products(text, candidate_products, is_owner=is_owner)
        if not mentioned:
            return

        token = settings.token
        if not token:
            return

        if len(mentioned) == 1:
            send_single_product_photo(settings, chat_id, mentioned[0])
            return

        # 2-5 товаров -> альбом sendMediaGroup
        media_group = []
        products_in_album = []
        for prod in mentioned[:5]:
            price_str = format_price_display(prod.price)
            caption = f"<b>{prod.name}</b>\n💰 {price_str} · ✅ в наличии"
            media_item = {
                "type": "photo",
                "caption": caption,
                "parse_mode": "HTML",
            }
            file_id = getattr(prod, "telegram_photo_file_id", None)
            if file_id:
                media_item["media"] = file_id
                media_group.append(media_item)
                products_in_album.append((prod, False))
            elif prod.image_url:
                media_item["media"] = prod.image_url
                media_group.append(media_item)
                products_in_album.append((prod, True))

        if len(media_group) >= 2:
            res = telegram_api.send_media_group(token, chat_id, media_group)
            if res.get("ok"):
                results = res.get("result", [])
                for idx, (p, needed_cache) in enumerate(products_in_album):
                    if idx < len(results):
                        photos = results[idx].get("photo", [])
                        if photos:
                            fid = photos[-1].get("file_id")
                            if fid and fid != getattr(p, "telegram_photo_file_id", None):
                                p.telegram_photo_file_id = fid
                                p.save(update_fields=["telegram_photo_file_id"])
            else:
                logger.warning("sendMediaGroup failed: %s", res)
        elif len(media_group) == 1:
            send_single_product_photo(settings, chat_id, products_in_album[0][0])

    except Exception as exc:
        logger.warning("send_product_photos_for_text error: %s", exc)
