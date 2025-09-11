from rest_framework import serializers
from .models import (
    Lead, Course, Group, Student, Lesson,
    Folder, Document, Attendance, TeacherRate
)
from apps.users.models import User   # 🔑 используем User вместо Teacher


class CompanyReadOnlyMixin:
    def create(self, validated_data):
        request = self.context.get('request')
        if request and getattr(getattr(request.user, 'company', None), 'id', None):
            validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request and getattr(getattr(request.user, 'company', None), 'id', None):
            validated_data['company'] = request.user.company
        return super().update(instance, validated_data)


# ====== Lead ======
class LeadSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Lead
        fields = [
            'id', 'company', 'name', 'phone', 'source', 'note', 'created_at'
        ]
        read_only_fields = ['id', 'company', 'created_at']


# ====== Course ======
class CourseSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Course
        fields = ['id', 'company', 'title', 'price_per_month']
        read_only_fields = ['id', 'company']


# ====== Group ======
class GroupSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all())

    course_title = serializers.CharField(source='course.title', read_only=True)

    class Meta:
        model = Group
        fields = [
            'id', 'company', 'course', 'course_title',
            'name'
        ]
        read_only_fields = ['id', 'company', 'course_title']

    def validate(self, attrs):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)

        course = attrs.get('course') or getattr(self.instance, 'course', None)
        if user_company_id and course and course.company_id != user_company_id:
            raise serializers.ValidationError({'course': 'Курс принадлежит другой компании.'})
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
            'group', 'group_name', 'discount', 'note', 'created_at', "active"
        ]
        read_only_fields = ['id', 'company', 'created_at', 'group_name']

    def validate_group(self, group):
        if group is None:
            return group
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        if user_company_id and group.company_id != user_company_id:
            raise serializers.ValidationError('Группа принадлежит другой компании.')
        return group


# ====== Lesson ======
# ====== Lesson ======
class LessonSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all())
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all(), required=False)
    teacher = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True, required=False)

    group_name = serializers.CharField(source='group.name', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)
    teacher_name = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            'id', 'company',
            'group', 'group_name',
            'course', 'course_title',
            'teacher', 'teacher_name',
            'date', 'time', 'duration', 'classroom', 'created_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'group_name', 'course_title', 'teacher_name']

    def get_teacher_name(self, obj):
        if obj.teacher:
            return f"{obj.teacher.first_name} {obj.teacher.last_name}".strip() or obj.teacher.email
        return None

    def validate(self, attrs):
        request = self.context.get('request')
        company_id = getattr(getattr(request.user, 'company', None), 'id', None)

        group   = attrs.get('group')   or getattr(self.instance, 'group', None)
        course  = attrs.get('course')  if 'course'  in attrs else getattr(self.instance, 'course', None)
        teacher = attrs.get('teacher') if 'teacher' in attrs else getattr(self.instance, 'teacher', None)

        # company checks
        if company_id and group  and group.company_id  != company_id:
            raise serializers.ValidationError({'group':  'Группа принадлежит другой компании.'})
        if company_id and course and course.company_id != company_id:
            raise serializers.ValidationError({'course': 'Курс принадлежит другой компании.'})
        if company_id and teacher and teacher.company_id != company_id:
            raise serializers.ValidationError({'teacher': 'Преподаватель принадлежит другой компании.'})

        # если курс не передали — подставим курс группы
        if not course and group:
            attrs['course'] = group.course
            course = attrs['course']

        # курс урока должен совпадать с курсом группы
        if group and course and group.course_id != course.id:
            raise serializers.ValidationError({'course': 'Курс урока должен совпадать с курсом выбранной группы.'})

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
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
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
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        if user_company_id and folder and folder.company_id != user_company_id:
            raise serializers.ValidationError('Папка принадлежит другой компании.')
        return folder


# ====== Attendance (общий CRUD, если понадобится) ======
class AttendanceSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Attendance
        fields = [
            'id', 'company', 'lesson', 'student',
            'present', 'note', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'updated_at']
        ref_name = "EducationAttendance"

    def validate(self, attrs):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)

        lesson = attrs.get('lesson') or getattr(self.instance, 'lesson', None)
        student = attrs.get('student') or getattr(self.instance, 'student', None)

        if user_company_id and lesson and lesson.company_id != user_company_id:
            raise serializers.ValidationError({'lesson': 'Занятие принадлежит другой компании.'})
        if user_company_id and student and student.company_id != user_company_id:
            raise serializers.ValidationError({'student': 'Студент принадлежит другой компании.'})
        if lesson and student and student.group_id != lesson.group_id:
            raise serializers.ValidationError({'student': 'Студент не из группы этого занятия.'})
        return attrs


# ====== Lesson attendance snapshot (GET/PUT /lessons/{id}/attendance/) ======
class LessonAttendanceItemSerializer(serializers.Serializer):
    """Элемент снимка посещаемости урока."""
    student = serializers.UUIDField()
    present = serializers.BooleanField(allow_null=True)
    note = serializers.CharField(allow_blank=True, required=False)
    # для ответа (GET) — имя ученика
    student_name = serializers.CharField(read_only=True)

class LessonAttendanceSnapshotSerializer(serializers.Serializer):
    """Тело запроса для PUT /lessons/{id}/attendance/ (для схемы)."""
    attendances = LessonAttendanceItemSerializer(many=True)


# ====== История посещаемости ученика (GET /students/{id}/attendance/) ======
class StudentAttendanceSerializer(serializers.ModelSerializer):
    lesson = serializers.UUIDField(source='lesson.id', read_only=True)
    date = serializers.DateField(source='lesson.date', read_only=True)
    time = serializers.TimeField(source='lesson.time', read_only=True)
    group = serializers.CharField(source='lesson.group.name', read_only=True)

    class Meta:
        model = Attendance
        fields = ("lesson", "date", "time", "group", "present", "note")
        ref_name = "EducationStudentAttendanceItem"


class TeacherRateSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    # явно укажем queryset (иногда полезно для OpenAPI)
    teacher = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    teacher_name = serializers.CharField(source="teacher.get_full_name", read_only=True)

    class Meta:
        model = TeacherRate
        fields = [
            "id", "company",
            "teacher", "teacher_name",
            "period", "mode", "rate",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "teacher_name", "created_at", "updated_at"]

    def validate(self, attrs):
        request = self.context["request"]
        company = request.user.company

        teacher = attrs.get("teacher", getattr(self.instance, "teacher", None))
        period  = attrs.get("period",  getattr(self.instance, "period",  None))
        mode    = attrs.get("mode",    getattr(self.instance, "mode",    None))

        # принадлежность преподавателя компании (если в User есть company_id)
        teacher_company_id = getattr(teacher, "company_id", None)
        if teacher and teacher_company_id and teacher_company_id != company.id:
            raise serializers.ValidationError({"teacher": "Преподаватель из другой компании."})

        # человекочитаемая проверка уникальности (вместо 500 по БД-ограничению)
        if teacher and period and mode:
            qs = TeacherRate.objects.filter(
                company_id=company.id,
                teacher=teacher,
                period=period,
                mode=mode,
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    "non_field_errors": ["Ставка уже существует для этого преподавателя, периода и режима."]
                })

        return attrs

    def create(self, validated_data):
        validated_data["company"] = self.context["request"].user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # фиксируем company от пользователя и при обновлении
        validated_data["company"] = self.context["request"].user.company
        return super().update(instance, validated_data)