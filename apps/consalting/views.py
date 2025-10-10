from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied

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
          * при активном филиале пользователя → только записи этого филиала
          * без филиала → только глобальные записи (branch IS NULL)
    Создание/обновление:
      - company берём из пользователя
      - branch автоматически из пользователя/запроса (см. _active_branch)
    Безопасен для swagger_fake_view и анонимов.
    """
    permission_classes = [permissions.IsAuthenticated]

    # --- user/company/branch helpers ---
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        # поддержка владельца и сотрудника
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _active_branch(self):
        """
        Определяем активный филиал пользователя:
          1) user.primary_branch() как метод
          2) user.primary_branch как свойство
          3) request.branch (если проставляет middleware)
          4) None (глобальная область)
        """
        request = getattr(self, "request", None)
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            if request:
                setattr(request, "branch", None)
            return None

        # 1) метод
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val:
                    if request:
                        setattr(request, "branch", val)
                    return val
            except Exception:
                pass
        # 2) свойство
        if primary:
            if request:
                setattr(request, "branch", primary)
            return primary

        # 3) из middleware
        if request and hasattr(request, "branch"):
            return request.branch

        # 4) глобально
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

        qs = qs.filter(company=company)

        model = qs.model
        if _has_field(model, "branch"):
            active_branch = self._active_branch()  # None или Branch
            if active_branch is not None:
                qs = qs.filter(branch=active_branch)
            else:
                qs = qs.filter(branch__isnull=True)
        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if _has_field(self.get_queryset().model, "branch"):
            serializer.save(company=company, branch=self._active_branch())
        else:
            serializer.save(company=company)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if _has_field(self.get_queryset().model, "branch"):
            serializer.save(company=company, branch=self._active_branch())
        else:
            serializer.save(company=company)


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer


class ServicesConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ServicesConsalting.objects.all()
    serializer_class = ServicesConsaltingSerializer


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = SaleConsalting.objects.select_related("services", "client", "user", "company").all()
    serializer_class = SaleConsaltingSerializer

    def perform_create(self, serializer):
        # company/branch — миксин; user — текущий оператор
        company = self._user_company()
        if _has_field(self.get_queryset().model, "branch"):
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


class SalaryConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SalaryConsalting.objects.select_related("user", "company").all()
    serializer_class = SalaryConsaltingSerializer


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company").all()
    serializer_class = RequestsConsaltingSerializer


class RequestsConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company").all()
    serializer_class = RequestsConsaltingSerializer


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = BookingConsalting.objects.select_related("employee", "company").all()
    serializer_class = BookingConsaltingSerializer


class BookingConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BookingConsalting.objects.select_related("employee", "company").all()
    serializer_class = BookingConsaltingSerializer
