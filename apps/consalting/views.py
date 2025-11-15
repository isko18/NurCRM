from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied

from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting,
)
from .serializers import (
    ServicesConsaltingSerializer,
    SaleConsaltingSerializer,
    SalaryConsaltingSerializer,
    RequestsConsaltingSerializer,
    BookingConsaltingSerializer,
)
from apps.users.models import Branch


# ===== helpers =====
def _has_field(model_cls, field_name: str) -> bool:
    try:
        return any(f.name == field_name for f in model_cls._meta.get_fields())
    except Exception:
        return False


# ===== company + branch scoped mixin (как в барбере/букинге/кафе) =====
class CompanyBranchQuerysetMixin:
    """
    Видимость данных:
      - всегда ограничиваемся компанией пользователя
      - если у модели есть поле branch:
          * при привязке сотрудника к филиалу → только записи этого филиала
          * без филиала → все записи компании (без фильтра по branch)

    Активный филиал:
      1) «жёстко» назначенный филиал пользователя:
           - user.primary_branch() / user.primary_branch
           - user.branch
           - (опционально) единственный филиал из user.branch_ids
      2) если жёсткого филиала нет — ?branch=<uuid>, если филиал принадлежит компании
      3) request.branch (если проставляет middleware и он из этой компании)
      4) иначе None

    Создание:
      - company берём из пользователя
      - если активный филиал определён → жёстко ставим его в branch
      - если филиала нет → branch не трогаем

    Обновление:
      - company фиксируем
      - branch не меняем (не переносим запись между филиалами)
    """

    permission_classes = [permissions.IsAuthenticated]

    # --- helpers: user / company / branch ---

    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        """
        Компания пользователя:
          - сначала owned_company / company
          - если нет, пробуем взять через user.branch.company
        """
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None

        company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        if company:
            return company

        # fallback: компания только через филиал пользователя
        br = getattr(user, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _fixed_branch_from_user(self, company):
        """
        «Жёстко» назначенный филиал сотрудника (который нельзя менять ?branch):
          - user.primary_branch() / user.primary_branch
          - user.branch
          - (опционально) единственный филиал из branch_ids
        """
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        # 1) primary_branch: метод или атрибут
        primary = getattr(user, "primary_branch", None)

        # 1a) как метод
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass

        # 1b) как свойство
        if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
            return primary

        # 2) user.branch
        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 3) единственный филиал в branch_ids
        branch_ids = getattr(user, "branch_ids", None)
        if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
            try:
                return Branch.objects.get(id=branch_ids[0], company_id=company_id)
            except Branch.DoesNotExist:
                pass

        return None

    def _active_branch(self):
        """
        Итоговый активный филиал с проверкой принадлежности компании.
        """
        request = getattr(self, "request", None)
        company = self._user_company()
        if not company:
            if request:
                setattr(request, "branch", None)
            return None

        company_id = getattr(company, "id", None)

        # 1) жёстко назначенный филиал (primary / branch / branch_ids)
        fixed = self._fixed_branch_from_user(company)
        if fixed is not None:
            if request:
                setattr(request, "branch", fixed)
            return fixed

        # 2) если жёсткого филиала нет — смотрим ?branch=
        branch_id = None
        if request is not None:
            if hasattr(request, "query_params"):
                branch_id = request.query_params.get("branch")
            elif hasattr(request, "GET"):
                branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # чужой/кривой id — игнорируем
                pass

        # 3) request.branch, если middleware уже поставил корректный филиал
        if request and hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 4) филиала нет
        if request:
            setattr(request, "branch", None)
        return None

    # --- queryset / save hooks ---

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        # ограничиваем по компании, если у модели есть такое поле
        model = qs.model
        if _has_field(model, "company"):
            qs = qs.filter(company=company)

        # если у модели есть branch — применяем логику филиала
        if _has_field(model, "branch"):
            active_branch = self._active_branch()  # None или Branch

            if active_branch is not None:
                # пользователь привязан к филиалу → только этот филиал
                qs = qs.filter(branch=active_branch)
            # если филиала нет → НЕ фильтруем по branch (видно все филиалы компании)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        model = self.get_queryset().model
        kwargs = {"company": company}

        if _has_field(model, "branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                # если есть филиал — жёстко пишем его
                kwargs["branch"] = active_branch
            # если филиала нет — branch не трогаем (можно создавать глобальные/любые по правилам сериализатора)

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        # company фиксируем, branch не меняем
        serializer.save(company=company)


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in ServicesConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class ServicesConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = SaleConsalting.objects.select_related("services", "client", "user", "company").all()
    serializer_class = SaleConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in SaleConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]

    def perform_create(self, serializer):
        # company/branch — миксин; user — текущий оператор
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        model = self.get_queryset().model
        if _has_field(model, "branch"):
            serializer.save(company=company, branch=self._active_branch(), user=self.request.user)
        else:
            serializer.save(company=company, user=self.request.user)


class SaleConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SaleConsalting.objects.select_related("services", "client", "user", "company").all()
    serializer_class = SaleConsaltingSerializer


# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = SalaryConsalting.objects.select_related("user", "company").all()
    serializer_class = SalaryConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in SalaryConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class SalaryConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SalaryConsalting.objects.select_related("user", "company").all()
    serializer_class = SalaryConsaltingSerializer


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company").all()
    serializer_class = RequestsConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in RequestsConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class RequestsConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company").all()
    serializer_class = RequestsConsaltingSerializer


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = BookingConsalting.objects.select_related("employee", "company").all()
    serializer_class = BookingConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in BookingConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class BookingConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BookingConsalting.objects.select_related("employee", "company").all()
    serializer_class = BookingConsaltingSerializer
