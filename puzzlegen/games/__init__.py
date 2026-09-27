"""Game plugins.

Every module under this package is subject to the import boundary enforced by
``tests/invariants/test_plugin_boundary.py``: no storage, no providers, no
network, no filesystem, no environment. A game receives a ContentPort and
nothing else.

In-tree games live here for convenience of development. Third-party games run
as sandboxed subprocesses and cannot import from this package at all, so the
boundary is enforced twice by two different mechanisms.
"""

__all__: list[str] = []
