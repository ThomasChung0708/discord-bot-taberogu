"""Shared URL validation for stored links and local restaurant images."""

import ipaddress
import re
from urllib.parse import urlsplit


UPLOAD_PATH_RE = re.compile(r"/uploads/restaurants/[A-Za-z0-9_-][A-Za-z0-9_.-]*\Z")


def valid_host(host: str) -> bool:
    """Accept IP literals and DNS/IDNA names, including local hostnames."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        return False
    return bool(ascii_host) and len(ascii_host) <= 253 and all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in ascii_host.split(".")
    )


def validate_url(value: str | None, *, image: bool = False) -> str | None:
    """Normalize empty values; reject executable URLs and ambiguous paths."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if image and UPLOAD_PATH_RE.fullmatch(value) and ".." not in value:
        return value
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme.lower() in {"http", "https"}
            and value.lower().startswith(("http://", "https://"))
            and parsed.netloc and parsed.hostname
            and valid_host(parsed.hostname)
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            and "\\" not in value
        )
        parsed.port  # Validate malformed/out-of-range ports too.
        if valid:
            return value
    except ValueError:
        pass
    raise ValueError("網址只允許有效的 http/https 連結；圖片另可使用安全的本機上傳路徑。")


def sanitize_url(value: str | None, *, image: bool = False) -> str | None:
    """Discard unsafe imported/legacy values without aborting the whole import."""
    try:
        return validate_url(value, image=image)
    except ValueError:
        return None
