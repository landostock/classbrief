from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractedSection:
    label: str
    text: str
    page: int | None = None


@dataclass
class ExtractedDocument:
    source: str
    sections: list[ExtractedSection] = field(default_factory=list)
    status: str = "ready"
    truncated: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(f"[{part.label}]\n{part.text}" for part in self.sections if part.text.strip())


class DocumentExtractor(ABC):
    @abstractmethod
    def extract(self, path: Path, *, max_chars: int | None = None) -> ExtractedDocument: ...


class UnsupportedDocumentError(ValueError):
    pass
