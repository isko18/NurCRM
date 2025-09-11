from rest_framework import generics, permissions
from rest_framework import filters as drf_filters  # DRF Search/Ordering
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters import rest_framework as dj_filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
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


class CompanyQuerysetMixin:
    """
    Скоуп по компании текущего пользователя + проставление company на create/update.
    Безопасен для drf_yasg (swagger_fake_view) и AnonymousUser.
    """
    def _user_company(self):
        user = getattr(self.request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        qs = super().get_queryset()
        company = self._user_company()
        return qs.filter(company=company) if company else qs.none()

    def perform_create(self, serializer):
        company = self._user_company()
        serializer.save(company=company) if company else serializer.save()

    def perform_update(self, serializer):
        company = self._user_company()
        serializer.save(company=company) if company else serializer.save()


# ===== Leads =====
class LeadListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Lead.objects.all()
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['name', 'phone', 'source', 'created_at']
    search_fields = ['name', 'phone', 'note']
    ordering_fields = ['created_at', 'name', 'source']
    ordering = ['-created_at']


class LeadRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Lead.objects.all()
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Courses =====
class CourseListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['title', 'price_per_month']
    search_fields = ['title']
    ordering_fields = ['title', 'price_per_month']
    ordering = ['title']


class CourseRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Groups =====
class GroupListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Group.objects.select_related('course').all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['course', 'name']
    search_fields = ['name', 'course__title']
    ordering_fields = ['name', 'course']
    ordering = ['course', 'name']


class GroupRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Group.objects.select_related('course').all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Students =====
class StudentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Student.objects.select_related('group').all()
    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['status', 'group', 'active', 'created_at']
    search_fields = ['name', 'phone', 'note', 'group__name']
    ordering_fields = ['created_at', 'name', 'status']
    ordering = ['-created_at']


class StudentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Student.objects.select_related('group').all()
    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Lessons =====
class LessonListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Lesson.objects.select_related('group', 'teacher', 'course').all()
    serializer_class = LessonSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['group', 'course', 'teacher', 'date', 'time', 'classroom']
    search_fields = ['group__name', 'teacher__first_name', 'teacher__last_name', 'classroom']
    ordering_fields = ['date', 'time', 'created_at']
    ordering = ['-date', '-time']


class LessonRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Lesson.objects.select_related('group', 'teacher', 'course').all()
    serializer_class = LessonSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Folders =====
class FolderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['parent', 'name']
    search_fields = ['name', 'parent__name']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']


class FolderRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Documents =====
class DocumentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_class = DocumentFilter
    search_fields = ['name', 'folder__name', 'file']  # по имени файла тоже найдёт
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-created_at']


class DocumentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]


# ===== Lesson attendance snapshot =====
class LessonAttendanceView(CompanyQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_lesson(self, lesson_id):
        company = self._user_company()
        return get_object_or_404(
            Lesson.objects.select_related("group", "company"),
            id=lesson_id, company=company
        )

    def get(self, request, lesson_id):
        lesson = self._get_lesson(lesson_id)

        # все ученики группы
        students = (
            Student.objects
            .filter(company=lesson.company, group=lesson.group)
            .order_by("name")
            .only("id", "name")
        )
        # существующие отметки
        existing = {
            a.student_id: a for a in Attendance.objects.filter(lesson=lesson)
        }

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

        # ожидаем снимок по всей группе
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

        # вернуть актуальный список (как GET)
        return self.get(request, lesson_id)


# ===== Student attendance history =====
class StudentAttendanceListView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudentAttendanceSerializer

    def get_queryset(self):
        company = self._user_company()
        student = get_object_or_404(
            Student.objects.filter(company=company),
            id=self.kwargs["student_id"]
        )
        return (
            Attendance.objects
            .filter(company=company, student=student)
            .select_related("lesson", "lesson__group")
            .order_by("-lesson__date", "-lesson__time")
        )


# ===== Teacher rates =====
class TeacherRateListCreateAPIView(CompanyQuerysetMixin, generics.ListCreateAPIView):
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
        return TeacherRate.objects.filter(company_id=self.request.user.company_id)


class TeacherRateRetrieveUpdateDestroyAPIView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/main/teacher-rates/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TeacherRateSerializer

    def get_queryset(self):
        return TeacherRate.objects.filter(company_id=self.request.user.company_id)
