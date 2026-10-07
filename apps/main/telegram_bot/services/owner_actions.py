"""
ТЗ ч.15, п. 4–6: действия ИИ в чате владельца — только после подтверждения.

Поток: ИИ (или разбор фото накладной) → `create_pending(...)` кладёт список изменений в кэш →
бот присылает список с кнопками «Выполнить»/«Отмена» → `execute_pending(...)` применяет изменения
в одной транзакции, остаток читается заново под блокировкой строки. В журнал товара (StockMovement)
пишется «Telegram-бот (ИИ), подтвердил владелец».
"""
import base64
import difflib
import html
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("telegram_bot.actions")

PENDING_TTL = 30 * 60
ZERO = Decimal("0")
Q2 = Decimal("0.01")
Q3 = Decimal("0.001")
AI_ACTOR_COMMENT = "Telegram-бот (ИИ), подтвердил владелец"

# Поля товара, которые ИИ может менять по подтверждению (п. 4: PATCH products/{id}/)
FIELD_LABELS = {
    "price": "цена продажи",
    "purchase_price": "цена закупки",
    "minimum_quantity": "минимальный остаток",
    "expiration_date": "срок годности",
    "shelf_life_days": "срок хранения (дней)",
    "description": "описание",
    "country": "страна",
    "brand": "бренд",
    "category": "категория",
    "barcode": "штрихкод",
    "article": "артикул",
}
MONEY_FIELDS = ("price", "purchase_price")
QTY_FIELDS = ("minimum_quantity",)
INT_FIELDS = ("shelf_life_days",)
DATE_FIELDS = ("expiration_date",)
TEXT_FIELDS = ("description", "country", "barcode", "article")
FK_FIELDS = ("brand", "category")

YES_WORDS = {"да", "выполнить", "выполняй", "подтверждаю", "подтвердить", "ок", "ok", "окей", "давай", "делай", "ооба", "макул", "yes"}
NO_WORDS = {"нет", "отмена", "отменить", "отмени", "не надо", "стоп", "жок", "no", "cancel"}


def _pending_key(company_id, chat_id) -> str:
    return f"tg_ai_pending:{company_id}:{chat_id}"


def get_pending(company_id, chat_id):
    return cache.get(_pending_key(company_id, chat_id))


def save_pending(company_id, chat_id, pending) -> None:
    cache.set(_pending_key(company_id, chat_id), pending, timeout=PENDING_TTL)


def clear_pending(company_id, chat_id) -> None:
    cache.delete(_pending_key(company_id, chat_id))


def is_yes(text: str) -> bool:
    return (text or "").strip().lower().strip("!. ") in YES_WORDS


def is_no(text: str) -> bool:
    return (text or "").strip().lower().strip("!. ") in NO_WORDS


def parse_markup_request(text: str):
    """«поставь 25 %», «наценка 30», «сделай 15%» → Decimal или None."""
    t = (text or "").lower()
    if not any(w in t for w in ("наценк", "постав", "сделай", "%", "процент", "устуна", "кой")):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:%|процент|проц)?", t)
    if not m:
        return None
    try:
        value = Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    if value < 0 or value > 1000:
        return None
    return value


# ---------------------------------------------------------------------------
# Числа и форматирование
# ---------------------------------------------------------------------------

def _dec(value, default=None):
    if value is None or value == "":
        return default
    try:
        d = Decimal(str(value).replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return default
    if not d.is_finite():
        return default
    return d


def _money(value) -> Decimal:
    return (_dec(value, ZERO) or ZERO).quantize(Q2, rounding=ROUND_HALF_UP)


def _qty(value) -> Decimal:
    return (_dec(value, ZERO) or ZERO).quantize(Q3, rounding=ROUND_HALF_UP)


def fmt_money(value) -> str:
    d = _money(value)
    whole, frac = f"{d:,.2f}".split(".")
    whole = whole.replace(",", " ")
    return f"{whole}.{frac}" if frac != "00" else whole


def fmt_qty(value) -> str:
    d = _qty(value)
    s = f"{d:f}".rstrip("0").rstrip(".") if "." in f"{d:f}" else f"{d:f}"
    return s or "0"


def _calc_price(purchase: Decimal, markup: Decimal) -> Decimal:
    return (purchase * (Decimal("1") + markup / Decimal("100"))).quantize(Q2, rounding=ROUND_HALF_UP)


def _calc_markup(purchase: Decimal, price: Decimal) -> Decimal:
    if purchase <= 0:
        return ZERO
    return ((price - purchase) / purchase * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^\+?(\d{1,4})\s*(д|дн|день|дня|дней|мес|месяц|месяца|месяцев)", s.lower())
    if m:
        n = int(m.group(1))
        days = n * 30 if m.group(2).startswith("мес") else n
        return timezone.localdate() + timedelta(days=days)
    return None


# ---------------------------------------------------------------------------
# Поиск товаров
# ---------------------------------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ң": "n", "ө": "o", "ү": "u",
}


def translit(text: str) -> str:
    """Кириллица → латиница для сравнения названий («Марс» == «Mars», «Сникерс» ≈ «Snickers»)."""
    out = "".join(_TRANSLIT.get(ch, ch) for ch in (text or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", out).strip()


def _name_similarity(query: str, name: str) -> float:
    ql, nl = query.lower(), name.lower()
    direct = difflib.SequenceMatcher(None, ql, nl).ratio()
    qt, nt = translit(ql), translit(nl)
    lat = difflib.SequenceMatcher(None, qt, nt).ratio()
    q_words = [w for w in qt.split() if len(w) >= 3]
    n_words = nt.split()
    # все значимые слова запроса встречаются в названии (с поправкой на окончания: «сникерса» → «snickers»)
    hit = q_words and all(any(nw.startswith(qw[:4]) or qw.startswith(nw[:4]) for nw in n_words) for qw in q_words)
    return max(direct, lat, 0.8 if hit else 0.0)


def _product_qs(company):
    from apps.main.models import Product

    return Product.objects.filter(company=company).exclude(status=Product.Status.ARCHIVED)


def find_product(company, query: str, barcode: str = None):
    """Товар по штрихкоду, точному имени, вхождению или похожести названия (difflib ≥ 0.72)."""
    from apps.main.models import ProductAlternateBarcode

    qs = _product_qs(company)
    bc = re.sub(r"\D", "", str(barcode or ""))
    if bc:
        p = qs.filter(barcode=bc).first()
        if p:
            return p
        alt = ProductAlternateBarcode.objects.filter(company=company, barcode=bc).select_related("product").first()
        if alt and alt.product and alt.product.status != "archived":
            return alt.product
    q = (query or "").strip()
    if not q:
        return None
    if re.fullmatch(r"\d{6,}", q):
        p = qs.filter(barcode=q).first()
        if p:
            return p
    p = qs.filter(name__iexact=q).first()
    if p:
        return p
    cands = list(qs.filter(name__icontains=q).order_by("name")[:20])
    if len(cands) == 1:
        return cands[0]
    if cands:
        # самое короткое название с вхождением — обычно искомое («Марс» → «Mars 50г», а не «Mars King Size»)
        return sorted(cands, key=lambda x: len(x.name))[0]
    # нечёткое совпадение по словам, в т.ч. кириллица ↔ латиница («Марс» → «Mars 50г»)
    ql = q.lower()
    words = [w for w in re.split(r"[^\wа-яёңөү]+", ql) if len(w) >= 3]
    pool = qs
    if words:
        from django.db.models import Q as DQ

        cond = DQ()
        for w in words[:3]:
            cond |= DQ(name__icontains=w[:4])
            lat = translit(w)
            if lat and lat != w:
                cond |= DQ(name__icontains=lat[:4])
        pool = qs.filter(cond)
    if not pool.exists():
        pool = qs
    best, best_ratio = None, 0.0
    for p in pool.only("id", "name").order_by("name")[:500]:
        r = _name_similarity(ql, p.name)
        if r > best_ratio or (r == best_ratio and best is not None and len(p.name) < len(best.name)):
            best, best_ratio = p, r
    return best if best_ratio >= 0.72 else None


# ---------------------------------------------------------------------------
# Список изменений → ожидание подтверждения
# ---------------------------------------------------------------------------

def normalize_changes(company, raw_changes: list, min_markup: Decimal = None):
    """
    Превращает запрос ИИ в проверенный список изменений.
    Возвращает (changes, not_found, errors). Каждое изменение:
      {"op": "stock_add"|"stock_set"|"stock_writeoff"|"set_field"|"create_product", ...}
    """
    changes, not_found, errors = [], [], []
    for idx, raw in enumerate(raw_changes or []):
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or raw.get("op") or "").strip().lower()
        name = str(raw.get("product") or raw.get("name") or "").strip()
        barcode = str(raw.get("barcode") or "").strip() or None
        reason = str(raw.get("reason") or "").strip()[:200]

        if action in ("create", "create_product", "new"):
            qty = _qty(raw.get("qty") or raw.get("quantity") or 0)
            purchase = _money(raw.get("purchase_price") or 0)
            price = raw.get("price")
            price = _money(price) if price not in (None, "") else None
            if not name:
                errors.append(f"Позиция {idx + 1}: у нового товара нет названия.")
                continue
            existing = find_product(company, name, barcode)
            if existing and (barcode and existing.barcode == barcode or existing.name.lower() == name.lower()):
                # уже есть — это приход, а не создание
                changes.append({
                    "op": "stock_add", "product_id": str(existing.id), "name": existing.name,
                    "qty": str(qty), "purchase_price": str(purchase) if purchase > 0 else None,
                    "price": str(price) if price is not None else None, "reason": reason,
                })
                continue
            if price is None and purchase > 0:
                price = _calc_price(purchase, min_markup if min_markup is not None else Decimal("20"))
            changes.append({
                "op": "create_product", "name": name[:255], "barcode": barcode, "qty": str(qty),
                "purchase_price": str(purchase), "price": str(price if price is not None else ZERO), "reason": reason,
            })
            continue

        product = find_product(company, name, barcode) if (name or barcode) else None
        if product is None:
            not_found.append(name or barcode or f"позиция {idx + 1}")
            continue

        if action in ("add", "income", "receipt", "stock_add", "приход"):
            qty = _qty(raw.get("qty") or raw.get("quantity") or 0)
            if qty <= 0:
                errors.append(f"«{product.name}»: укажите количество прихода.")
                continue
            purchase = raw.get("purchase_price")
            price = raw.get("price")
            changes.append({
                "op": "stock_add", "product_id": str(product.id), "name": product.name, "qty": str(qty),
                "purchase_price": str(_money(purchase)) if purchase not in (None, "") else None,
                "price": str(_money(price)) if price not in (None, "") else None, "reason": reason,
            })
        elif action in ("writeoff", "write_off", "stock_writeoff", "defect", "списание", "брак"):
            qty = _qty(raw.get("qty") or raw.get("quantity") or 0)
            if qty <= 0:
                errors.append(f"«{product.name}»: укажите количество списания.")
                continue
            changes.append({
                "op": "stock_writeoff", "product_id": str(product.id), "name": product.name, "qty": str(qty),
                "reason": reason or "списание",
            })
        elif action in ("set", "set_stock", "stock_set", "inventory", "остаток"):
            qty = _dec(raw.get("qty") if raw.get("qty") not in (None, "") else raw.get("quantity"))
            if qty is None or qty < 0:
                errors.append(f"«{product.name}»: укажите новый остаток.")
                continue
            changes.append({
                "op": "stock_set", "product_id": str(product.id), "name": product.name, "qty": str(_qty(qty)),
                "reason": reason or "ревизия",
            })
        elif action in ("update", "set_field", "field", "изменить"):
            field = str(raw.get("field") or "").strip().lower()
            value = raw.get("value")
            if field not in FIELD_LABELS:
                errors.append(f"«{product.name}»: поле «{field}» менять нельзя.")
                continue
            ok, norm_value, err = _normalize_field_value(field, value)
            if not ok:
                errors.append(f"«{product.name}»: {err}")
                continue
            changes.append({
                "op": "set_field", "product_id": str(product.id), "name": product.name,
                "field": field, "value": norm_value, "reason": reason,
            })
        else:
            errors.append(f"Позиция {idx + 1}: неизвестное действие «{action}».")
    return changes, not_found, errors


def _normalize_field_value(field: str, value):
    """→ (ok, строковое значение для кэша, ошибка)."""
    if field in MONEY_FIELDS:
        d = _dec(value)
        if d is None or d < 0:
            return False, None, f"{FIELD_LABELS[field]}: нужно число ≥ 0."
        return True, str(_money(d)), None
    if field in QTY_FIELDS:
        d = _dec(value)
        if d is None or d < 0:
            return False, None, f"{FIELD_LABELS[field]}: нужно число ≥ 0."
        return True, str(_qty(d)), None
    if field in INT_FIELDS:
        d = _dec(value)
        if d is None or d < 0:
            return False, None, f"{FIELD_LABELS[field]}: нужно целое число дней."
        return True, str(int(d)), None
    if field in DATE_FIELDS:
        if value in (None, "", "null", "нет", "—"):
            return True, "", None
        dt = _parse_date(value)
        if dt is None:
            return False, None, "срок годности: дата в формате ДД.ММ.ГГГГ."
        return True, dt.isoformat(), None
    if field in TEXT_FIELDS:
        s = str(value or "").strip()
        if field == "barcode":
            s = re.sub(r"\s", "", s)
            if s and not re.fullmatch(r"[0-9A-Za-z\-]{3,64}", s):
                return False, None, "штрихкод: только цифры и буквы, 3–64 символа."
        return True, s[:2000 if field == "description" else 255], None
    if field in FK_FIELDS:
        s = str(value or "").strip()
        return True, s[:120], None
    return False, None, "неизвестное поле"


def create_pending(company, chat_id, changes: list, *, kind: str = "product_changes",
                   markup: Decimal = None, unparsed: list = None, source: str = "ai") -> dict:
    pending = {
        "id": uuid.uuid4().hex[:10],
        "kind": kind,
        "changes": changes,
        "unparsed": unparsed or [],
        "markup": str(markup) if markup is not None else None,
        "source": source,
        "announced": False,
        "created_at": timezone.now().isoformat(),
    }
    save_pending(company.id, chat_id, pending)
    return pending


def describe_change(ch: dict) -> str:
    op = ch.get("op")
    name = html.escape(ch.get("name") or "")
    if op == "stock_add":
        extra = []
        if ch.get("purchase_price"):
            extra.append(f"закупка {fmt_money(ch['purchase_price'])}")
        if ch.get("price"):
            extra.append(f"цена {fmt_money(ch['price'])}")
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"📥 Приход: {name} +{fmt_qty(ch['qty'])} шт{tail}"
    if op == "stock_writeoff":
        reason = f" — {html.escape(ch['reason'])}" if ch.get("reason") else ""
        return f"📤 Списание: {name} −{fmt_qty(ch['qty'])} шт{reason}"
    if op == "stock_set":
        return f"📋 Остаток: {name} → {fmt_qty(ch['qty'])} шт"
    if op == "set_field":
        field = ch.get("field")
        value = ch.get("value")
        if field in MONEY_FIELDS:
            shown = f"{fmt_money(value)} сом"
        elif field in DATE_FIELDS:
            shown = datetime.fromisoformat(value).strftime("%d.%m.%Y") if value else "убрать"
        elif field == "description":
            shown = html.escape(str(value)[:80]) + ("…" if len(str(value)) > 80 else "")
        else:
            shown = html.escape(str(value)) or "—"
        return f"✏️ {name}: {FIELD_LABELS.get(field, field)} → {shown}"
    if op == "create_product":
        bc = f", ШК {html.escape(ch['barcode'])}" if ch.get("barcode") else ""
        return (
            f"🆕 Новый товар: {name}{bc} — {fmt_qty(ch['qty'])} шт, "
            f"закупка {fmt_money(ch['purchase_price'])}, цена {fmt_money(ch['price'])}"
        )
    return html.escape(str(ch))


def build_confirmation_message(pending: dict) -> tuple:
    """(HTML-текст, inline-клавиатура) для сообщения с кнопками «Выполнить»/«Отмена»."""
    lines = []
    if pending.get("kind") == "invoice":
        markup = pending.get("markup")
        lines.append(f"📄 <b>Накладная</b> — наценка {fmt_money(markup) if markup is not None else '—'} %:")
    else:
        lines.append("<b>Изменения товаров:</b>")
    for ch in pending.get("changes") or []:
        lines.append("• " + describe_change(ch))
    if pending.get("unparsed"):
        lines.append("")
        lines.append("<b>Не разобрал (проверьте вручную):</b>")
        for u in pending["unparsed"][:15]:
            lines.append("• " + html.escape(str(u)))
    if pending.get("kind") == "invoice":
        lines.append("")
        lines.append("Чтобы изменить наценку, напишите, например: «поставь 25 %».")
    lines.append("")
    lines.append("Выполнить? Ответьте «да»/«нет» или нажмите кнопку.")
    pid = pending["id"]
    markup_kb = {
        "inline_keyboard": [[
            {"text": "✅ Выполнить", "callback_data": f"aiact:run:{pid}"},
            {"text": "❌ Отмена", "callback_data": f"aiact:cancel:{pid}"},
        ]]
    }
    return "\n".join(lines), markup_kb


def apply_markup_to_pending(pending: dict, markup: Decimal) -> dict:
    """Пересчёт цен в накладной под новую наценку (п. 5.4)."""
    pending["markup"] = str(markup)
    for ch in pending.get("changes") or []:
        purchase = _dec(ch.get("purchase_price"))
        if purchase is None or purchase <= 0:
            continue
        if ch.get("op") in ("create_product", "stock_add"):
            ch["price"] = str(_calc_price(purchase, markup))
    pending["id"] = uuid.uuid4().hex[:10]
    pending["announced"] = False
    return pending


# ---------------------------------------------------------------------------
# Выполнение
# ---------------------------------------------------------------------------

def execute_pending(company, chat_id, pending: dict, *, user=None) -> dict:
    """
    Применяет подтверждённые изменения. Возвращает {"done": [...], "failed": [...]}.
    Каждое изменение — своя транзакция: одно неудачное не откатывает остальные (п. 4.4).
    """
    from apps.main.models import Product

    actor = user or getattr(company, "owner", None)
    done, failed = [], []
    for ch in pending.get("changes") or []:
        try:
            with transaction.atomic():
                if ch["op"] == "create_product":
                    msg = _create_product(company, ch, actor)
                else:
                    product = Product.objects.select_for_update().filter(pk=ch["product_id"], company=company).first()
                    if product is None:
                        raise ValueError("товар не найден")
                    if ch["op"] in ("stock_add", "stock_writeoff", "stock_set"):
                        msg = _apply_stock(company, product, ch, actor)
                    elif ch["op"] == "set_field":
                        msg = _apply_field(company, product, ch, actor)
                    else:
                        raise ValueError(f"неизвестная операция {ch['op']}")
            done.append(msg)
        except Exception as exc:  # noqa: BLE001 — результат по каждой строке отдаём владельцу
            logger.warning("AI action failed for %s: %s", ch, exc)
            failed.append(f"{html.escape(ch.get('name') or '')}: {html.escape(str(exc))[:200]}")
    clear_pending(company.id, chat_id)
    _audit(company, actor, pending, done, failed)
    return {"done": done, "failed": failed}


def _movement(company, product, mtype, before, change, after, actor, comment, reason=""):
    from apps.main.models import record_stock_movement

    record_stock_movement(
        company=company, branch=product.branch, type=mtype, object_id=product.id, product_name=product.name,
        warehouse="finished_goods", qty_before=before, change=change, qty_after=after, created_by=actor,
        comment=(f"{AI_ACTOR_COMMENT}. {reason}".strip() if reason else AI_ACTOR_COMMENT)[:1000],
        ref_type="telegram_ai", source_name="Telegram-бот (ИИ)",
    )


def _apply_stock(company, product, ch, actor) -> str:
    from apps.main.models import StockMovement

    before = _qty(product.quantity or 0)
    qty = _qty(ch["qty"])
    fields = ["quantity", "updated_at"]
    if ch["op"] == "stock_add":
        after = before + qty
        mtype = StockMovement.Type.INCOME
        purchase = _dec(ch.get("purchase_price"))
        price = _dec(ch.get("price"))
        if purchase is not None and purchase > 0:
            product.purchase_price = _money(purchase)
            fields.append("purchase_price")
        if price is not None and price > 0:
            product.price = _money(price)
            product.markup_percent = _calc_markup(_money(product.purchase_price or 0), product.price)
            product._manual_price = True
            fields += ["price", "markup_percent"]
        elif purchase is not None and purchase > 0:
            # цена продажи остаётся, наценка пересчитывается от новой закупки
            product.markup_percent = _calc_markup(product.purchase_price, _money(product.price or 0))
            product._manual_price = True
            fields.append("markup_percent")
        label = f"Приход {html.escape(product.name)}: +{fmt_qty(qty)} → {fmt_qty(after)} шт"
    elif ch["op"] == "stock_writeoff":
        if before - qty < 0:
            raise ValueError(f"остаток {fmt_qty(before)} шт, списать {fmt_qty(qty)} нельзя")
        after = before - qty
        mtype = StockMovement.Type.WRITEOFF
        label = f"Списание {html.escape(product.name)}: −{fmt_qty(qty)} → {fmt_qty(after)} шт"
    else:
        after = qty
        mtype = StockMovement.Type.INVENTORY
        label = f"Остаток {html.escape(product.name)}: {fmt_qty(before)} → {fmt_qty(after)} шт"
    product.quantity = after
    if not getattr(product, "_manual_price", False):
        product._manual_price = True  # не пересчитывать цену при сохранении остатка
    product.save(update_fields=fields)
    if after != before:
        _movement(company, product, mtype, before, after - before, after, actor, AI_ACTOR_COMMENT, ch.get("reason") or "")
    return label


def _apply_field(company, product, ch, actor) -> str:
    from apps.main.models import ProductBrand, ProductCategory

    field, value = ch["field"], ch.get("value")
    fields = [field, "updated_at"]
    if field == "price":
        product.price = _money(value)
        product.markup_percent = _calc_markup(_money(product.purchase_price or 0), product.price)
        product._manual_price = True
        fields.append("markup_percent")
    elif field == "purchase_price":
        product.purchase_price = _money(value)
        product.price = _calc_price(product.purchase_price, _dec(product.markup_percent, ZERO) or ZERO)
        product._manual_price = True
        fields.append("price")
    elif field in QTY_FIELDS:
        setattr(product, field, _qty(value))
        product._manual_price = True
    elif field in INT_FIELDS:
        setattr(product, field, int(value))
        product._manual_price = True
    elif field in DATE_FIELDS:
        setattr(product, field, date.fromisoformat(value) if value else None)
        product._manual_price = True
    elif field == "brand":
        product.brand = ProductBrand.objects.get_or_create(company=company, name=value)[0] if value else None
        product._manual_price = True
    elif field == "category":
        product.category = ProductCategory.objects.get_or_create(company=company, name=value)[0] if value else None
        product._manual_price = True
    else:
        setattr(product, field, value or ("" if field != "barcode" else None))
        product._manual_price = True
    product.save(update_fields=fields)
    shown = describe_change(ch).split(": ", 1)[-1]
    return f"✏️ {html.escape(product.name)}: {shown}"


def _create_product(company, ch, actor) -> str:
    from apps.main.models import Product, StockMovement

    purchase = _money(ch.get("purchase_price") or 0)
    price = _money(ch.get("price") or 0)
    qty = _qty(ch.get("qty") or 0)
    barcode = ch.get("barcode") or None
    if barcode and _product_qs(company).filter(barcode=barcode).exists():
        raise ValueError(f"штрихкод {barcode} уже есть у другого товара")
    product = Product(
        company=company, branch=None, name=ch["name"], barcode=barcode,
        purchase_price=purchase, markup_percent=_calc_markup(purchase, price), price=price,
        quantity=qty, date=timezone.now(), created_by=actor,
    )
    product._manual_price = True
    product.save()
    if qty > 0:
        _movement(company, product, StockMovement.Type.INCOME, ZERO, qty, qty, actor, AI_ACTOR_COMMENT,
                  ch.get("reason") or "накладная")
    return f"🆕 {html.escape(product.name)}: создан, {fmt_qty(qty)} шт, цена {fmt_money(price)} сом"


def _audit(company, actor, pending, done, failed):
    try:
        from apps.main.telegram_bot.models import TelegramBotAudit

        TelegramBotAudit.objects.create(
            company=company, user=actor if getattr(actor, "pk", None) else None,
            user_name=getattr(actor, "email", "") or "owner",
            action=TelegramBotAudit.Action.AI_ADVICE_APPLY,
            object_title=f"ИИ-бот: {pending.get('kind')}",
            source="ai_advisor",
            changes={"done": done, "failed": failed, "changes": pending.get("changes")},
        )
    except Exception as exc:  # журнал не должен ломать выполнение
        logger.debug("audit skipped: %s", exc)


def build_result_message(result: dict) -> str:
    lines = []
    if result["done"]:
        lines.append("✅ <b>Выполнено:</b>")
        lines += [f"• {d}" for d in result["done"]]
    if result["failed"]:
        lines.append("")
        lines.append("⚠️ <b>Не получилось:</b>")
        lines += [f"• {f}" for f in result["failed"]]
    if not lines:
        lines.append("Изменений не было.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Накладная по фото (п. 5)
# ---------------------------------------------------------------------------

INVOICE_SYSTEM = (
    "Ты распознаёшь товарные накладные, чеки поставщиков и прайс-листы по фото для магазина в Кыргызстане. "
    "Верни строго JSON вида "
    '{"rows":[{"name":"...","qty":1,"unit_price":0,"line_total":0,"barcode":null}],"unparsed":["..."],"supplier":null,"date":null}. '
    "name — название товара как в документе (без артикулов); qty — количество (число); unit_price — закупочная цена за единицу; "
    "если указана только сумма строки — положи её в line_total и unit_price=null. Строки, которые невозможно разобрать "
    "(нечитаемые, без количества и цены), помести в unparsed текстом как есть. Не выдумывай ничего, чего нет на фото. "
    "Итоговые строки («Итого», «Всего», «НДС») в rows не включай."
)


def parse_invoice_photo(api_key: str, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    from apps.main.telegram_bot.services import ai_service

    parts = [
        {"text": "Распознай все строки документа на фото и верни JSON."},
        {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": base64.b64encode(image_bytes).decode("ascii")}},
    ]
    data = ai_service.generate_json(api_key, parts, system_instruction=INVOICE_SYSTEM, max_tokens=4000)
    if not isinstance(data, dict):
        return {"rows": [], "unparsed": [], "error": "не удалось прочитать документ"}
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    unparsed = [str(u) for u in (data.get("unparsed") or []) if str(u).strip()]
    return {"rows": rows, "unparsed": unparsed, "supplier": data.get("supplier"), "date": data.get("date")}


def build_invoice_pending(company, chat_id, parsed: dict, *, min_markup: Decimal) -> dict:
    """Строки накладной → изменения: знакомые товары — приход, новые — создание (п. 5.3–5.4)."""
    changes, unparsed = [], list(parsed.get("unparsed") or [])
    for raw in parsed.get("rows") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        qty = _dec(raw.get("qty"))
        unit = _dec(raw.get("unit_price"))
        total = _dec(raw.get("line_total"))
        if unit is None and total is not None and qty:
            unit = total / qty
        if not name or qty is None or qty <= 0 or unit is None or unit < 0:
            unparsed.append(" | ".join(str(v) for v in (raw.get("name"), raw.get("qty"), raw.get("unit_price"), raw.get("line_total")) if v not in (None, "")))
            continue
        qty, unit = _qty(qty), _money(unit)
        barcode = re.sub(r"\D", "", str(raw.get("barcode") or "")) or None
        product = find_product(company, name, barcode)
        if product is not None:
            markup = max(_dec(product.markup_percent, ZERO) or ZERO, min_markup)
            changes.append({
                "op": "stock_add", "product_id": str(product.id), "name": product.name, "qty": str(qty),
                "purchase_price": str(unit), "price": str(_calc_price(unit, markup)), "reason": "накладная",
                "invoice_name": name,
            })
        else:
            changes.append({
                "op": "create_product", "name": name[:255], "barcode": barcode, "qty": str(qty),
                "purchase_price": str(unit), "price": str(_calc_price(unit, min_markup)), "reason": "накладная",
            })
    return create_pending(company, chat_id, changes, kind="invoice", markup=min_markup, unparsed=unparsed, source="invoice_photo")


# ---------------------------------------------------------------------------
# Напоминания должникам через WhatsApp (п. 6)
# ---------------------------------------------------------------------------

def normalize_kg_phone(phone: str) -> str:
    """«0700 123 456» → «996700123456»; пусто — если номер не похож на телефон."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    if digits.startswith("996") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "996" + digits[1:]
    if len(digits) == 9:
        return "996" + digits
    if len(digits) >= 11:
        return digits
    return ""


def build_debt_reminders_message(company, names: list = None, limit: int = 30) -> str:
    """Список должников со ссылками wa.me и готовым текстом напоминания."""
    from urllib.parse import quote

    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_debtors

    data = fn_get_debtors(company, limit=limit)
    debtors = data.get("debtors") or []
    if names:
        wanted = [n.lower() for n in names if n]
        debtors = [d for d in debtors if any(w in (d.get("name") or "").lower() for w in wanted)]
    if not debtors:
        return "Должников не найдено." if names else "Должников нет — все расчёты закрыты. 🎉"
    lines = ["🧾 <b>Напоминания должникам</b> — нажмите ссылку, WhatsApp откроется с готовым текстом:", ""]
    total = ZERO
    for d in debtors:
        amount = _money(d.get("debt_amount"))
        total += amount
        name = d.get("name") or "Без имени"
        phone = normalize_kg_phone(d.get("phone"))
        since = d.get("oldest_debt_date")
        since_txt = f", с {datetime.fromisoformat(since).strftime('%d.%m')}" if since else ""
        if phone:
            text = (
                f"Здравствуйте, {name}! Напоминаем о долге в нашем магазине: {fmt_money(amount)} сом. "
                "Пожалуйста, верните долг. Спасибо!"
            )
            link = f"https://wa.me/{phone}?text={quote(text)}"
            lines.append(f"• {html.escape(name)} — {fmt_money(amount)} сом{since_txt} — <a href=\"{link}\">написать в WhatsApp</a>")
        else:
            lines.append(f"• {html.escape(name)} — {fmt_money(amount)} сом{since_txt} — нет телефона")
    lines.append("")
    lines.append(f"<b>Итого:</b> {fmt_money(total)} сом, должников: {len(debtors)}")
    return "\n".join(lines)


def extract_debtor_names(text: str) -> list:
    """«напомни должникам Айбеку и Асель» → ["айбеку", "асель"]; пусто — всем."""
    t = (text or "").lower()
    t = re.sub(r".*?(должник\w*|карыз\w*)", "", t, count=1).strip(" :,.")
    if not t or t in ("всем", "все", "баарына"):
        return []
    parts = [p.strip() for p in re.split(r"[,;]| и | жана ", t) if p.strip()]
    names = []
    for p in parts:
        p = re.sub(r"\b(о долге|про долг|насчёт долга)\b", "", p).strip()
        if len(p) >= 3:
            names.append(p[:40])
    return names
