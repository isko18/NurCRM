from django.db import models


class ClientRelease(models.Model):
    """
    Версия десктопного .exe-клиента NurCRM для автообновления.

    Хранится в БД (а не в settings.py), чтобы загружать новую версию через
    POST /api/version/ без перезапуска сервера. В норме держим только последнюю
    запись — при загрузке новой версии предыдущая удаляется вместе с файлом.
    """

    version = models.CharField("Версия", max_length=50)
    zip_file = models.FileField("ZIP-архив клиента", upload_to="client-releases/")
    release_notes = models.TextField("Список изменений", blank=True, default="")
    created_at = models.DateTimeField("Загружено", auto_now_add=True)

    class Meta:
        verbose_name = "Релиз клиента"
        verbose_name_plural = "Релизы клиента"
        ordering = ["-created_at"]

    def __str__(self):
        return f"NurCRM {self.version}"
