"""URL sanitizing and exclusion rules. Pure functions, no I/O."""

from __future__ import annotations

from urllib.parse import urlsplit

from logger.config import LoggerConfig


def sanitize_url(url: str | None) -> str | None:
    """Reduce a URL to its bare domain (lowercase, no ``www.``, port, userinfo, path or query).

    Returns None for anything that is not an http(s) URL with a host.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return host[4:] if host.startswith("www.") else host


def domain_matches(domain: str, suffix: str) -> bool:
    return domain == suffix or domain.endswith("." + suffix)


def is_excluded(bundle_id: str | None, domain: str | None, title: str | None, cfg: LoggerConfig) -> bool:
    if bundle_id and bundle_id in cfg.exclude_bundle_ids:
        return True
    if domain:
        if any(domain_matches(domain, s) for s in cfg.exclude_domains):
            return True
        if any(k in domain for k in cfg.exclude_domain_keywords):
            return True
    if title and any(p.search(title) for p in cfg.exclude_title_patterns):
        return True
    return False
