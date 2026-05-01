from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional

from core.config import get_storage
from core.models import Contact

INDEX_PATH = "contacts/_index.json"


def _load_index() -> Dict[str, dict]:
    storage = get_storage()
    if not storage.exists(INDEX_PATH):
        return {}
    raw = storage.get(INDEX_PATH).decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _save_index(index: Dict[str, dict]) -> None:
    storage = get_storage()
    payload = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
    storage.put(INDEX_PATH, payload)


def list_contacts() -> List[Contact]:
    index = _load_index()
    contacts = [Contact.from_dict(item) for item in index.values()]
    contacts.sort(key=lambda c: (c.group.lower(), c.name.lower()))
    return contacts


def get_contact(contact_id: str) -> Optional[Contact]:
    index = _load_index()
    raw = index.get(contact_id)
    return Contact.from_dict(raw) if raw else None


def save_contact(contact: Contact) -> Contact:
    index = _load_index()
    index[contact.id] = contact.to_dict()
    _save_index(index)
    return contact


def create_contact(name: str, group: str, notes: Optional[str] = None) -> Contact:
    name = name.strip()
    group = group.strip()
    if not name:
        raise ValueError("El nombre no puede estar vacío.")
    contact = Contact(id=str(uuid.uuid4()), name=name, group=group, notes=notes or None)
    return save_contact(contact)


def update_contact(contact_id: str, name: str, group: str, notes: Optional[str] = None) -> Contact:
    existing = get_contact(contact_id)
    if not existing:
        raise KeyError(f"Contacto no encontrado: {contact_id}")
    existing.name = name.strip()
    existing.group = group.strip()
    existing.notes = (notes or "").strip() or None
    return save_contact(existing)


def delete_contact(contact_id: str) -> bool:
    index = _load_index()
    if contact_id not in index:
        return False
    del index[contact_id]
    _save_index(index)
    return True


def list_groups() -> List[str]:
    groups = {c.group for c in list_contacts() if c.group}
    return sorted(groups, key=str.lower)


def find_or_create(name: str, group: str) -> Contact:
    name = name.strip()
    group = group.strip()
    for contact in list_contacts():
        if contact.name.lower() == name.lower() and contact.group.lower() == group.lower():
            return contact
    return create_contact(name, group)
