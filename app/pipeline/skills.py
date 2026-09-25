"""Skill-name normalization and "does this text mention skill X?" helpers.

Why: job posts and CVs write the same skill in different ways ("Postgres",
"PostgreSQL", "postgres db"). We turn every spelling into one canonical,
lowercase name so they can be compared.

To teach the tool a new synonym, add a line to SYNONYMS below:
    "spelling people use": "canonical name",
"""

from __future__ import annotations

import re
from functools import lru_cache

SYNONYMS: dict[str, str] = {
    "js": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "python3": "python",
    "python 3": "python",
    "golang": "go",
    "c sharp": "c#",
    "node": "node.js",
    "nodejs": "node.js",
    "node js": "node.js",
    "react.js": "react",
    "reactjs": "react",
    "react js": "react",
    "nextjs": "next.js",
    "next js": "next.js",
    "vue": "vue.js",
    "vuejs": "vue.js",
    "drf": "django rest framework",
    "tailwind": "tailwind css",
    "tailwindcss": "tailwind css",
    "spring": "spring boot",
    "html5": "html",
    "css3": "css",
    "postgres": "postgresql",
    "postgre sql": "postgresql",
    "postgres db": "postgresql",
    "mongo": "mongodb",
    "ms sql": "sql server",
    "mssql": "sql server",
    "amazon web services": "aws",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "microsoft azure": "azure",
    "k8s": "kubernetes",
    "cicd": "ci/cd",
    "ci cd": "ci/cd",
    "ci/cd pipelines": "ci/cd",
    "continuous integration": "ci/cd",
    "gitlab ci": "ci/cd",
    "github actions": "ci/cd",
    "iac": "infrastructure as code",
    "rest apis": "rest api",
    "restful api": "rest api",
    "restful apis": "rest api",
    "rest api design": "rest api",
    "aws certified": "aws certification",
    "linux administration": "linux",
    "linux admin": "linux",
    "shell scripting": "bash",
    "bash scripting": "bash",
    "ml": "machine learning",
    "wcag": "accessibility",
    "web accessibility": "accessibility",
    "a11y": "accessibility",
    "react testing library": "testing library",
}


def normalize_skill(name: str) -> str:
    """Lowercase, trim, collapse spaces, then map through SYNONYMS."""
    cleaned = re.sub(r"\s+", " ", name.strip().lower()).strip(" .,;:")
    return SYNONYMS.get(cleaned, cleaned)


def normalize_skills(names: list[str]) -> list[str]:
    """Normalize a list and drop duplicates/empties, keeping the original order."""
    seen: dict[str, None] = {}
    for name in names:
        normalized = normalize_skill(name)
        if normalized:
            seen.setdefault(normalized, None)
    return list(seen)


@lru_cache
def aliases_for(canonical: str) -> tuple[str, ...]:
    """All spellings that mean `canonical` (including itself)."""
    return (canonical, *(alias for alias, target in SYNONYMS.items() if target == canonical))


@lru_cache(maxsize=4096)
def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def text_mentions(text: str, skill: str) -> bool:
    """True if `text` mentions `skill` (any spelling), case-insensitive, whole words only."""
    lowered = text.lower()
    return any(_pattern(alias).search(lowered) for alias in aliases_for(normalize_skill(skill)))


def find_mentions(text: str, skill: str) -> list[tuple[int, int]]:
    """(start, end) positions of every mention of `skill` in `text` (used for highlighting)."""
    lowered = text.lower()
    return [m.span() for alias in aliases_for(normalize_skill(skill)) for m in _pattern(alias).finditer(lowered)]
