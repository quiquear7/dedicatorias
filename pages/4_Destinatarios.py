from __future__ import annotations

from collections import defaultdict

import streamlit as st

from core import contacts as contacts_module
from core.auth import logout_button, require_login
from core.config import get_config

st.set_page_config(page_title="Destinatarios", page_icon="👥", layout="wide")
require_login()
logout_button()
st.title("👥 Destinatarios")
st.caption("Gestiona las personas para las que vas a generar dedicatorias, agrupadas por categoría (familia, amigos, trabajo, etc.).")

cfg = get_config()
if not cfg.is_storage_ready:
    st.error("Almacenamiento no configurado. Revisa tu .env o secrets.")
    st.stop()


def _refresh():
    st.rerun()


with st.expander("➕ Añadir destinatario", expanded=True):
    existing_groups = contacts_module.list_groups()
    with st.form("new_contact", clear_on_submit=True):
        col1, col2 = st.columns([2, 2])
        with col1:
            name = st.text_input("Nombre", placeholder="Ej. Enrique")
        with col2:
            if existing_groups:
                group_choice = st.selectbox(
                    "Grupo",
                    options=["— Nuevo grupo —", *existing_groups],
                    index=1 if existing_groups else 0,
                )
                if group_choice == "— Nuevo grupo —":
                    group = st.text_input("Nombre del nuevo grupo", placeholder="Ej. Amigos Madrid")
                else:
                    group = group_choice
            else:
                group = st.text_input("Grupo", placeholder="Ej. Familia")
        notes = st.text_input("Notas (opcional)", placeholder="Cumpleaños, gustos, etc.")
        submitted = st.form_submit_button("Guardar destinatario", type="primary")
        if submitted:
            try:
                contacts_module.create_contact(name=name, group=group, notes=notes)
                st.success(f"Destinatario «{name}» añadido al grupo «{group}».")
                _refresh()
            except ValueError as e:
                st.error(str(e))

st.divider()
st.subheader("Listado")

contacts = contacts_module.list_contacts()
if not contacts:
    st.info("Todavía no hay destinatarios. Añade el primero arriba.")
else:
    by_group: dict[str, list] = defaultdict(list)
    for c in contacts:
        by_group[c.group or "(sin grupo)"].append(c)

    for group_name in sorted(by_group.keys(), key=str.lower):
        group_contacts = by_group[group_name]
        with st.expander(f"📁 {group_name} · {len(group_contacts)} personas", expanded=True):
            for contact in group_contacts:
                cols = st.columns([3, 3, 4, 1, 1])
                with cols[0]:
                    new_name = st.text_input(
                        "Nombre",
                        value=contact.name,
                        key=f"name_{contact.id}",
                        label_visibility="collapsed",
                    )
                with cols[1]:
                    new_group = st.text_input(
                        "Grupo",
                        value=contact.group,
                        key=f"group_{contact.id}",
                        label_visibility="collapsed",
                    )
                with cols[2]:
                    new_notes = st.text_input(
                        "Notas",
                        value=contact.notes or "",
                        key=f"notes_{contact.id}",
                        label_visibility="collapsed",
                        placeholder="Notas",
                    )
                with cols[3]:
                    if st.button("💾", key=f"save_{contact.id}", help="Guardar cambios"):
                        try:
                            contacts_module.update_contact(
                                contact.id, new_name, new_group, new_notes
                            )
                            st.toast("Guardado")
                            _refresh()
                        except (ValueError, KeyError) as e:
                            st.error(str(e))
                with cols[4]:
                    if st.button("🗑️", key=f"del_{contact.id}", help="Eliminar"):
                        contacts_module.delete_contact(contact.id)
                        st.toast(f"Eliminado «{contact.name}».")
                        _refresh()
