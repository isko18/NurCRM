import uuid
from django.db import models
from apps.users.models import Company, User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from decimal import Decimal

class Lead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='leads', verbose_name='Организация'
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

    def __str__(self):
        return f"{self.name} ({self.get_source_display()})"


class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='courses', verbose_name='Организация'
    )
    title = models.CharField("Название", max_length=255)
    price_per_month = models.DecimalField("Цена/мес", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Курс"
        verbose_name_plural = "Курсы"
        ordering = ["title"]

    def __str__(self):
        return self.title


class Group(models.Model):
    """Группа без привязки к учителю"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='groups', verbose_name='Организация'
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="groups", verbose_name="Курс"
    )
    name = models.CharField("Название группы", max_length=255)

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"
        ordering = ["course", "name"]

    def __str__(self):
        return f"{self.name} — {self.course.title}"


class Student(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='students', verbose_name='Организация'
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

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class Lesson(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='lessons', verbose_name='Организация'
    )

    # НОВОЕ: прямое FK на курс
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
            models.Index(fields=["company", "course"]),   # удобно фильтровать по курсу
        ]

    def __str__(self):
        return f"{self.group.name} — {self.date} {self.time} ({self.teacher})"

    def clean(self):
        # компания везде одна
        if self.course and self.company_id != self.course.company_id:
            raise ValidationError({"course": "Курс принадлежит другой компании."})
        if self.group and self.company_id != self.group.company_id:
            raise ValidationError({"group": "Группа принадлежит другой компании."})
        # курс урока должен совпадать с курсом группы
        if self.group_id and self.course_id and self.group.course_id != self.course_id:
            raise ValidationError({"course": "Курс урока должен совпадать с курсом выбранной группы."})


class Folder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='educations_folders', verbose_name='Компания'
    )
    name = models.CharField('Название папки', max_length=255)
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='children', verbose_name='Родительская папка'
    )

    class Meta:
        verbose_name = 'Папка'
        verbose_name_plural = 'Папки'
        unique_together = (('company', 'parent', 'name'),)
        indexes = [models.Index(fields=['company', 'parent', 'name'])]

    def __str__(self):
        return self.name


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="educations_documents", verbose_name="Компания"
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
        indexes = [models.Index(fields=["company"])]

    def __str__(self):
        return self.name or self.file.name

    def clean(self):
        folder_company_id = getattr(self.folder, 'company_id', None)
        if folder_company_id and self.company_id and folder_company_id != self.company_id:
            raise ValidationError({'folder': 'Папка принадлежит другой компании.'})


class Attendance(models.Model):
    """Отметка посещаемости ученика на конкретном занятии."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="attendances", verbose_name="Компания"
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
            models.Index(fields=["lesson"]),
            models.Index(fields=["student"]),
        ]

    def __str__(self):
        return f"{self.student} — {self.lesson}: {self.present}"

    def clean(self):
        # компания везде одна
        if self.company_id != self.lesson.company_id:
            raise ValidationError({"lesson": "Занятие другой компании."})
        if self.company_id != self.student.company_id:
            raise ValidationError({"student": "Студент другой компании."})
        # ученик должен быть из группы занятия
        if self.student.group_id != self.lesson.group_id:
            raise ValidationError({"student": "Студент не из группы этого занятия."})
        
class TeacherRate(models.Model):
    class Mode(models.TextChoices):
        HOUR   = "hour",   "Час"
        LESSON = "lesson", "Урок"
        MONTH  = "month",  "Месяц"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE,
                                related_name="teacher_rates", verbose_name="Компания")
    teacher = models.ForeignKey(User, on_delete=models.CASCADE,
                                related_name="teacher_rates", verbose_name="Преподаватель")

    period = models.CharField(
        "Период", max_length=7,
        validators=[RegexValidator(r"^\d{4}-(0[1-9]|1[0-2])$", "Формат периода: YYYY-MM")],
        help_text="YYYY-MM",
    )
    mode = models.CharField("Режим", max_length=10, choices=Mode.choices)
    rate = models.DecimalField("Ставка", max_digits=12, decimal_places=2,
                               validators=[MinValueValidator(Decimal("0"))])

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        db_table = "education_teacher_rate"
        verbose_name = "Ставка преподавателя"
        verbose_name_plural = "Ставки преподавателей"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "teacher", "mode", "period"],
                name="uniq_company_teacher_mode_period",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "teacher", "period", "mode"],
                         name="idx_tr_company_teacher_period_mode"),
        ]

    def __str__(self):
        return f"{self.teacher} · {self.period} · {self.get_mode_display()} = {self.rate}"

    def clean(self):
        # согласованность компании
        if self.teacher_id and self.company_id:
            # если в User есть поле company — проверим
            teacher_company_id = getattr(self.teacher, "company_id", None)
            if teacher_company_id and teacher_company_id != self.company_id:
                raise ValidationError({"teacher": "Преподаватель из другой компании."})

    @classmethod
    def get_for(cls, company, teacher, period: str, mode: str):
        """
        Удобный помощник: вернуть ставку за период (точное совпадение).
        period: 'YYYY-MM'
        """
        try:
            return cls.objects.get(company=company, teacher=teacher, period=period, mode=mode).rate
        except cls.DoesNotExist:
            return None