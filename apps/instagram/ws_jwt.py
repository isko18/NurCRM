# Backward-compatible re-export. Prefer core.ws_jwt in new code.
from core.ws_jwt import JWTAuthMiddleware

__all__ = ["JWTAuthMiddleware"]
