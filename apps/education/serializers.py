from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    Lead, Course, Group, Student, Lesson,
    Folder, Document, Attendance, TeacherRate
)
from apps.users.models import User   # 🔑 используем User вместо Teacher


# ===========================
# Общий миксин: company/branch (branch авто из пользователя)
# ===========================
class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.
    Порядок получения branch:
      1) user.primary_branch() / user.primary_branch (если есть)
      2) request.branch (если положил mixin/view/middleware)
      3) None (глобальная запись компании)
    """
    _cached_branch = None

    def _auto_branch(self):
        if self._cached_branch is not None:
            return self._cached_branch

        request = self.context.get("request")
        if not request:
            self._cached_branch = None
            return None

        user = getattr(request, "user", None)
        user_company_id = getattr(getattr(user, "company", None), "id", None)

        branch_candidate = None
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                branch_candidate = primary() or None
            except Exception:
                branch_candidate = None
        elif primary:
            branch_candidate = primary

        if branch_candidate is None and hasattr(request, "branch"):
            branch_candidate = getattr(request, "branch")

        # консистентность company ↔ branch.company
        if branch_candidate and user_company_id and getattr(branch_candidate, "company_id", None) != user_company_id:
            branch_candidate = None

        self._cached_branch = branch_candidate
        return self._cached_branch

    def _inject_company_branch(self, validated_data):
        request = self.context.get("request")
        if request:
            user = getattr(request, "user", None)
            if user is not None and getattr(getattr(user, "company", None), "id", None):
                validated_data["company"] = user.company
            validated_data["branch"] = self._auto_branch()
        return validated_data

    def create(self, validated_data):
        self._inject_company_branch(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._inject_company_branch(validated_data)
        return super().update(instance, validated_data)


# ====== Lead ======
class LeadSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)


    class Meta:
        model = Lead
        fields = ['id', 'company', 'branch', 'name', 'phone', 'source', 'note', 'created_at']
        read_only_fields = ['id', 'company', 'branch', 'created_at']

    def validate(self, attrs):
        # нормализация телефона (минимальная)
        phone = attrs.get("phone")
        if phone:
            attrs["phone"] = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == '+')
        return attrs


# ====== Course ======
class CourseSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)


    class Meta:
        model = Course
        fields = ['id', 'company', 'branch', 'title', 'price_per_month']
        read_only_fields = ['id', 'company', 'branch']


# ====== Group ======
class GroupSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)


    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all())
    course_title = serializers.CharField(source='course.title', read_only=True)

    class Meta:
        model = Group
        fields = ['id', 'company', 'branch', 'course', 'course_title', 'name']
        read_only_fields = ['id', 'company', 'branch', 'course_title']

    def validate(self, attrs):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        target_branch = self._auto_branch()

        course = attrs.get('course') or getattr(self.instance, 'course', None)

        # company check
        if user_company_id and course and course.company_id != user_company_id:
            raise serializers.ValidationError({'course': 'Курс принадлежит другой компании.'})

        # branch check: если активен филиал, курс должен быть глобальным или этого филиала
        if target_branch is not None and course and course.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError({'course': 'Курс принадлежит другому филиалу.'})

        return attrs


# ====== Student ======
class StudentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)


    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all(), allow_null=True, required=False)
    group_name = serializers.CharField(source='group.name', read_only=True)

    class Meta:
        model = Student
        fields = [
            'id', 'company', 'branch', 'name', 'phone', 'status',
            'group', 'group_name', 'discount', 'note', 'created_at', 'active'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'group_name']

    def validate_group(self, group):
        if group is None:
            return group
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        if user_company_id and group.company_id != user_company_id:
            raise serializers.ValidationError('Группа принадлежит другой компании.')
        # ветка: студент глобальный/ветка пользователя; группа — глобальная/та же ветка
        target_branch = self._auto_branch()
        if target_branch is not None and group.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError('Группа принадлежит другому филиалу.')
        return group

    def validate(self, attrs):
        # нормализация телефона
        phone = attrs.get("phone")
        if phone:
            attrs["phone"] = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == '+')
        return attrs


# ====== Lesson ======
class LessonSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)

    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all())
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all(), required=False)
    teacher = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True, required=False)

    group_name = serializers.CharField(source='group.name', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)
    teacher_name = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            'id', 'company', 'branch',
            'group', 'group_name',
            'course', 'course_title',
            'teacher', 'teacher_name',
            'date', 'time', 'duration', 'classroom', 'created_at'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'group_name', 'course_title', 'teacher_name']

    def get_teacher_name(self, obj):
        if obj.teacher:
            return (f"{obj.teacher.first_name or ''} {obj.teacher.last_name or ''}".strip()
                    or obj.teacher.email)
        return None

    def validate(self, attrs):
        """
        Проверяем:
        - group/course/teacher из компании пользователя
        - если course не передан — берём из group
        - course == group.course
        - филиальная согласованность (group/course глобальные или активного филиала)
        - вызывем model.clean() на «теневом» инстансе (важно для частичных апдейтов)
        """
        request = self.context.get('request')
        company = getattr(request.user, 'company', None)
        company_id = getattr(company, 'id', None)
        target_branch = self._auto_branch()

        instance = self.instance
        group   = attrs.get('group',   getattr(instance, 'group',   None))
        course  = attrs.get('course',  getattr(instance, 'course',  None))
        teacher = attrs.get('teacher', getattr(instance, 'teacher', None))
        date    = attrs.get('date',    getattr(instance, 'date',    None))
        time    = attrs.get('time',    getattr(instance, 'time',    None))
        duration= attrs.get('duration',getattr(instance, 'duration',None))
        classroom = attrs.get('classroom', getattr(instance, 'classroom', None))

        # company checks
        if company_id and group  and group.company_id  != company_id:
            raise serializers.ValidationError({'group':  'Группа принадлежит другой компании.'})
        if company_id and course and course.company_id != company_id:
            raise serializers.ValidationError({'course': 'Курс принадлежит другой компании.'})
        if company_id and teacher and getattr(teacher, 'company_id', None) not in (None, company_id):
            # если в User хранится company_id — проверим
            if teacher.company_id != company_id:
                raise serializers.ValidationError({'teacher': 'Преподаватель принадлежит другой компании.'})

        # если курс не передали — подставим из группы
        if not course and group:
            attrs['course'] = group.course
            course = attrs['course']

        # курс урока должен совпадать с курсом группы
        if group and course and group.course_id != course.id:
            raise serializers.ValidationError({'course': 'Курс урока должен совпадать с курсом выбранной группы.'})

        # branch checks
        if target_branch is not None:
            tbid = target_branch.id
            if group and group.branch_id not in (None, tbid):
                raise serializers.ValidationError({'group': 'Группа другого филиала.'})
            if course and course.branch_id not in (None, tbid):
                raise serializers.ValidationError({'course': 'Курс другого филиала.'})
            # teacher — без ветки (User). Если у вас есть членства — добавьте проверку здесь.

        # собрать «теневой» объект и дернуть model.clean()
        shadow = Lesson(
            company=company, branch=target_branch,
            group=group, course=course, teacher=teacher,
            date=date, time=time, duration=duration, classroom=classroom
        )
        if instance:
            shadow.id = instance.id
        try:
            shadow.clean()
        except DjangoValidationError as e:
            if hasattr(e, "message_dict"):
                raise serializers.ValidationError(e.message_dict)
            if hasattr(e, "messages"):
                raise serializers.ValidationError({"detail": e.messages})
            raise serializers.ValidationError({"detail": str(e)})

        return attrs


# ====== Folder ======
class FolderSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)
    parent = serializers.PrimaryKeyRelatedField(queryset=Folder.objects.all(), allow_null=True, required=False)
    parent_name = serializers.CharField(source='parent.name', read_only=True)

    class Meta:
        model = Folder
        fields = ['id', 'company', 'branch', 'name', 'parent', 'parent_name']
        read_only_fields = ['id', 'company', 'branch', 'parent_name']
        ref_name = "EducationFolder"

    def validate_parent(self, parent):
        if parent is None:
            return parent
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        target_branch = self._auto_branch()
        if user_company_id and parent.company_id != user_company_id:
            raise serializers.ValidationError('Родительская папка принадлежит другой компании.')
        if target_branch is not None and parent.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError('Родительская папка принадлежит другому филиалу.')
        return parent


# ====== Document ======
class DocumentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)

    folder_name = serializers.CharField(source='folder.name', read_only=True)

    class Meta:
        model = Document
        fields = [
            'id', 'company', 'branch',
            'name', 'file',
            'folder', 'folder_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'updated_at', 'folder_name']
        ref_name = "EducationDocument"

    def validate_folder(self, folder):
        if folder is None:
            return folder
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        target_branch = self._auto_branch()
        if user_company_id and folder.company_id != user_company_id:
            raise serializers.ValidationError('Папка принадлежит другой компании.')
        if target_branch is not None and folder.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError('Папка принадлежит другому филиалу.')
        return folder


# ====== Attendance (общий CRUD) ======
class AttendanceSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source='company_id', read_only=True)
    branch = serializers.UUIDField(source='branch_id', read_only=True)


    class Meta:
        model = Attendance
        fields = [
            'id', 'company', 'branch',
            'lesson', 'student',
            'present', 'note', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'updated_at']
        ref_name = "EducationAttendance"

    def validate(self, attrs):
        request = self.context.get('request')
        user_company_id = getattr(getattr(request.user, 'company', None), 'id', None)
        target_branch = self._auto_branch()

        instance = self.instance
        lesson = attrs.get('lesson', getattr(instance, 'lesson', None))
        student = attrs.get('student', getattr(instance, 'student', None))

        if user_company_id and lesson and lesson.company_id != user_company_id:
            raise serializers.ValidationError({'lesson': 'Занятие принадлежит другой компании.'})
        if user_company_id and student and student.company_id != user_company_id:
            raise serializers.ValidationError({'student': 'Студент принадлежит другой компании.'})
        if lesson and student and student.group_id != lesson.group_id:
            raise serializers.ValidationError({'student': 'Студент не из группы этого занятия.'})

        if target_branch is not None:
            tbid = target_branch.id
            if lesson and lesson.branch_id not in (None, tbid):
                raise serializers.ValidationError({'lesson': 'Занятие другого филиала.'})
            if student and student.branch_id not in (None, tbid):
                raise serializers.ValidationError({'student': 'Студент другого филиала.'})

        # дернём model.clean() через «теневой» объект
        shadow = Attendance(
            company=self.context['request'].user.company,
            branch=target_branch,
            lesson=lesson,
            student=student,
            present=attrs.get('present', getattr(instance, 'present', None)),
            note=attrs.get('note', getattr(instance, 'note', None)),
        )
        if instance:
            shadow.id = instance.id
        try:
            shadow.clean()
        except DjangoValidationError as e:
            if hasattr(e, "message_dict"):
                raise serializers.ValidationError(e.message_dict)
            if hasattr(e, "messages"):
                raise serializers.ValidationError({"detail": e.messages})
            raise serializers.ValidationError({"detail": str(e)})

        return attrs


# ====== Lesson attendance snapshot (GET/PUT /lessons/{id}/attendance/) ======
class LessonAttendanceItemSerializer(serializers.Serializer):
    """Элемент снимка посещаемости урока."""
    student = serializers.UUIDField()
    present = serializers.BooleanField(allow_null=True)
    note = serializers.CharField(allow_blank=True, required=False)
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


# ====== TeacherRate ======
class TeacherRateSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.UUIDField(source="company_id", read_only=True)
    branch = serializers.UUIDField(source="branch_id", read_only=True)

    teacher = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    teacher_name = serializers.CharField(source="teacher.get_full_name", read_only=True)

    class Meta:
        model = TeacherRate
        fields = [
            "id", "company", "branch",
            "teacher", "teacher_name",
            "period", "mode", "rate",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "teacher_name", "created_at", "updated_at"]

    def validate(self, attrs):
        request = self.context["request"]
        company = request.user.company
        branch = self._auto_branch()

        teacher = attrs.get("teacher", getattr(self.instance, "teacher", None))
        period  = attrs.get("period",  getattr(self.instance, "period",  None))
        mode    = attrs.get("mode",    getattr(self.instance, "mode",    None))

        # принадлежность преподавателя компании (если у User есть company_id)
        teacher_company_id = getattr(teacher, "company_id", None)
        if teacher and teacher_company_id and teacher_company_id != getattr(company, "id", None):
            raise serializers.ValidationError({"teacher": "Преподаватель из другой компании."})

        # человекочитаемая проверка уникальности с учётом branch (как в БД)
        if teacher and period and mode:
            qs = TeacherRate.objects.filter(
                company_id=getattr(company, "id", None),
                teacher=teacher,
                period=period,
                mode=mode,
            )
            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    "non_field_errors": ["Ставка уже существует для этого преподавателя, периода и режима."]
                })

        return attrs

    def create(self, validated_data):
        # company/branch проставятся миксином
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # company/branch проставятся миксином
        return super().update(instance, validated_data)
