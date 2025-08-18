import uuid
from django.db import models
from apps.users.models import Company
from django.core.exceptions import ValidationError


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

    student = models.OneToOneField(
        "Student", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lead", verbose_name="Студент"
    )

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


class Teacher(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='teachers', verbose_name='Организация'
    )
    name = models.CharField(max_length=255, verbose_name="Имя")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Телефон")
    subject = models.CharField(max_length=255, blank=True, null=True, verbose_name="Предмет")

    class Meta:
        verbose_name = "Преподаватель"
        verbose_name_plural = "Преподаватели"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.subject})" if self.subject else self.name


class Group(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='groups', verbose_name='Организация'
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="groups", verbose_name="Курс"
    )
    name = models.CharField("Название группы", max_length=255)
    teacher = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="groups", verbose_name="Основной преподаватель"
    )

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
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="lessons", verbose_name="Группа"
    )
    teacher = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
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

    def __str__(self):
        return f"{self.group.name} — {self.date} {self.time} ({self.teacher})"


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