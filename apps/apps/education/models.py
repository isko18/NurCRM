import uuid
from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator

from apps.users.models import Company, User, Branch


# ==========================
# Lead
# ==========================
class Lead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='leads', verbose_name='Организация'
    )
    # ⬇️ глобальный (NULL) или филиальный лид
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_leads',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    class SourceChoices(models.TextChoices):
        INSTAGRAM = "instagram", "Instagram"
        WHATSAPP = "whatsapp", "WhatsApp"
        TELEGRAM = "telegram", "Telegram"

    name = models.CharField("Имя", max_length=120)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    source = models.CharField(
        "Источник", max_length=50, choices=SourceChoices.choices,
        default=SourceChoices.INSTAGRAM,
    )
    note = models.TextField("Заметка", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Лид"
        verbose_name_plural = "Лиды"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "branch", "created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_source_display()})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ==========================
# Course
# ==========================
class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='courses', verbose_name='Организация'
    )
    # ⬇️ глобальный или филиальный курс
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_courses',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    title = models.CharField("Название", max_length=255)
    price_per_month = models.DecimalField("Цена/мес", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Курс"
        verbose_name_plural = "Курсы"
        ordering = ["title"]
        constraints = [
            # Уникальность названия среди курсов филиала
            models.UniqueConstraint(
                fields=("branch", "title"),
                name="uq_course_title_per_branch",
                condition=models.Q(branch__isnull=False),
            ),
            # И отдельно — глобальные курсы в рамках компании
            models.UniqueConstraint(
                fields=("company", "title"),
                name="uq_course_title_global_per_company",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "title"]),
            models.Index(fields=["company", "branch", "title"]),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})


# ==========================
# Group
# ==========================
class Group(models.Model):
    """Группа без привязки к учителю"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='groups', verbose_name='Организация'
    )
    # ⬇️ глобальная или филиальная группа
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_groups',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="groups", verbose_name="Курс"
    )
    name = models.CharField("Название группы", max_length=255)

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"
        ordering = ["course", "name"]
        constraints = [
            # имя группы уникально в рамках филиала и курса
            models.UniqueConstraint(
                fields=("branch", "course", "name"),
                name="uq_group_name_per_branch_course",
                condition=models.Q(branch__isnull=False),
            ),
            # и для глобальных групп — в рамках компании и курса
            models.UniqueConstraint(
                fields=("company", "course", "name"),
                name="uq_group_name_global_per_company_course",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "course"]),
            models.Index(fields=["company", "branch", "course"]),
        ]

    def __str__(self):
        return f"{self.name} — {self.course.title}"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        # Курс должен быть той же компании
        if self.course and self.course.company_id != self.company_id:
            raise ValidationError({'course': 'Курс принадлежит другой компании.'})
        # Если у группы есть филиал, курс должен быть глобальным или этого филиала
        if self.branch_id and self.course and self.course.branch_id not in (None, self.branch_id):
            raise ValidationError({'course': 'Курс принадлежит другому филиалу.'})


# ==========================
# Student
# ==========================
class Student(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='students', verbose_name='Организация'
    )
    # ⬇️ глобальный или филиальный студент
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_students',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Активный"
        SUSPENDED = "suspended", "Приостановлен"
        ARCHIVED = "archived", "Архивный"

    name = models.CharField("Имя", max_length=120)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=StatusChoices.choices,
        default=StatusChoices.ACTIVE,
    )
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="students", verbose_name="Группа"
    )
    discount = models.DecimalField("Скидка (сом)", max_digits=10, decimal_places=2, default=0)
    note = models.TextField("Заметка", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=False, verbose_name="Завершен/Активный")

    class Meta:
        verbose_name = "Студент"
        verbose_name_plural = "Студенты"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch", "status"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.group:
            if self.group.company_id != self.company_id:
                raise ValidationError({'group': 'Группа принадлежит другой компании.'})
            if self.branch_id and self.group.branch_id not in (None, self.branch_id):
                raise ValidationError({'group': 'Группа принадлежит другому филиалу.'})


# ==========================
# Lesson
# ==========================
class Lesson(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='lessons', verbose_name='Организация'
    )
    # ⬇️ глобальное или филиальное занятие
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_lessons',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    course = models.ForeignKey(
        Course, on_delete=models.PROTECT, related_name="lessons", verbose_name="Курс"
    )
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="lessons", verbose_name="Группа"
    )
    teacher = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lessons", verbose_name="Преподаватель"
    )
    date = models.DateField("Дата")
    time = models.TimeField("Время")
    duration = models.PositiveIntegerField("Длительность (мин)", default=90)
    classroom = models.CharField("Аудитория", max_length=255, default="Онлайн")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Занятие"
        verbose_name_plural = "Занятия"
        ordering = ["-date", "-time"]
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "date", "time"], name="unique_teacher_lesson_per_time"
            )
        ]
        indexes = [
            models.Index(fields=["company", "course"]),
            models.Index(fields=["company", "branch", "course"]),
            models.Index(fields=["company", "branch", "date"]),
        ]

    def __str__(self):
        return f"{self.group.name} — {self.date} {self.time} ({self.teacher})"

    def clean(self):
        # company согласованность
        if self.course and self.company_id != self.course.company_id:
            raise ValidationError({"course": "Курс принадлежит другой компании."})
        if self.group and self.company_id != self.group.company_id:
            raise ValidationError({"group": "Группа принадлежит другой компании."})
        # курс урока = курс группы
        if self.group_id and self.course_id and self.group.course_id != self.course_id:
            raise ValidationError({"course": "Курс урока должен совпадать с курсом выбранной группы."})
        # филиальная согласованность
        if self.branch_id:
            if self.course and self.course.branch_id not in (None, self.branch_id):
                raise ValidationError({"course": "Курс другого филиала."})
            if self.group and self.group.branch_id not in (None, self.branch_id):
                raise ValidationError({"group": "Группа другого филиала."})


# ==========================
# Folder / Document
# ==========================
class Folder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='educations_folders', verbose_name='Компания'
    )
    # ⬇️ глобальная или филиальная папка
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_folders',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField('Название папки', max_length=255)
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='children', verbose_name='Родительская папка'
    )

    class Meta:
        verbose_name = 'Папка'
        verbose_name_plural = 'Папки'
        constraints = [
            models.UniqueConstraint(
                fields=('company', 'branch', 'parent', 'name'),
                name='uq_edu_folder_company_branch_parent_name',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'parent', 'name']),
            models.Index(fields=['company', 'branch', 'parent', 'name']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.parent_id:
            if self.parent.company_id != self.company_id:
                raise ValidationError({'parent': 'Родительская папка другой компании.'})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'parent': 'Родительская папка другого филиала.'})


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="educations_documents", verbose_name="Компания"
    )
    # ⬇️ глобальный или филиальный документ
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_documents',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    name = models.CharField("Название документа", max_length=255, blank=True)
    file = models.FileField("Файл", upload_to="documents/")
    folder = models.ForeignKey(
        Folder, on_delete=models.CASCADE, related_name="documents", verbose_name="Папка"
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "branch"]),
        ]

    def __str__(self):
        return self.name or self.file.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.folder.company_id != self.company_id:
            raise ValidationError({'folder': 'Папка принадлежит другой компании.'})
        if (self.folder.branch_id or None) != (self.branch_id or None):
            raise ValidationError({'folder': 'Папка принадлежит другому филиалу.'})


# ==========================
# Attendance
# ==========================
class Attendance(models.Model):
    """Отметка посещаемости ученика на конкретном занятии."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="attendances", verbose_name="Компания"
    )
    # ⬇️ глобальная или филиальная отметка
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_attendances',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )

    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, related_name="attendances", verbose_name="Занятие"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="attendances", verbose_name="Студент"
    )
    # None = ещё не отмечали; True/False = присутствовал/отсутствовал
    present = models.BooleanField("Присутствие", null=True, blank=True)
    note = models.CharField("Примечание", max_length=255, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Посещаемость"
        verbose_name_plural = "Посещаемость"
        unique_together = (("lesson", "student"),)
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "branch"]),
            models.Index(fields=["lesson"]),
            models.Index(fields=["student"]),
        ]

    def __str__(self):
        return f"{self.student} — {self.lesson}: {self.present}"

    def clean(self):
        if self.company_id != self.lesson.company_id:
            raise ValidationError({"lesson": "Занятие другой компании."})
        if self.company_id != self.student.company_id:
            raise ValidationError({"student": "Студент другой компании."})
        if self.student.group_id != self.lesson.group_id:
            raise ValidationError({"student": "Студент не из группы этого занятия."})
        # филиальная согласованность
        if self.branch_id:
            if self.lesson.branch_id not in (None, self.branch_id):
                raise ValidationError({"lesson": "Занятие другого филиала."})
            if self.student.branch_id not in (None, self.branch_id):
                raise ValidationError({"student": "Студент другого филиала."})


# ==========================
# TeacherRate
# ==========================
class TeacherRate(models.Model):
    class Mode(models.TextChoices):
        HOUR   = "hour",   "Час"
        LESSON = "lesson", "Урок"
        MONTH  = "month",  "Месяц"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="teacher_rates", verbose_name="Компания"
    )
    # ⬇️ глобальная ставка по компании или филиальная
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='education_teacher_rates',
        verbose_name='Филиал', null=True, blank=True, db_index=True
    )
    teacher = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name="teacher_rates", verbose_name="Преподаватель"
    )

    period = models.CharField(
        "Период", max_length=7,
        validators=[RegexValidator(r"^\d{4}-(0[1-9]|1[0-2])$", "Формат периода: YYYY-MM")],
        help_text="YYYY-MM",
    )
    mode = models.CharField("Режим", max_length=10, choices=Mode.choices)
    rate = models.DecimalField(
        "Ставка", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))]
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        db_table = "education_teacher_rate"
        verbose_name = "Ставка преподавателя"
        verbose_name_plural = "Ставки преподавателей"
        ordering = ["-updated_at"]
        # Две уникальности: отдельно для филиальных и для глобальных ставок
        constraints = [
            models.UniqueConstraint(
                fields=["company", "branch", "teacher", "mode", "period"],
                name="uq_tr_comp_branch_tchr_mode_per",
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["company", "teacher", "mode", "period"],
                name="uq_tr_comp_tchr_mode_per_global",
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "teacher", "period", "mode"], name="ix_tr_comp_tchr_per_mode"),
            models.Index(fields=["company", "branch", "teacher", "period", "mode"], name="ix_tr_comp_br_per_mode"),
        ]

    def __str__(self):
        return f"{self.teacher} · {self.period} · {self.get_mode_display()} = {self.rate}"

    def clean(self):
        # согласованность компании у teacher и branch
        teacher_company_id = getattr(self.teacher, "company_id", None)
        if teacher_company_id and self.company_id and teacher_company_id != self.company_id:
            raise ValidationError({"teacher": "Преподаватель из другой компании."})
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Филиал другой компании."})

    @classmethod
    def get_for(cls, company, teacher, period: str, mode: str, branch=None):
        """
        Вернуть ставку за период. Если указан branch — ищем филиальную,
        иначе глобальную по компании.
        """
        try:
            if branch:
                return cls.objects.get(
                    company=company, teacher=teacher, period=period, mode=mode, branch=branch
                ).rate
            return cls.objects.get(
                company=company, teacher=teacher, period=period, mode=mode, branch__isnull=True
            ).rate
        except cls.DoesNotExist:
            return None
