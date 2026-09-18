"""Compatibility facade for Nana's frozen teacher introspection.

The VGC-Bench-specific implementation now lives behind ``TeacherAdapter`` so
future model families can be swapped without leaking their native action catalog
into Nana's persistent memory layers.
"""

from battle_lab.nana_teacher_adapter import inspect_light_decision

__all__ = ["inspect_light_decision"]
