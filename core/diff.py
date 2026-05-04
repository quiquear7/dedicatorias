from __future__ import annotations

import difflib
import html
import re
from typing import List


_WORD_RE = re.compile(r"\s+|\S+")


def _tokenize(text: str) -> List[str]:
    return [m.group(0) for m in _WORD_RE.finditer(text)]


def html_diff(a: str, b: str) -> str:
    """Devuelve dos cadenas HTML (una por columna) con los cambios resaltados.

    - Texto eliminado en `a` aparece en rojo.
    - Texto añadido en `b` aparece en verde.
    - Texto común aparece sin formato.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    matcher = difflib.SequenceMatcher(a=tokens_a, b=tokens_b, autojunk=False)

    left_parts: List[str] = []
    right_parts: List[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        a_chunk = "".join(tokens_a[i1:i2])
        b_chunk = "".join(tokens_b[j1:j2])
        if tag == "equal":
            left_parts.append(html.escape(a_chunk))
            right_parts.append(html.escape(b_chunk))
        elif tag == "delete":
            left_parts.append(_wrap_del(a_chunk))
        elif tag == "insert":
            right_parts.append(_wrap_ins(b_chunk))
        elif tag == "replace":
            left_parts.append(_wrap_del(a_chunk))
            right_parts.append(_wrap_ins(b_chunk))
    left_html = _wrap_block("".join(left_parts))
    right_html = _wrap_block("".join(right_parts))
    return left_html, right_html  # type: ignore[return-value]


def _wrap_del(text: str) -> str:
    return f'<span style="background-color:#ffd6d6;text-decoration:line-through;color:#a40000;">{html.escape(text)}</span>'


def _wrap_ins(text: str) -> str:
    return f'<span style="background-color:#d6ffd6;color:#0a6900;">{html.escape(text)}</span>'


def _wrap_block(inner: str) -> str:
    return (
        '<div style="white-space:pre-wrap;font-family:ui-sans-serif,system-ui;'
        'line-height:1.5;padding:0.6em 0.9em;border:1px solid #ddd;border-radius:6px;'
        f'background:#fafafa;color:#222;">{inner}</div>'
    )
