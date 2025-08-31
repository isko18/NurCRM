from rest_framework import generics, permissions
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Lead, Course, Group, Student, Lesson,
    Folder, Document,
)
from .serializers import (
    LeadSerializer, CourseSerializer, GroupSerializer,
    StudentSerializer, LessonSerializer, FolderSerializer, DocumentSerializer,
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
