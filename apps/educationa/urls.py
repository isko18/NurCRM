from django.urls import path
from apps.educationa.views import (
    LeadListCreateView,
    LeadDetailView,
    CourseListCreateView,
    CourseDetailView,
    TeacherListCreateView,
    TeacherDetailView,
    GroupListCreateView,
    GroupDetailView,
    StudentListCreateView,
    StudentDetailView,
    LessonListCreateView,
    LessonDetailView,
    FolderListCreateView,
    FolderDetailView,
    DocumentListCreateView,
    DocumentDetailView,
)

urlpatterns = [
    # LEADS
    path('leads/', LeadListCreateView.as_view(), name='lead-list'),
    path('leads/<uuid:pk>/', LeadDetailView.as_view(), name='lead-detail'),

    # COURSES
    path('courses/', CourseListCreateView.as_view(), name='course-list'),
    path('courses/<uuid:pk>/', CourseDetailView.as_view(), name='course-detail'),

    # TEACHERS
    path('teachers/', TeacherListCreateView.as_view(), name='teacher-list'),
    path('teachers/<uuid:pk>/', TeacherDetailView.as_view(), name='teacher-detail'),

    # GROUPS
    path('groups/', GroupListCreateView.as_view(), name='group-list'),
    path('groups/<uuid:pk>/', GroupDetailView.as_view(), name='group-detail'),

    # STUDENTS
    path('students/', StudentListCreateView.as_view(), name='student-list'),
    path('students/<uuid:pk>/', StudentDetailView.as_view(), name='student-detail'),

    # LESSONS
    path('lessons/', LessonListCreateView.as_view(), name='lesson-list'),
    path('lessons/<uuid:pk>/', LessonDetailView.as_view(), name='lesson-detail'),

    # FOLDERS
    path('folders/', FolderListCreateView.as_view(), name='folder-list'),
    path('folders/<uuid:pk>/', FolderDetailView.as_view(), name='folder-detail'),

    # DOCUMENTS
    path('documents/', DocumentListCreateView.as_view(), name='document-list'),
    path('documents/<uuid:pk>/', DocumentDetailView.as_view(), name='document-detail'),
]
