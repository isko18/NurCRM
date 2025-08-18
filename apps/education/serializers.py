# serializers.py
from rest_framework import serializers
from .models import (
    Lead, Course, Teacher, Group, Student, Lesson,
    Folder, Document,
)


class CompanyReadOnlyMixin:
    def create(self, validated_data):
        request = self.context.get('request')
        if request and getattr(getattr(request, 'user', None), 'company_id', None):
            validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request and getattr(getattr(request, 'user', None), 'company_id', None):
            validated_data['company'] = request.user.company
        return super().update(instance, validated_data)


# ====== Lead ======
class LeadSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    student = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.all(), allow_null=True, required=False
    )
    # для удобного отображения
    student_name = serializers.CharField(source='student.name', read_only=True)

    class Meta:
        model = Lead
        fields = [
            'id', 'company', 'name', 'phone', 'source', 'note',
            'created_at', 'student', 'student_name'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'student_name']

    def validate_student(self, student):
        if student is None:
            return student
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)
        if user_company_id and student.company_id != user_company_id:
            raise serializers.ValidationError('Студент принадлежит другой компании.')
        # запрет связывать студента, у которого уже есть лид (OneToOne)
        inst = getattr(self, 'instance', None)
        if hasattr(student, 'lead') and (inst is None or student.lead_id != getattr(inst, 'id', None)):
            raise serializers.ValidationError('Этот студент уже связан с другим лидом.')
        return student


# ====== Course ======
class CourseSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Course
        fields = ['id', 'company', 'title', 'price_per_month']
        read_only_fields = ['id', 'company']


# ====== Teacher ======
class TeacherSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Teacher
        fields = ['id', 'company', 'name', 'phone', 'subject']
        read_only_fields = ['id', 'company']


# ====== Group ======
class GroupSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all())
    teacher = serializers.PrimaryKeyRelatedField(queryset=Teacher.objects.all(), allow_null=True, required=False)

    # удобные readonly-поля
    course_title = serializers.CharField(source='course.title', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)

    class Meta:
        model = Group
        fields = [
            'id', 'company', 'course', 'course_title',
            'name', 'teacher', 'teacher_name'
        ]
        read_only_fields = ['id', 'company', 'course_title', 'teacher_name']

    def validate(self, attrs):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)

        course = attrs.get('course') or getattr(self.instance, 'course', None)
        teacher = attrs.get('teacher') if 'teacher' in attrs else getattr(self.instance, 'teacher', None)

        if user_company_id and course and course.company_id != user_company_id:
            raise serializers.ValidationError({'course': 'Курс принадлежит другой компании.'})
        if user_company_id and teacher and teacher.company_id != user_company_id:
            raise serializers.ValidationError({'teacher': 'Преподаватель принадлежит другой компании.'})
        return attrs


# ====== Student ======
class StudentSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all(), allow_null=True, required=False)
    group_name = serializers.CharField(source='group.name', read_only=True)

    class Meta:
        model = Student
        fields = [
            'id', 'company', 'name', 'phone', 'status',
            'group', 'group_name', 'discount', 'note', 'created_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'group_name']

    def validate_group(self, group):
        if group is None:
            return group
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)
        if user_company_id and group.company_id != user_company_id:
            raise serializers.ValidationError('Группа принадлежит другой компании.')
        return group


# ====== Lesson ======
class LessonSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all())
    teacher = serializers.PrimaryKeyRelatedField(queryset=Teacher.objects.all(), allow_null=True, required=False)

    group_name = serializers.CharField(source='group.name', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)

    class Meta:
        model = Lesson
        fields = [
            'id', 'company', 'group', 'group_name',
            'teacher', 'teacher_name',
            'date', 'time', 'duration', 'classroom', 'created_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'group_name', 'teacher_name']

    def validate(self, attrs):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)

        group = attrs.get('group') or getattr(self.instance, 'group', None)
        teacher = attrs.get('teacher') if 'teacher' in attrs else getattr(self.instance, 'teacher', None)

        if user_company_id and group and group.company_id != user_company_id:
            raise serializers.ValidationError({'group': 'Группа принадлежит другой компании.'})
        if user_company_id and teacher and teacher.company_id != user_company_id:
            raise serializers.ValidationError({'teacher': 'Преподаватель принадлежит другой компании.'})
        return attrs


# ====== Folder ======
class FolderSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    parent = serializers.PrimaryKeyRelatedField(queryset=Folder.objects.all(), allow_null=True, required=False)
    parent_name = serializers.CharField(source='parent.name', read_only=True)

    class Meta:
        model = Folder
        fields = ['id', 'company', 'name', 'parent', 'parent_name']
        read_only_fields = ['id', 'company', 'parent_name']
        ref_name = "EducationFolder"

    def validate_parent(self, parent):
        if parent is None:
            return parent
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)
        if user_company_id and parent.company_id != user_company_id:
            raise serializers.ValidationError('Родительская папка принадлежит другой компании.')
        return parent


# ====== Document ======
class DocumentSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    folder_name = serializers.CharField(source='folder.name', read_only=True)

    class Meta:
        model = Document
        fields = [
            'id', 'company', 'name', 'file',
            'folder', 'folder_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'updated_at', 'folder_name']
        ref_name = "EducationDocument"
        

    def validate_folder(self, folder):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)
        if user_company_id and folder and folder.company_id != user_company_id:
            raise serializers.ValidationError('Папка принадлежит другой компании.')
        return folder
