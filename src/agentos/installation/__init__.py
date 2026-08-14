"""Where Orin lives on a machine, and where it is allowed to write.

This package is deliberately a leaf: it imports nothing from the rest of Orin so
that both the launcher and the core services can depend on it without creating a
cycle. It answers two separate questions that the repository used to conflate:

* ``OrinPaths``     — where *mutable* state belongs (data, logs, cache, run state)
* ``RuntimeProfile``— where the *immutable* runtime lives (code, built frontend,
  migrations) and how to spawn another copy of Orin

Keeping them apart is what makes a future ``orin update`` possible: the
installation directory can be replaced wholesale because nothing the user cares
about was ever written inside it.
"""

from .paths import OrinPaths, orin_paths, reset_cached_paths
from .profile import RuntimeProfile, reset_cached_profile, runtime_profile
from .versions import read_installation_status, remove_installed_version

__all__ = [
    "OrinPaths",
    "RuntimeProfile",
    "orin_paths",
    "reset_cached_paths",
    "reset_cached_profile",
    "runtime_profile",
    "read_installation_status",
    "remove_installed_version",
]
