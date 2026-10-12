"""Turning a client's upload into a file that is safe to serve.

Uploads are served straight from disk (nginx `/media/`, or Django in development), and the server picks the
Content-Type from the file's *extension*. A client-chosen name such as `photo.html` around real image bytes would
therefore be served as a web page from the API's own domain. So nothing here keeps the client's name: images are
opened with Pillow and named after the format Pillow *detected*; other files get the extension of an allow-listed
content type. The client's name is only ever kept as display text by the caller.
"""

import uuid
from io import BytesIO

from django.core.files.base import ContentFile
from ninja.errors import HttpError
from PIL import Image, UnidentifiedImageError

IMAGE_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


def safe_image(upload, *, max_bytes: int, formats=("JPEG", "PNG", "WEBP", "GIF"), label: str = "Each image") -> ContentFile:
    """The upload as a ContentFile named `<random>.<real extension>`, or 422 if it is not an allowed image."""
    if upload.size > max_bytes:
        raise HttpError(422, f"{label} must be {max_bytes // (1024 * 1024)} MB or smaller")
    data = upload.read()
    try:
        image = Image.open(BytesIO(data))
        found = image.format
        image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise HttpError(422, f"{getattr(upload, 'name', 'The file')} is not an image") from None
    if found not in formats:
        raise HttpError(422, f"Images must be {', '.join(IMAGE_EXTENSIONS[f].upper() for f in formats)}")
    return ContentFile(data, name=f"{uuid.uuid4().hex}.{IMAGE_EXTENSIONS[found]}")


def safe_name(content_type: str, allowed: dict) -> str:
    """A random file name with the extension that `allowed` ({content_type: extension}) gives this content type."""
    if content_type not in allowed:
        raise HttpError(422, f"Unsupported file type: {content_type}")
    return f"{uuid.uuid4().hex}.{allowed[content_type]}"
