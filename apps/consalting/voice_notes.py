"""Preparation of browser-recorded voice notes for WhatsApp/Wazzup."""
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


VOICE_TYPES = {"voice", "audio", "ptt"}


def is_voice(media_type="", content_type="", filename=""):
    media_type = (media_type or "").lower().strip()
    content_type = (content_type or "").lower().strip()
    suffix = Path(filename or "").suffix.lower()
    return media_type in VOICE_TYPES or content_type.startswith("audio/") or suffix in {".webm", ".ogg", ".opus", ".m4a", ".mp3", ".wav"}


def _to_ogg(raw: bytes) -> bytes | None:
    """Return mono OGG/Opus or None if transcoding cannot be performed."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory(prefix="nurcrm-voice-") as temp_dir:
        source = Path(temp_dir) / "source"
        target = Path(temp_dir) / "voice.ogg"
        source.write_bytes(raw)
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", str(source), "-c:a", "libopus", "-b:a", "32k", "-ar", "16000", "-ac", "1", "-f", "ogg", str(target)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode or not target.exists() or not target.stat().st_size:
            return None
        return target.read_bytes()


def transcode_voice_bytes(raw: bytes) -> str | None:
    encoded = _to_ogg(raw)
    if encoded is None:
        return None
    name = f"wazzup/voice/{uuid.uuid4().hex}.ogg"
    return default_storage.save(name, ContentFile(encoded))


def transcode_local_voice_uri(content_uri: str) -> str | None:
    """Transcode an already uploaded local media URL, returning its storage path."""
    if not content_uri:
        return None
    path = urlparse(content_uri).path
    media_url = (getattr(settings, "MEDIA_URL", "/media/") or "/media/").rstrip("/") + "/"
    if not path.startswith(media_url):
        return None
    storage_name = path[len(media_url):].lstrip("/")
    if not storage_name or not default_storage.exists(storage_name):
        return None
    try:
        with default_storage.open(storage_name, "rb") as source:
            return transcode_voice_bytes(source.read())
    except OSError:
        return None


def public_uri(request, storage_name: str) -> str:
    relative_url = f"{(getattr(settings, 'MEDIA_URL', '/media/') or '/media/').rstrip('/')}/{storage_name.lstrip('/')}"
    return request.build_absolute_uri(relative_url)
