"""Pydantic model for a consultant profile CV (spec §6.1).

Pydantic checks that JSON data has the right fields and types, and raises a
clear error if something is missing or wrong.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Experience(BaseModel):
    company: str
    role: str
    start: str
    end: str
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    degree: str
    institution: str
    year: str


class Contact(BaseModel):
    email: str
    phone: str
    location: str


class ProfileCV(BaseModel):
    name: str
    headline: str
    total_years_experience: float
    summary: str
    skills: list[str]
    experience: list[Experience]
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    contact: Contact
