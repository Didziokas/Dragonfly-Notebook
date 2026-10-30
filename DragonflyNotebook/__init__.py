r"""The DragonflyNotebook package.

The plugin class is re-exported the way the app's plugin manager and the
verification suites see it — ``DragonflyNotebook.DragonflyNotebook`` — in
TWO shapes, because round 8 splits the plugin into two processes and the
two hosts need different import contracts:

* Dragonfly itself (and every ORS-capable host: the tier-2/3 headless
  suites) gets the CANONICAL eager re-export — the one-line
  ``from .DragonflyNotebook import DragonflyNotebook`` the ``__init__`` of
  all 158 shipped ORS plugins carries. It is load-bearing, not style: the
  user-plugin loader (ORSServiceClass/OrsPlugin/
  allCurrentUserPythonPlugins.py) star-imports every plugin folder
  (``exec('from ... import *')``) and the platform then registers whatever
  the star-import bound by evaluating ``DragonflyNotebook.getDescriptor()``
  — the class must already sit in this package's ``__dict__`` when that
  happens.
* The standalone notebook application process (``notebook_app.py``, the
  round-8 process separation) imports this package's sibling submodules
  (``notebook_bridge_protocol``, ``dfnb_client``, ...) in a process that
  must never load the ORS glue (``DragonflyNotebook.py`` imports ORS and
  the OrsLibraries logger). It marks itself ``DFNB_ORS_FREE_APP=1``
  BEFORE any package import, and the guard below skips the eager
  re-export by that marker — process identity, not ORS importability: a
  broken-but-findable ORS on the app's path must not crash the app at
  package import, and a fully importable one must never silently load
  the glue there (the round-5-7 crash class reborn). What the fallback
  buys in such a host: the package and its sibling submodules stay
  importable ORS-free. The PEP 562 ``__getattr__`` can NOT resolve the
  class there — the glue needs OrsLibraries, which an ORS-free host
  cannot import — so a ``from ...DragonflyNotebook import
  DragonflyNotebook`` (or a star-import) in an ORS-free host fails
  LOUDLY with ModuleNotFoundError, never silently binding the wrong
  thing.

Round 8 shipped the LAZY-ONLY shape (``__all__`` + ``__getattr__``) and
broke registration: the lazy import of the submodule plants
``pkg.__dict__['DragonflyNotebook'] = <submodule>`` (PEP 562
``__getattr__`` only fires on a ``__dict__`` miss), so the loader's
star-import bound the SUBMODULE — its ``getDescriptor()`` registration
eval raised AttributeError, OrsPluginManager logged ``Descriptor not found
for ...Plugins\DragonflyNotebook``, and the plugin silently never
  registered (no Utilities menu item; the 2026-10-15 install's
  Application log at 14:34:08, probe-confirmed). The guarded eager
shape gives the loader the class without giving up the ORS-free app
process.
"""

import importlib.util
import os
import platform
import sys

__all__ = ['DragonflyNotebook']

# Round 13's window-spam fix, part 1 — this runs BEFORE the eager re-export
# below (and before any submodule import), so it beats every numpy import
# in every host. On Dragonfly's frozen 3.10 interpreter, EVERY
# ``platform.win32_ver()`` call runs ``cmd /c ver`` as a subprocess
# (``platform._syscmd_ver``, uncached): numpy reaches it at import time
# (``platform.system()`` inside ``_add_new_docs_scalars``), debugpy at
# kernel start (``platform.platform()``). Both hosts run console-less (the
# DETACHED notebook app; the CREATE_NO_WINDOW gate children; Dragonfly's
# own GUI process loading the ORS glue), and a subprocess spawned from a
# console-less parent allocates a NEW VISIBLE console window — the window
# spam the user watched. Replace the shell-out with the in-process source
# platform.py itself falls back to when the ``ver`` output cannot be
# parsed (``sys.getwindowsversion().platform_version or winver[:3]``);
# ``win32_ver`` consumes only the tuple's [2] element (the
# major.minor.build string), so the passthrough defaults for [0]/[1] are
# the stdlib's own behavior on any failure.
if sys.platform == 'win32':
    def _syscmd_ver_no_shell(system='', release='', version='',
                             supported_platforms=('win32', 'cygwin',
                                                  'darwin')):
        if sys.platform not in supported_platforms:
            return system, release, version
        try:
            winver = sys.getwindowsversion()
            numbers = winver.platform_version or winver[:3]
            return system, release, '{}.{}.{}'.format(*numbers[:3])
        except Exception:
            return system, release, version

    platform._syscmd_ver = _syscmd_ver_no_shell

# The host decision, MARKER FIRST: the notebook application process sets
# DFNB_ORS_FREE_APP=1 before importing anything from this package (see
# notebook_app.py), so it is identified by process identity, not by ORS
# importability — the launcher's env strip keeps ORS off the app's
# sys.path today, and the marker keeps the invariant even if that ever
# leaks. Every other host falls back to the ORS probe.
if os.environ.get('DFNB_ORS_FREE_APP') != '1' \
        and importlib.util.find_spec('ORSServiceClass') is not None:
    # Dragonfly (and every ORS-capable host: the tier-2/3 headless suites):
    # the canonical EAGER re-export -- the loader star-imports this package
    # and registers whatever `from ... import *` binds, and the lazy-only
    # shape binds the SUBMODULE (the lazy import itself plants the
    # submodule in __dict__, shadowing __getattr__ and breaking
    # registration -- 'Descriptor not found' in the app log).
    from .DragonflyNotebook import DragonflyNotebook
# else: an ORS-free host — the standalone notebook application process
# (round 8, by its own DFNB_ORS_FREE_APP marker) or a launcher-free
# environment (tier 1). The glue must not load there, and __getattr__
# could not resolve the class anyway (the glue needs OrsLibraries —
# probed ModuleNotFoundError): its real job is keeping THIS PACKAGE and
# its sibling submodules importable ORS-free, while a star-import of the
# class in such a host fails LOUDLY, never silently binding the wrong
# thing.


def __getattr__(name):
    if name == 'DragonflyNotebook':
        from .DragonflyNotebook import DragonflyNotebook
        return DragonflyNotebook
    raise AttributeError('module {!r} has no attribute {!r}'
                         .format(__name__, name))


def __dir__():
    return sorted(list(globals()) + __all__)
