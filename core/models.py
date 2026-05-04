from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Zone:
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Zone":
        return cls(
            x_mm=float(data["x_mm"]),
            y_mm=float(data["y_mm"]),
            width_mm=float(data["width_mm"]),
            height_mm=float(data["height_mm"]),
        )


@dataclass
class TextStyle:
    font_family: str = "Helvetica"
    font_size_pt: float = 14.0
    color_hex: str = "#000000"
    align: Literal["left", "center", "right"] = "center"
    line_height: float = 1.3
    italic: bool = False
    bold: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TextStyle":
        return cls(
            font_family=data.get("font_family", "Helvetica"),
            font_size_pt=float(data.get("font_size_pt", 14.0)),
            color_hex=data.get("color_hex", "#000000"),
            align=data.get("align", "center"),
            line_height=float(data.get("line_height", 1.3)),
            italic=bool(data.get("italic", False)),
            bold=bool(data.get("bold", False)),
        )


@dataclass
class Template:
    id: str
    name: str
    source_path: str
    source_type: Literal["image", "pdf"]
    width_mm: float
    height_mm: float
    text_zone: Zone
    text_style: TextStyle
    name_zone: Optional[Zone] = None
    name_style: Optional[TextStyle] = None
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_path": self.source_path,
            "source_type": self.source_type,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "text_zone": self.text_zone.to_dict(),
            "text_style": self.text_style.to_dict(),
            "name_zone": self.name_zone.to_dict() if self.name_zone else None,
            "name_style": self.name_style.to_dict() if self.name_style else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Template":
        return cls(
            id=data["id"],
            name=data["name"],
            source_path=data["source_path"],
            source_type=data["source_type"],
            width_mm=float(data["width_mm"]),
            height_mm=float(data["height_mm"]),
            text_zone=Zone.from_dict(data["text_zone"]),
            text_style=TextStyle.from_dict(data["text_style"]),
            name_zone=Zone.from_dict(data["name_zone"]) if data.get("name_zone") else None,
            name_style=TextStyle.from_dict(data["name_style"]) if data.get("name_style") else None,
            created_at=data.get("created_at", now_iso()),
        )


@dataclass
class Contact:
    id: str
    name: str
    group: str
    notes: Optional[str] = None
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Contact":
        return cls(
            id=data["id"],
            name=data["name"],
            group=data["group"],
            notes=data.get("notes"),
            created_at=data.get("created_at", now_iso()),
        )

    @property
    def label(self) -> str:
        if self.group:
            return f"{self.name} — {self.group}"
        return self.name


@dataclass
class Dedication:
    id: str
    recipient_name: str
    recipient_group: str
    input_mode: Literal["audio", "text"]
    raw_input: str
    corrected_text: str
    final_text: str
    status: Literal["pending", "rendered"] = "pending"
    template_id: Optional[str] = None
    template_snapshot: Optional[Dict[str, Any]] = None
    card_pdf_path: Optional[str] = None
    card_png_path: Optional[str] = None
    contact_id: Optional[str] = None
    audio_path: Optional[str] = None
    is_generic: bool = False
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    rendered_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dedication":
        # Compatibilidad con el formato antiguo (sin status): si tiene paths de render se considera "rendered"
        if "status" in data:
            status = data["status"]
        else:
            status = "rendered" if data.get("card_pdf_path") else "pending"
        return cls(
            id=data["id"],
            recipient_name=data["recipient_name"],
            recipient_group=data.get("recipient_group", ""),
            input_mode=data.get("input_mode", "text"),
            raw_input=data.get("raw_input", ""),
            corrected_text=data.get("corrected_text", ""),
            final_text=data["final_text"],
            status=status,
            template_id=data.get("template_id"),
            template_snapshot=data.get("template_snapshot"),
            card_pdf_path=data.get("card_pdf_path"),
            card_png_path=data.get("card_png_path"),
            contact_id=data.get("contact_id"),
            audio_path=data.get("audio_path"),
            is_generic=bool(data.get("is_generic", False)),
            tags=list(data.get("tags", [])),
            created_at=data.get("created_at", now_iso()),
            rendered_at=data.get("rendered_at"),
        )

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"
