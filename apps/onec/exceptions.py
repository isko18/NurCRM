class OneCAPIError(Exception):
    """Ошибка обмена с HTTP-сервисами 1С."""

    def __init__(self, message: str, *, status_code: int | None = None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    @property
    def is_business_error(self) -> bool:
        """4xx (кроме 401/408/429) — ошибка данных, ретраить бессмысленно."""
        code = self.status_code or 0
        if code in (401, 408, 429):
            return False
        return 400 <= code < 500
