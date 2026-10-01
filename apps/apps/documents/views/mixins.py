
from typing import Optional
from apps.users.models import Branch, User
from django.db.models import Q

from rest_framework.permissions import IsAuthenticated

from rest_framework import permissions
from django.db import transaction, connection


# скопировано из main
class CompanyBranchRestrictedMixin:
    """
    - Фильтрует queryset по компании и (если у модели есть поле branch) по «активному филиалу».
    - На create/save подставляет company и (если у модели есть поле branch) — текущий филиал.

    Активный филиал:

        1) «жёсткий» филиал сотрудника:
            - user.primary_branch() / user.primary_branch
            - первый филиал из branch_ids
            - первая запись из user.branch_memberships / user.branches (если есть такие связи)
            - request.branch (если мидлварь уже положила)
        2) ?branch=<uuid> в запросе (если филиал принадлежит компании,
           И у пользователя нет жёстко назначенного филиала)
        3) None (нет филиала — работаем по всей компании, но только с записями без branch)

    Логика выборки:
        - если branch определён → показываем только данные этого филиала;
        - если branch = None → показываем только данные без филиала (branch IS NULL).
    """

    permission_classes = [permissions.IsAuthenticated]

    # ----- helpers -----
    def _request(self):
        return getattr(self, "request", None)

    def _user(self):
        req = self._request()
        return getattr(req, "user", None) if req else None

    def _company(self):
        """
        Компания текущего пользователя.
        Для суперюзера -> None (без ограничения по company).

        Если у юзера нет company, но есть филиал с company — берём её.
        """
        u = self._user()
        if not u or not getattr(u, "is_authenticated", False):
            return None
        if getattr(u, "is_superuser", False):
            return None

        company = getattr(u, "owned_company", None) or getattr(u, "company", None)
        if company:
            return company

        # fallback: пробуем достать компанию из его филиала (если есть связь)
        br = getattr(u, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _fixed_branch_from_user(self, company) -> Optional[Branch]:
        """
        «Жёстко» назначенный филиал сотрудника (который нельзя менять через ?branch):

         - user.primary_branch() или user.primary_branch
         - user.branch (если есть такое поле)
         - первый филиал из branch_ids (как в /me)
         - первая связь из user.branches / user.branch_memberships (если такие есть)
         - request.branch (если мидлварь уже положила)
        """
        req = self._request()
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        # 1) user.primary_branch как метод
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass

        # 1b) user.primary_branch как атрибут
        if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
            return primary

        # 1c) user.branch (если так хранится)
        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 1d) если у пользователя есть M2M/through связи на филиалы: user.branches
        try:
            if hasattr(user, "branches"):
                qs = user.branches.all()
                if company_id:
                    qs = qs.filter(company_id=company_id)
                b = qs.first()
                if b:
                    return b
        except Exception:
            pass

        # 1e) если есть user.branch_memberships -> branch
        try:
            if hasattr(user, "branch_memberships"):
                ms = (
                    user.branch_memberships
                    .select_related("branch")
                )
                if company_id:
                    ms = ms.filter(branch__company_id=company_id)
                m = ms.first()
                if m and getattr(m, "branch", None):
                    return m.branch
        except Exception:
            pass

        # 1f) если на модели User есть поле/свойство branch_ids (как в /me)
        #     выбираем первый филиал этой компании
        branch_ids = getattr(user, "branch_ids", None)
        if branch_ids:
            try:
                b = (
                    Branch.objects
                    .filter(id__in=list(branch_ids), company_id=company_id)
                    .first()
                )
                if b:
                    return b
            except Exception:
                # на всякий случай не роняем
                pass

        # 2) request.branch как результат работы middleware
        if req and hasattr(req, "branch"):
            b = getattr(req, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        return None

    def _auto_branch(self) -> Optional[Branch]:
        """
        Активный филиал:
          1) «Жёсткий» филиал сотрудника (primary / branch / branch_ids / memberships / request.branch)
          2) ?branch=<uuid> в запросе (если принадлежит компании и НЕТ жёсткого филиала)
          3) None (нет филиала — глобальный режим по всей компании, но только записи без branch)
        """
        req = self._request()
        user = self._user()
        if not req or not user or not getattr(user, "is_authenticated", False):
            return None

        # чтобы не дергать логику по несколько раз на один запрос
        cached = getattr(req, "_cached_auto_branch", None)
        if cached is not None:
            return cached

        company = self._company()
        company_id = getattr(company, "id", None)

        # 1) сначала ищем жёстко назначенный филиал
        fixed_branch = self._fixed_branch_from_user(company)
        if fixed_branch is not None:
            setattr(req, "branch", fixed_branch)
            setattr(req, "_cached_auto_branch", fixed_branch)
            return fixed_branch

        # 2) если у пользователя НЕТ назначенного филиала — позволяем выбирать через ?branch
        branch_id = None
        if hasattr(req, "query_params"):
            branch_id = req.query_params.get("branch")
        elif hasattr(req, "GET"):
            branch_id = req.GET.get("branch")

        if branch_id and company_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(req, "branch", br)
                setattr(req, "_cached_auto_branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # чужой/битый UUID — игнорируем
                pass

        # 3) никакого филиала → None (работаем по компании, но без филиалов)
        setattr(req, "_cached_auto_branch", None)
        return None

    @staticmethod
    def _model_has_field(model, field_name: str) -> bool:
        try:
            return any(f.name == field_name for f in model._meta.get_fields())
        except Exception:
            return False

    def _filter_qs_company_branch(
        self,
        qs,
        company_field: Optional[str] = None,
        branch_field: Optional[str] = None,
    ):
        """
        Ограничение queryset текущей company / branch.

        По умолчанию смотрим на поля самой модели:
            company / branch

        Но если данные живут не на самой модели, а через FK (например,
        AgentRequestItem -> cart -> company/branch), можно передать:
            company_field="cart__company"
            branch_field="cart__branch"

        НОВАЯ ЛОГИКА:
            - если branch определён → фильтруем по этому branch;
            - если branch is None → показываем только записи с branch IS NULL.
        """

        company = self._company()
        branch = self._auto_branch()
        model = qs.model

        # company
        if company is not None:
            if company_field:
                qs = qs.filter(**{company_field: company})
            elif self._model_has_field(model, "company"):
                qs = qs.filter(company=company)

        # branch
        if branch_field:
            if branch is not None:
                # есть активный филиал → только он
                qs = qs.filter(**{branch_field: branch})
            else:
                # филиал не выбран → только глобальные записи без филиала
                qs = qs.filter(**{f"{branch_field}__isnull": True})
        elif self._model_has_field(model, "branch"):
            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

        return qs

    def get_queryset(self):
        assert hasattr(self, "queryset") and self.queryset is not None, (
            f"{self.__class__.__name__} must define .queryset or override get_queryset()."
        )
        return self._filter_qs_company_branch(self.queryset.all())

    def get_serializer_context(self):
        # пробрасываем request, чтобы сериализаторы могли использовать company/branch/юзера
        ctx = super().get_serializer_context() if hasattr(super(), "get_serializer_context") else {}
        ctx["request"] = self.request
        return ctx

    def _save_with_company_branch(self, serializer, **extra):
        """
        Безопасно подставляет company/branch только если такие поля есть у модели.

        ВАЖНО:
        - если у пользователя есть активный филиал → всегда жёстко проставляем его в branch;
        - если филиала нет → branch не трогаем (можно создавать как глобальные, так и по филиалам,
          если это позволено сериализатором/валидаторами).
        """
        model = serializer.Meta.model
        kwargs = dict(extra)

        company = self._company()
        if self._model_has_field(model, "company") and company is not None:
            kwargs.setdefault("company", company)

        if self._model_has_field(model, "branch"):
            branch = self._auto_branch()
            if branch is not None:
                # сотрудник с филиалом — жёстко пишем его, игнорируя поле в payload
                kwargs["branch"] = branch
            # если branch is None — НЕ подставляем, пусть решает сериализатор/валидатор

        serializer.save(**kwargs)

    def perform_create(self, serializer):
        self._save_with_company_branch(serializer)

    def perform_update(self, serializer):
        self._save_with_company_branch(serializer)




