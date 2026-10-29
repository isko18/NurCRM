class EkassaConfigurationError(Exception):
    """Нет настроек или интеграция выключена."""


class EkassaAPIError(Exception):
    def __init__(self, message: str, *, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
