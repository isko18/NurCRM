from rest_framework import generics, permissions
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView

from .models import (
    Lead, Course, Group, Student, Lesson,
    Folder, Document, Attendance
)
from .serializers import (
    LeadSerializer, CourseSerializer, GroupSerializer,
    StudentSerializer, LessonSerializer, FolderSerializer, DocumentSerializer, LessonAttendanceItemSerializer, StudentAttendanceSerializer
)


# ----- Кастомный фильтр для Document (не автофильтруем FileField) -----
class DocumentFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr='icontains')
    folder = filters.UUIDFilter(field_name='folder__id')  # фильтр по UUID папки
    file_name = filters.CharFilter(field_name='file', lookup_expr='icontains')
    created_at = filters.DateTimeFromToRangeFilter()
    updated_at = filters.DateTimeFromToRangeFilter()

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
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['id', 'company', 'name', 'phone', 'source', 'note', 'created_at']


class LeadRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Lead.objects.all()
    serializer_class = LeadSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Courses =====
class CourseListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Course._meta.get_fields() if not f.is_relation or f.many_to_one]


class CourseRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Groups =====
class GroupListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Group.objects.select_related('course').all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Group._meta.get_fields() if not f.is_relation or f.many_to_one]


class GroupRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Group.objects.select_related('course').all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Students =====
class StudentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Student.objects.select_related('group').all()
    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Student._meta.get_fields() if not f.is_relation or f.many_to_one]


class StudentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Student.objects.select_related('group').all()
    serializer_class = StudentSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Lessons =====
class LessonListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Lesson.objects.select_related('group', 'teacher').all()
    serializer_class = LessonSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Lesson._meta.get_fields() if not f.is_relation or f.many_to_one]


class LessonRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Lesson.objects.select_related('group', 'teacher').all()
    serializer_class = LessonSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===== Folders =====
class FolderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Folder._meta.get_fields() if not f.is_relation or f.many_to_one]
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
    filter_backends = [DjangoFilterBackend]
    filterset_class = DocumentFilter


class DocumentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]


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
