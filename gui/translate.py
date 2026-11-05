"""Translate a page's controls as they are built: every text a control shows goes through the chosen language
pack (pokeuds.app.i18n). English needs nothing, so with no pack this returns at once.

The text lives in ``value`` (Text), ``content`` (a button's label), ``text`` (a dropdown option), ``label``,
``hint_text``, ``helper``, ``error`` and ``tooltip``; children in any field holding controls. A TextField's or
Dropdown's ``value`` is the player's input and is never touched.
"""

from __future__ import annotations

import dataclasses

import flet as ft

from pokeuds.app import i18n

TEXT_FIELDS = ("content", "text", "label", "hint_text", "helper", "error", "tooltip", "semantics_label")


def translate(control):
    catalog = i18n.current()
    if not catalog:
        return control
    _walk(control, catalog, set())
    return control


def _walk(obj, catalog, seen: set) -> None:
    if id(obj) in seen or not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        return
    seen.add(id(obj))
    for f in dataclasses.fields(obj):
        try:
            value = getattr(obj, f.name)
        except Exception:
            continue
        if isinstance(value, str):
            if f.name in TEXT_FIELDS or (f.name == "value" and isinstance(obj, ft.Text)):
                translated = catalog.tr(value)
                if translated != value:
                    try:
                        setattr(obj, f.name, translated)
                    except Exception:
                        pass
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item, catalog, seen)
        elif f.name not in ("parent", "page", "data") and dataclasses.is_dataclass(value):
            _walk(value, catalog, seen)
