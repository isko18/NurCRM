import logging
from django.db import transaction, models
from apps.consalting.models import (
    RegionalFunnelRoutingConsalting, RegionalFunnelRuleConsalting,
    FunnelConsalting, FunnelStageConsalting
)
from apps.users.models import User

logger = logging.getLogger("nurcrm.consalting.regional_routing")

REGION_LABELS = {
    "bishkek": "Бишкек",
    "osh": "Ош",
    "jalal_abad": "Джалал-Абад",
    "batken": "Баткен",
    "chuy": "Чуй",
    "issyk_kul": "Иссык-Куль",
    "naryn": "Нарын",
    "talas": "Талас",
    "other": "Другой",
}


def normalize_phone(phone: str) -> str:
    """Оставляет только цифры и лидирующий плюс."""
    if not phone:
        return ""
    phone = str(phone).strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if phone.startswith("+"):
        return f"+{digits}"
    return digits


def assign_owner_within_region(company, rule):
    """Pick an active recipient only from the rule's own region.

    ``assign_role_ids`` narrows the pool by role, while
    ``consulting_region_codes`` is the mandatory boundary between regional
    funnels.  In particular, never fall back to a same-role employee from a
    different region when this pool is empty.

    ``rule`` must be locked by the caller when its round-robin cursor is used.
    """
    region_code = str(rule.region_code or "").strip().lower()
    if not region_code:
        logger.warning(
            "regional.assign_owner.empty_pool",
            extra={"company_id": str(company.id), "rule_id": str(rule.id), "region": region_code},
        )
        return None

    role_ids = [str(value) for value in rule.assign_role_ids if value]
    if not role_ids:
        return None

    import uuid as _uuid

    uuid_roles = []
    named_roles = []
    for role_id in role_ids:
        try:
            _uuid.UUID(role_id)
            uuid_roles.append(role_id)
        except (ValueError, TypeError):
            named_roles.append(role_id)

    role_filter = models.Q()
    if uuid_roles:
        role_filter |= models.Q(custom_role_id__in=uuid_roles)
    if named_roles:
        role_filter |= models.Q(role__in=named_roles)

    # Filter in Python after the compact company/role query so this remains
    # portable for JSONField in both PostgreSQL production and SQLite tests.
    # get_consulting_region_codes() also normalizes legacy values consistently.
    candidates = [
        user for user in User.objects.filter(
            company=company,
            is_active=True,
        ).filter(role_filter).order_by("email", "id")
        if region_code in user.get_consulting_region_codes()
    ]

    if not candidates:
        logger.warning(
            "regional.assign_owner.empty_pool",
            extra={"company_id": str(company.id), "rule_id": str(rule.id), "region": region_code},
        )
        return None

    if rule.assign_strategy == RegionalFunnelRuleConsalting.AssignStrategy.ROUND_ROBIN:
        chosen = candidates[rule._rr_cursor % len(candidates)]
    elif rule.assign_strategy == RegionalFunnelRuleConsalting.AssignStrategy.LEAST_LOADED:
        from apps.consalting.models import LeadConsalting

        counts = (
            LeadConsalting.objects.filter(
                company=company,
                owner__in=candidates,
                status__in=[LeadConsalting.Status.NEW, LeadConsalting.Status.IN_WORK],
            )
            .values("owner_id")
            .annotate(count=models.Count("id"))
        )
        count_by_owner = {item["owner_id"]: item["count"] for item in counts}
        least_count = min(count_by_owner.get(user.id, 0) for user in candidates)
        least_loaded = [
            user for user in candidates
            if count_by_owner.get(user.id, 0) == least_count
        ]
        chosen = least_loaded[rule._rr_cursor % len(least_loaded)]
    else:
        return None

    rule._rr_cursor += 1
    rule.save(update_fields=["_rr_cursor"])
    return chosen


def pick_region_balanced(company, routing=None):
    """
    Выбирает региональное правило для лида без определённого региона (§4.1):
    - least_loaded: по наименьшему числу открытых лидов (статус new/in_work)
    - round_robin: строгий RR по routing._rr_cursor
    """
    if not routing:
        routing = getattr(company, "consalting_regional_routing", None)
        if not routing:
            routing = RegionalFunnelRoutingConsalting.objects.filter(company=company).first()
    if not routing or not routing.enabled:
        return None

    rules = list(routing.rules.filter(is_active=True).select_related("funnel").order_by("order", "created_at"))
    if not rules:
        return None

    if routing.balance_strategy == RegionalFunnelRoutingConsalting.BalanceStrategy.ROUND_ROBIN:
        chosen = rules[routing._rr_cursor % len(rules)]
        routing._rr_cursor += 1
        routing.save(update_fields=["_rr_cursor"])
        return chosen

    # least_loaded (по умолчанию)
    from apps.consalting.models import LeadConsalting
    active_counts = (
        LeadConsalting.objects.filter(
            company=company,
            region_code__in=[r.region_code for r in rules],
            status__in=[LeadConsalting.Status.NEW, LeadConsalting.Status.IN_WORK]
        ).values("region_code").annotate(c=models.Count("id"))
    )
    cnt_map = {r["region_code"]: r["c"] for r in active_counts}
    min_c = min(cnt_map.get(r.region_code, 0) for r in rules)
    candidates = [r for r in rules if cnt_map.get(r.region_code, 0) == min_c]
    # Детерминированный тай-брейк: по порядку правил
    chosen = candidates[0]
    return chosen


def resolve_funnel_and_assignee(company, *, phone=None, wazzup_account_id=None, source="whatsapp"):
    """
    Разрешает воронку, первую стадию и ответственного по правилам RegionalFunnelRouting (§4.2).
    Возвращает:
      (funnel, stage, matched_rule, assigned_user)
    Если маршрутизация выключена или не настроена, возвращает (None, None, None, None).
    """
    if not company:
        return None, None, None, None

    routing = getattr(company, "consalting_regional_routing", None)
    if not routing:
        routing = RegionalFunnelRoutingConsalting.objects.filter(company=company).first()

    if not routing or not routing.enabled:
        return None, None, None, None

    with transaction.atomic():
        # Блокируем routing для синхронизации RR
        routing = RegionalFunnelRoutingConsalting.objects.select_for_update().get(pk=routing.pk)
        rules = list(routing.rules.filter(is_active=True).select_related("funnel").order_by("order", "created_at"))

        norm_phone = normalize_phone(phone)
        matched_rule = None

        # 3a. Сопоставление по префиксу телефона (longest prefix wins)
        if norm_phone:
            prefix_matches = []
            for r in rules:
                prefixes = r.phone_prefixes or []
                for p in prefixes:
                    np = normalize_phone(p)
                    if np:
                        # Проверяем совпадение с префиксом
                        if norm_phone.startswith(np) or norm_phone.lstrip("+").startswith(np.lstrip("+")):
                            prefix_matches.append((len(np), r))
            if prefix_matches:
                prefix_matches.sort(key=lambda x: x[0], reverse=True)
                matched_rule = prefix_matches[0][1]

        # 3b. Сопоставление по Wazzup account ID
        if not matched_rule and wazzup_account_id:
            acc_str = str(wazzup_account_id).strip().lower()
            for r in rules:
                acc_ids = [str(x).strip().lower() for x in (r.wazzup_account_ids or [])]
                if acc_str in acc_ids:
                    matched_rule = r
                    break

        # 3c. Сопоставление по источнику (source_channels)
        if not matched_rule and source:
            src_str = str(source).strip().lower()
            for r in rules:
                chans = [str(x).strip().lower() for x in (r.source_channels or [])]
                if src_str in chans:
                    matched_rule = r
                    break

        # 4. Fallback если правило не найдено — сбалансированное деление по регионам (§3, §4.1)
        chosen_funnel = None
        if matched_rule:
            chosen_funnel = matched_rule.funnel
        else:
            balanced_rule = pick_region_balanced(company, routing=routing)
            if balanced_rule:
                matched_rule = balanced_rule
                chosen_funnel = balanced_rule.funnel
            elif routing.fallback_strategy == RegionalFunnelRoutingConsalting.FallbackStrategy.DEFAULT_FUNNEL:
                chosen_funnel = routing.default_funnel
            elif routing.default_funnel:
                chosen_funnel = routing.default_funnel

        if not chosen_funnel:
            return None, None, None, None

        # 5. Первая стадия воронки
        stage = FunnelStageConsalting.objects.filter(funnel=chosen_funnel).order_by("order").first()
        if not stage:
            stage = FunnelStageConsalting.objects.create(
                company=company,
                funnel=chosen_funnel,
                name="Новый лид",
                stage_type=FunnelStageConsalting.StageType.NEW_LEAD,
                order=100
            )

        # 7. Назначение ответственного внутри региона
        assigned_user = None
        if matched_rule and matched_rule.assign_role_ids:
            rule_locked = RegionalFunnelRuleConsalting.objects.select_for_update().get(pk=matched_rule.pk)
            assigned_user = assign_owner_within_region(company, rule_locked)

        return chosen_funnel, stage, matched_rule, assigned_user


def redistribute_leads(company, actor, *, scope="main_unassigned", regions=None, dry_run=False):
    """
    Разовое выравнивание базы лидов по регионам (§4.2).
    scope:
      - 'main_unassigned': лиды главной воронки без владельца (status in new/in_work)
      - 'all_open': все открытые лиды компании (status in new/in_work)
      - 'inbound_new': входящие лиды status=new
    """
    from apps.consalting.models import LeadConsalting, InboundLeadConsalting, FunnelConsalting

    routing = getattr(company, "consalting_regional_routing", None)
    if not routing:
        routing = RegionalFunnelRoutingConsalting.objects.filter(company=company).first()
    if not routing:
        return {"planned": {}, "total": 0, "moved": 0}

    rules_qs = routing.rules.filter(is_active=True).select_related("funnel").order_by("order", "created_at")
    if regions:
        rules_qs = rules_qs.filter(region_code__in=regions)
    rules = list(rules_qs)
    if not rules:
        return {"planned": {}, "total": 0, "moved": 0}

    if scope == "main_unassigned":
        main_funnel = FunnelConsalting.objects.filter(company=company, is_main=True).first()
        if not main_funnel:
            main_funnel = FunnelConsalting.objects.filter(company=company).order_by("created_at").first()
        if not main_funnel:
            return {"planned": {r.region_code: 0 for r in rules}, "total": 0, "moved": 0}
        targets_qs = LeadConsalting.objects.filter(
            company=company,
            funnel=main_funnel,
            owner__isnull=True,
            status__in=[LeadConsalting.Status.NEW, LeadConsalting.Status.IN_WORK]
        ).order_by("created_at")
    elif scope == "all_open":
        targets_qs = LeadConsalting.objects.filter(
            company=company,
            status__in=[LeadConsalting.Status.NEW, LeadConsalting.Status.IN_WORK]
        ).order_by("created_at")
    elif scope == "inbound_new":
        targets_qs = InboundLeadConsalting.objects.filter(
            company=company,
            status=InboundLeadConsalting.Status.NEW
        ).order_by("created_at")
    else:
        raise ValueError(f"Неизвестный scope: {scope}")

    targets = list(targets_qs)
    n = len(targets)
    k = len(rules)
    if n == 0:
        return {"planned": {r.region_code: 0 for r in rules}, "total": 0, "moved": 0}

    base, rem = divmod(n, k)

    if scope == "inbound_new":
        load_map = {
            r.region_code: InboundLeadConsalting.objects.filter(
                company=company, region_code=r.region_code, status=InboundLeadConsalting.Status.NEW
            ).count()
            for r in rules
        }
    else:
        load_map = {
            r.region_code: LeadConsalting.objects.filter(
                company=company, region_code=r.region_code, status__in=[LeadConsalting.Status.NEW, LeadConsalting.Status.IN_WORK]
            ).count()
            for r in rules
        }

    order = sorted(rules, key=lambda r: (load_map.get(r.region_code, 0), rules.index(r)))
    plan = {r.region_code: base + (1 if i < rem else 0) for i, r in enumerate(order)}

    if dry_run:
        return {"planned": plan, "total": n}

    moved = 0
    with transaction.atomic():
        idx = 0
        for rule in order:
            cnt = plan[rule.region_code]
            chunk = targets[idx : idx + cnt]
            idx += cnt
            if not chunk:
                continue

            if scope == "inbound_new":
                for ib in chunk:
                    ib.region_code = rule.region_code
                    ib.save(update_fields=["region_code", "updated_at"])
                    moved += 1
            else:
                first_st = rule.funnel.stages.order_by("order").first()
                for lead in chunk:
                    lead.funnel = rule.funnel
                    if first_st:
                        lead.stage = first_st
                    lead.region_code = rule.region_code
                    lead.owner = None
                    lead.save(update_fields=["funnel", "stage", "region_code", "owner", "updated_at"])
                    moved += 1

    return {"planned": plan, "moved": moved}
