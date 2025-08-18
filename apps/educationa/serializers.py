from rest_framework import serializers
from .models import Lead, Course, Teacher, Group, Student, Lesson, Folder, Document


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = "__all__"


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = "__all__"


class TeacherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = "__all__"


class GroupSerializer(serializers.ModelSerializer):
    course = CourseSerializer(read_only=True)
    teacher = TeacherSerializer(read_only=True)

    class Meta:
        model = Group
        fields = "__all__"


class StudentSerializer(serializers.ModelSerializer):
    group = GroupSerializer(read_only=True)

    class Meta:
        model = Student
        fields = "__all__"


class LessonSerializer(serializers.ModelSerializer):
    group = GroupSerializer(read_only=True)
    teacher = TeacherSerializer(read_only=True)

    class Meta:
        model = Lesson
        fields = "__all__"


class FolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Folder
        fields = "__all__"


class DocumentSerializer(serializers.ModelSerializer):
    folder = FolderSerializer(read_only=True)

    class Meta:
        model = Document
        fields = "__all__"
