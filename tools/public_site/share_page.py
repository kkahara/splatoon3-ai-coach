"""Share copy and crawler metadata for one public review link."""

from __future__ import annotations

import re
from html import escape

from public_site.errors import RequestRejected
from public_site.service import SubmissionService
from public_site.settings import PublicSettings

TITLE = "Splatoon 3 Coaching Review"
DESCRIPTION = (
    "See what happened in this match and get evidence-based coaching feedback."
)
ANALYSIS = "Evidence-based analysis of this match."

_REVIEW = re.compile(r"^review/([A-Za-z0-9_-]+)$")


def review_token(full_path: str) -> str | None:
    """Return the review token from a site path, or None."""
    match = _REVIEW.fullmatch(full_path)
    if match is None:
        return None
    return match.group(1)


def moment_line(count: int, match_seconds: int | None) -> str:
    """Short line for a finished review, such as ``3 coaching moments · 4:12 match``."""
    label = "1 coaching moment" if count == 1 else f"{count} coaching moments"
    if match_seconds is None:
        return label
    return f"{label} · {_clock(match_seconds)} match"


def review_html(
    service: SubmissionService,
    settings: PublicSettings,
    template: str,
    token: str,
) -> str | None:
    """Index HTML with preview tags for a known review. Unknown tokens stay plain."""
    try:
        view = service.view(token)
    except RequestRejected:
        return None
    count = None
    image = None
    if view.status == "complete" and view.result is not None:
        count = len(view.result.moments)
        image = _image(settings, token, view.result.moments)
    description = DESCRIPTION
    if count is not None:
        description = f"{DESCRIPTION} {moment_line(count, view.match_seconds)}"
    url = f"{settings.public_base_url.rstrip('/')}/review/{token}"
    return _document(template, url=url, description=description, image=image)


def _image(settings: PublicSettings, token: str, moments: list) -> str | None:
    for index, moment in enumerate(moments):
        if moment.frame:
            base = settings.public_base_url.rstrip("/")
            return f"{base}/api/submissions/{token}/moments/{index}/frame"
    return None


def _document(
    template: str, *, url: str, description: str, image: str | None
) -> str:
    tags = [
        _meta("property", "og:title", TITLE),
        _meta("property", "og:description", description),
        _meta("property", "og:type", "website"),
        _meta("property", "og:url", url),
        _meta("name", "twitter:card", "summary_large_image" if image else "summary"),
        _meta("name", "twitter:title", TITLE),
        _meta("name", "twitter:description", description),
    ]
    if image:
        tags.append(_meta("property", "og:image", image))
        tags.append(_meta("name", "twitter:image", image))
    block = "\n".join(tags)
    page = template
    if "<title>" in page:
        page = re.sub(
            r"<title>.*?</title>",
            f"<title>{escape(TITLE)}</title>",
            page,
            count=1,
        )
    if "</head>" in page:
        return page.replace("</head>", f"{block}\n</head>", 1)
    return f"{block}\n{page}"


def _meta(attr: str, name: str, content: str) -> str:
    return f'<meta {attr}="{name}" content="{escape(content, quote=True)}" />'


def _clock(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"
