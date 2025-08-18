from django.urls import path

from .views import (
    # Leads
    LeadListCreateView, LeadRetrieveUpdateDestroyView,
    # Courses
    CourseListCreateView, CourseRetrieveUpdateDestroyView,
    # Teachers
    TeacherListCreateView, TeacherRetrieveUpdateDestroyView,
    # Groups
    GroupListCreateView, GroupRetrieveUpdateDestroyView,
    # Students
    StudentListCreateView, StudentRetrieveUpdateDestroyView,
    # Lessons
    LessonListCreateView, LessonRetrieveUpdateDestroyView,
    # Folders & Documents
    FolderListCreateView, FolderRetrieveUpdateDestroyView,
    DocumentListCreateView, DocumentRetrieveUpdateDestroyView,
)

app_name = "education"  # можете поменять на имя вашего приложения

urlpatterns = [
    # Leads
    path("leads/", LeadListCreateView.as_view(), name="lead-list"),
    path("leads/<uuid:pk>/", LeadRetrieveUpdateDestroyView.as_view(), name="lead-detail"),

    # Courses
    path("courses/", CourseListCreateView.as_view(), name="course-list"),
    path("courses/<uuid:pk>/", CourseRetrieveUpdateDestroyView.as_view(), name="course-detail"),

    # Teachers
    path("teachers/", TeacherListCreateView.as_view(), name="teacher-list"),
    path("teachers/<uuid:pk>/", TeacherRetrieveUpdateDestroyView.as_view(), name="teacher-detail"),

    # Groups
    path("groups/", GroupListCreateView.as_view(), name="group-list"),
    path("groups/<uuid:pk>/", GroupRetrieveUpdateDestroyView.as_view(), name="group-detail"),

    # Students
    path("students/", StudentListCreateView.as_view(), name="student-list"),
    path("students/<uuid:pk>/", StudentRetrieveUpdateDestroyView.as_view(), name="student-detail"),

    # Lessons
    path("lessons/", LessonListCreateView.as_view(), name="lesson-list"),
    path("lessons/<uuid:pk>/", LessonRetrieveUpdateDestroyView.as_view(), name="lesson-detail"),

    # Folders
    path("folders/", FolderListCreateView.as_view(), name="folder-list"),
    path("folders/<uuid:pk>/", FolderRetrieveUpdateDestroyView.as_view(), name="folder-detail"),

    # Documents (multipart/form-data для POST/PATCH с файлом)
    path("documents/", DocumentListCreateView.as_view(), name="document-list"),
    path("documents/<uuid:pk>/", DocumentRetrieveUpdateDestroyView.as_view(), name="document-detail"),
]
