"""Upload validation and serving. Files never touch disk and are never executed (OWASP A03/A04/A05).

Images are decoded by Pillow and *re-encoded*, which drops metadata (GPS location in phone photos)
and anything that is not pixel data. Videos are accepted only if their magic bytes say MP4/WebM.
"""

import io
import re

from flask import Blueprint, Response, abort, current_app, request
from PIL import Image, ImageOps, UnidentifiedImageError

from .extensions import db
from .models import Media

bp = Blueprint("media", __name__)

# Refuse decompression bombs: 40 megapixels is more than any phone camera.
Image.MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "MPO"}
SERVE_TYPES = {"image/webp", "image/png", "video/mp4", "video/webm"}
EXT = {"image/webp": "webp", "image/png": "png", "video/mp4": "mp4", "video/webm": "webm"}


class UploadError(ValueError):
    pass


def save_image(file_storage, max_side: int = 1600, fmt: str = "WEBP") -> Media:
    raw = file_storage.read()
    if not raw:
        raise UploadError("The file is empty.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(raw))
        if img.format not in ALLOWED_IMAGE_FORMATS:
            raise UploadError("Use a JPG, PNG, WebP or GIF image.")
        img = ImageOps.exif_transpose(img)
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise UploadError("That file isn't a readable image.") from exc
    has_alpha = img.mode in ("RGBA", "LA", "P")
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    if fmt == "PNG":
        img.save(out, "PNG", optimize=True)
        ctype = "image/png"
    else:
        img.save(out, "WEBP", quality=82, method=4)
        ctype = "image/webp"
    data = out.getvalue()
    media = Media(content_type=ctype, data=data, size=len(data), width=img.width, height=img.height)
    db.session.add(media)
    db.session.flush()
    return media


def save_video(file_storage) -> Media:
    data = file_storage.read(current_app.config["MAX_VIDEO_BYTES"] + 1)
    if len(data) > current_app.config["MAX_VIDEO_BYTES"]:
        raise UploadError("Videos must be 15 MB or smaller. Try a shorter clip or compress it.")
    if len(data) > 12 and data[4:8] == b"ftyp":
        ctype = "video/mp4"
    elif data[:4] == b"\x1a\x45\xdf\xa3":
        ctype = "video/webm"
    else:
        raise UploadError("Use an MP4 or WebM video.")
    media = Media(content_type=ctype, data=data, size=len(data))
    db.session.add(media)
    db.session.flush()
    return media


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@bp.route("/media/<token>.<ext>")
def serve(token, ext):
    if not re.fullmatch(r"[0-9a-f]{24}", token):
        abort(404)
    m = Media.query.filter_by(token=token).first()
    if not m or m.content_type not in SERVE_TYPES or EXT[m.content_type] != ext:
        abort(404)
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "Accept-Ranges": "bytes",
        "Content-Disposition": "inline",
        # Media can be shown by our own pages only.
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    data = m.data
    rng = request.headers.get("Range", "")
    match = _RANGE_RE.match(rng) if rng else None
    if match and (match.group(1) or match.group(2)):
        size = len(data)
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))
            end = size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(data[start : end + 1], status=206, mimetype=m.content_type, headers=headers)
    return Response(data, mimetype=m.content_type, headers=headers)
