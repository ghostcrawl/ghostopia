"""authoring/ — the plug-and-play authoring SDK.

Unlike ``builtin/``, this package is NOT auto-discovered on import — it holds the one-file
behavior TEMPLATE an author copies (``template_behavior.py``) plus ``AUTHORING.md``. A real
behavior is authored by copying the template, renaming it, and dropping it under ``builtin/``
where the auto-discovery loader registers it with zero renderer/core-loop edit.
"""

from __future__ import annotations

__all__: list[str] = []
