from rest_framework import generics, permissions, status
from rest_framework import filters as drf_filters
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters import rest_framework as dj_filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView

from .models import (
    Lead, Course, Group, Student, Lesson,
    Folder, Document, Attendance, TeacherRate
)
from .serializers import (
    LeadSerializer, CourseSerializer, GroupSerializer,
    StudentSerializer, LessonSerializer, FolderSerializer, DocumentSerializer,
    LessonAttendanceItemSerializer, StudentAttendanceSerializer, TeacherRateSerializer
)


# ----- Кастомный фильтр для Document (не автофильтруем FileField) -----
class DocumentFilter(dj_filters.FilterSet):
    name = dj_filters.CharFilter(lookup_expr='icontains')
    folder = dj_filters.UUIDFilter(field_name='folder__id')  # фильтр по UUID папки
    file_name = dj_filters.CharFilter(field_name='file', lookup_expr='icontains')
    created_at = dj_filters.IsoDateTimeFromToRangeFilter()
    updated_at = dj_filters.IsoDateTimeFromToRangeFilter()

    class Meta:
        model = Document
        fields = ['name', 'folder', 'file_name', 'created_at', 'updated_at']


# ===== Company + Branch scoped mixin (как в «барбере») =====
class CompanyBranchQuerysetMixin:
    """
    🔒 Универсальный миксин для фильтрации queryset по компании и филиалу.

    Правила:
      • Всегда фильтрует записи по company пользователя.
      • Если у пользователя есть активный филиал → показывает только branch=<активный>.
      • Если филиала нет → показывает глобальные (branch IS NULL).
      • При создании/обновлении автоматически проставляет company/branch.
    """
    _UNSET = object()  # единый маркер "ещё не вычисляли"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cached_active_branch = self._UNSET

    # ---- helpers ----
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        """Определяем компанию пользователя (Company)."""
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _user_primary_branch(self):
        """Определяем основной филиал пользователя (Branch)."""
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None

        memberships = getattr(user, "branch_memberships", None)
        if memberships is None:
            return None

        # Приоритет: primary → первый попавшийся
        primary = memberships.filter(is_primary=True).select_related("branch").first()
        if primary and primary.branch:
            return primary.branch

        any_member = memberships.select_related("branch").first()
        return any_member.branch if any_member and any_member.branch else None

    def _get_model(self):
        """Извлекаем модель из serializer.Meta.model (если есть)."""
        sc = self.get_serializer_class()
        return getattr(getattr(sc, "Meta", None), "model", None)

    def _model_has_field(self, field_name: str) -> bool:
        model = self._get_model()
        if not model:
            return False
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    def _active_branch(self):
        """Определяем активный филиал и кешируем результат."""
        if self._cached_active_branch is not self._UNSET:
            return self._cached_active_branch

        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            self._cached_active_branch = None
            return None

        user_branch = self._user_primary_branch()
        if user_branch and user_branch.company_id == company.id:
            setattr(request, "branch", user_branch)
            self._cached_active_branch = user_branch
            return user_branch

        setattr(request, "branch", None)
        self._cached_active_branch = None
        return None

    # ---- queryset / save hooks ----
    def get_queryset(self):
        """Автоматически фильтруем queryset по company и branch."""
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        if self._model_has_field("branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                qs = qs.filter(branch=active_branch)
            else:
                qs = qs.filter(branch__isnull=True)

        return qs

    def perform_create(self, serializer):
        """Автопроставление company/branch при создании."""
        company = self._user_company()
        branch = self._active_branch() if self._model_has_field("branch") else None
        serializer.save(company=company, branch=branch)

    def perform_update(self, serializer):
        """Автопроставление company/branch при обновлении."""
        company = self._user_company()
        branch = self._active_branch() if self._model_has_field("branch") else None
        serializer.save(company=company, branch=branch)



# ===== Leads =====
class LeadListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Lead.objects.all()
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['name', 'phone', 'source', 'created_at']
    search_fields = ['name', 'phone', 'note']
    ordering_fields = ['created_at', 'name', 'source']
    ordering = ['-created_at']


class LeadRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Lead.objects.all()
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Courses =====
class CourseListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['title', 'price_per_month']
    search_fields = ['title']
    ordering_fields = ['title', 'price_per_month']
    ordering = ['title']


class CourseRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Groups =====
class GroupListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Group.objects.select_related('course')
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['course', 'name']
    search_fields = ['name', 'course__title']
    ordering_fields = ['name', 'course']
    ordering = ['course', 'name']


class GroupRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Group.objects.select_related('course')
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Students =====
class StudentListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Student.objects.select_related('group')
    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['status', 'group', 'active', 'created_at']
    search_fields = ['name', 'phone', 'note', 'group__name']
    ordering_fields = ['created_at', 'name', 'status']
    ordering = ['-created_at']


class StudentRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Student.objects.select_related('group')
    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Lessons =====
class LessonListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Lesson.objects.select_related('group', 'teacher', 'course')
    serializer_class = LessonSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['group', 'course', 'teacher', 'date', 'time', 'classroom']
    search_fields = ['group__name', 'teacher__first_name', 'teacher__last_name', 'classroom']
    ordering_fields = ['date', 'time', 'created_at']
    ordering = ['-date', '-time']


class LessonRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Lesson.objects.select_related('group', 'teacher', 'course')
    serializer_class = LessonSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Folders =====
class FolderListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related('parent')
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['parent', 'name']
    search_fields = ['name', 'parent__name']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']


class FolderRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Folder.objects.select_related('parent')
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Documents =====
class DocumentListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Document.objects.select_related('folder')
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_class = DocumentFilter
    search_fields = ['name', 'folder__name', 'file']  # по имени файла тоже найдёт
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-created_at']


class DocumentRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Document.objects.select_related('folder')
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]


# ===== Lesson attendance snapshot =====
class LessonAttendanceView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_lesson(self, lesson_id):
        company = self._user_company()
        # учитываем и ветку (глобально/филиально), как в списках
        qs = Lesson.objects.select_related("group", "company")
        qs = qs.filter(company=company)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        else:
            qs = qs.filter(branch__isnull=True)
        return get_object_or_404(qs, id=lesson_id)

    def get(self, request, lesson_id):
        lesson = self._get_lesson(lesson_id)

        students = (
            Student.objects
            .filter(company=lesson.company, group=lesson.group)
            .only("id", "name")
            .order_by("name")
        )
        existing = {a.student_id: a for a in Attendance.objects.filter(lesson=lesson)}

        items = []
        for s in students:
            a = existing.get(s.id)
            items.append({
                "student": s.id,
                "student_name": s.name,
                "present": None if not a else a.present,
                "note": "" if not a else (a.note or ""),
            })

        ser = LessonAttendanceItemSerializer(items, many=True)
        return Response({
            "lesson": str(lesson.id),
            "group": lesson.group.name,
            "attendances": ser.data,
        })

    def put(self, request, lesson_id):
        lesson = self._get_lesson(lesson_id)
        payload = request.data.get("attendances", [])

        ser = LessonAttendanceItemSerializer(data=payload, many=True)
        ser.is_valid(raise_exception=True)
        items = ser.validated_data

        group_student_ids = set(
            Student.objects.filter(company=lesson.company, group=lesson.group)
            .values_list("id", flat=True)
        )
        payload_ids = [i["student"] for i in items]
        payload_set = set(payload_ids)

        unknown = payload_set - group_student_ids
        missing = group_student_ids - payload_set
        if unknown:
            return Response(
                {"detail": "Есть ученики не из этой группы.", "unknown": list(unknown)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if missing:
            return Response(
                {"detail": "Передан неполный снимок — отсутствуют ученики группы.", "missing": list(missing)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(payload_ids) != len(payload_set):
            return Response({"detail": "Дубликаты учеников в списке."}, status=status.HTTP_400_BAD_REQUEST)

        existing = {a.student_id: a for a in Attendance.objects.filter(lesson=lesson)}
        to_create, to_update = [], []

        for i in items:
            sid = i["student"]
            present = i.get("present")
            note = i.get("note", "")

            if sid in existing:
                a = existing[sid]
                a.present = present
                a.note = note
                to_update.append(a)
            else:
                to_create.append(Attendance(
                    company=lesson.company,
                    branch=lesson.branch,   # 🔑 проставляем branch, как и в миксине
                    lesson=lesson,
                    student_id=sid,
                    present=present,
                    note=note,
                ))

        with transaction.atomic():
            if to_create:
                Attendance.objects.bulk_create(to_create, ignore_conflicts=True)
            if to_update:
                Attendance.objects.bulk_update(to_update, fields=["present", "note"])

        return self.get(request, lesson_id)


# ===== Student attendance history =====
class StudentAttendanceListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudentAttendanceSerializer

    def get_queryset(self):
        company = self._user_company()
        active_branch = self._active_branch()

        student_qs = Student.objects.filter(company=company)
        if active_branch is not None:
            student_qs = student_qs.filter(branch__in=[None, active_branch])  # студент может быть глобальным
        else:
            student_qs = student_qs.filter(branch__isnull=True)

        student = get_object_or_404(student_qs, id=self.kwargs["student_id"])

        qs = Attendance.objects.filter(company=company, student=student).select_related("lesson", "lesson__group")
        if self._model_has_field("branch"):
            if active_branch is not None:
                qs = qs.filter(branch__in=[None, active_branch])  # отметка может быть глобальной или филиальной
            else:
                qs = qs.filter(branch__isnull=True)
        return qs.order_by("-lesson__date", "-lesson__time")


# ===== Teacher rates =====
class TeacherRateListCreateAPIView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/teacher-rates/?teacher=<id>&period=YYYY-MM&mode=hour|lesson|month
    POST /api/main/teacher-rates/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TeacherRateSerializer
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    filterset_fields = ["teacher", "period", "mode"]
    ordering_fields = ["updated_at", "period", "rate"]
    ordering = ["-updated_at"]

    def get_queryset(self):
        qs = TeacherRate.objects.filter(company_id=self.request.user.company_id)
        active_branch = self._active_branch()
        return qs.filter(branch=active_branch) if active_branch is not None else qs.filter(branch__isnull=True)


class TeacherRateRetrieveUpdateDestroyAPIView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/main/teacher-rates/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TeacherRateSerializer

    def get_queryset(self):
        qs = TeacherRate.objects.filter(company_id=self.request.user.company_id)
        active_branch = self._active_branch()
        return qs.filter(branch=active_branch) if active_branch is not None else qs.filter(branch__isnull=True)
