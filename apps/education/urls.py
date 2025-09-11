from django.urls import path

from .views import (
    LeadListCreateView, LeadRetrieveUpdateDestroyView,
    CourseListCreateView, CourseRetrieveUpdateDestroyView,
    GroupListCreateView, GroupRetrieveUpdateDestroyView,
    StudentListCreateView, StudentRetrieveUpdateDestroyView,
    LessonListCreateView, LessonRetrieveUpdateDestroyView,
    FolderListCreateView, FolderRetrieveUpdateDestroyView,
    DocumentListCreateView, DocumentRetrieveUpdateDestroyView,
    LessonAttendanceView, StudentAttendanceListView,
    TeacherRateListCreateAPIView, TeacherRateRetrieveUpdateDestroyAPIView,
)

app_name = "education"

urlpatterns = [
    path("leads/", LeadListCreateView.as_view(), name="lead-list"),
    path("leads/<uuid:pk>/", LeadRetrieveUpdateDestroyView.as_view(), name="lead-detail"),

    path("courses/", CourseListCreateView.as_view(), name="course-list"),
    path("courses/<uuid:pk>/", CourseRetrieveUpdateDestroyView.as_view(), name="course-detail"),

    path("groups/", GroupListCreateView.as_view(), name="group-list"),
    path("groups/<uuid:pk>/", GroupRetrieveUpdateDestroyView.as_view(), name="group-detail"),

    path("students/", StudentListCreateView.as_view(), name="student-list"),
    path("students/<uuid:pk>/", StudentRetrieveUpdateDestroyView.as_view(), name="student-detail"),

    path("lessons/", LessonListCreateView.as_view(), name="lesson-list"),
    path("lessons/<uuid:pk>/", LessonRetrieveUpdateDestroyView.as_view(), name="lesson-detail"),

    path("folders/", FolderListCreateView.as_view(), name="folder-list"),
    path("folders/<uuid:pk>/", FolderRetrieveUpdateDestroyView.as_view(), name="folder-detail"),

    path("documents/", DocumentListCreateView.as_view(), name="document-list"),
    path("documents/<uuid:pk>/", DocumentRetrieveUpdateDestroyView.as_view(), name="document-detail"),

    # Посещаемость
    path("lessons/<uuid:lesson_id>/attendance/", LessonAttendanceView.as_view(), name="lesson-attendance"),
    path("students/<uuid:student_id>/attendance/", StudentAttendanceListView.as_view(), name="student-attendance"),

    # Ставки преподавателей
    path("teacher-rates/", TeacherRateListCreateAPIView.as_view(), name="teacher-rate-list"),
    path("teacher-rates/<uuid:pk>/", TeacherRateRetrieveUpdateDestroyAPIView.as_view(), name="teacher-rate-detail"),
]
