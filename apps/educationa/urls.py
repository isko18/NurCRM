from django.urls import path
from apps.educationa.views import (
    LeadViewSet,
    CourseViewSet,
    TeacherViewSet,
    GroupViewSet,
    StudentViewSet,
    LessonViewSet,
    FolderViewSet,
    DocumentViewSet,
)

lead_list = LeadViewSet.as_view({'get': 'list', 'post': 'create'})
lead_detail = LeadViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

course_list = CourseViewSet.as_view({'get': 'list', 'post': 'create'})
course_detail = CourseViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

teacher_list = TeacherViewSet.as_view({'get': 'list', 'post': 'create'})
teacher_detail = TeacherViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

group_list = GroupViewSet.as_view({'get': 'list', 'post': 'create'})
group_detail = GroupViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

student_list = StudentViewSet.as_view({'get': 'list', 'post': 'create'})
student_detail = StudentViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

lesson_list = LessonViewSet.as_view({'get': 'list', 'post': 'create'})
lesson_detail = LessonViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

folder_list = FolderViewSet.as_view({'get': 'list', 'post': 'create'})
folder_detail = FolderViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})

document_list = DocumentViewSet.as_view({'get': 'list', 'post': 'create'})
document_detail = DocumentViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})


urlpatterns = [
    # LEADS
    path('leads/', lead_list, name='lead-list'),
    path('leads/<uuid:pk>/', lead_detail, name='lead-detail'),

    # COURSES
    path('courses/', course_list, name='course-list'),
    path('courses/<uuid:pk>/', course_detail, name='course-detail'),

    # TEACHERS
    path('teachers/', teacher_list, name='teacher-list'),
    path('teachers/<uuid:pk>/', teacher_detail, name='teacher-detail'),

    # GROUPS
    path('groups/', group_list, name='group-list'),
    path('groups/<uuid:pk>/', group_detail, name='group-detail'),

    # STUDENTS
    path('students/', student_list, name='student-list'),
    path('students/<uuid:pk>/', student_detail, name='student-detail'),

    # LESSONS
    path('lessons/', lesson_list, name='lesson-list'),
    path('lessons/<uuid:pk>/', lesson_detail, name='lesson-detail'),

    # FOLDERS
    path('folders/', folder_list, name='folder-list'),
    path('folders/<uuid:pk>/', folder_detail, name='folder-detail'),

    # DOCUMENTS
    path('documents/', document_list, name='document-list'),
    path('documents/<uuid:pk>/', document_detail, name='document-detail'),
]
