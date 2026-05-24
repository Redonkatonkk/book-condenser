from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChapterStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class JobStatus(str, Enum):
    queued = "queued"
    analyzing = "analyzing"
    ready = "ready"
    condensing = "condensing"
    building = "building"
    completed = "completed"
    failed = "failed"


@dataclass
class Chapter:
    id: str
    title: str
    text: str
    order: int
    original_count: int


@dataclass
class ImageAsset:
    id: str
    filename: str
    media_type: str
    content: bytes
    alt: str = ""


@dataclass
class IntegrityReport:
    file_type: str
    is_complete: bool
    warnings: list[str] = field(default_factory=list)
    chapter_count: int = 0
    total_count: int = 0
    empty_chapter_count: int = 0


@dataclass
class ParsedBook:
    title: str
    author: str
    chapters: list[Chapter]
    integrity: IntegrityReport
    images: list[ImageAsset] = field(default_factory=list)
