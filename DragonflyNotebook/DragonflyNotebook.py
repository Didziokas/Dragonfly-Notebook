r"""Dragonfly Notebook: Jupyter-style .ipynb notebooks beside Dragonfly.

Round 8 — the notebook no longer lives inside the Dragonfly process. The
2026-10-15 live-validation crash (an access violation on Dragonfly's GUI
thread inside ORS's own paint/UI re-entrancy: a paint pass called an ORS
Python paintEvent override whose nested PyQt QtGui calls died in Qt6Gui)
was the third crash of the same class after rounds 5-7 closed two others:
ANY event pumping on Dragonfly's GUI thread can deliver ORS's deferred
live-UI work re-entrantly and crash the application. Process separation
removes the whole class by construction:

* THIS module is the Dragonfly-side launcher: the menu item starts the
  bridge service (``bridge_service`` — a ``QLocalServer`` named
  ``dfnb_bridge_<pid>`` that dispatches dfnb requests to the ORS data
  layer on the GUI thread, one request at a time) and spawns ONE
  standalone notebook application process per click
  (``notebook_app.py``, DETACHED_PROCESS so no console window appears —
  the user's explicit choice). Several notebook windows are simply
  several app processes.
* The application process never loads ORS: it owns the editor UI and the
  kernel (``notebook_kernel``), and reaches the session only through the
  bridge client (``dfnb_client``). Dragonfly's GUI stays fully usable —
  and fully event-pumping — while notebook cells run.

Round 9 adds the .ipynb drop entry: an application-level Qt event filter
(``_IpynbDropFilter``) intercepts file drags anywhere in Dragonfly and,
when the drag carries ONLY local .ipynb URLs (the shared
``notebook_drop_policy`` decision), consumes it BEFORE the app's C++
file-drop dispatcher (which has hardcoded extension lists and no hook a
plugin could register .ipynb with — probed round 9) and launches the
notebook application with the dropped file. The filter is installed when
the first plugin instance starts and removed when the last one goes.
Every other drag returns False from the filter and hits the platform
path untouched.

The child's environment deliberately strips ``PYTHONPATH`` (the ambient
ORS auto-imports of the Dragonfly process must not apply there) and
``QT_PLUGIN_PATH`` (the child uses its own Python_env PyQt6 plugin path),
and sets ``KMP_DUPLICATE_LIB_OK`` per the platform gauntlet.

Cleanup: when the plugin instance goes away (Dragonfly closing) its child
process is killed and — when no instance is left — the bridge is closed;
a client that loses the pipe shows its disconnected state and reconnects
if Dragonfly (and a click on the menu) comes back.

:author: Dragonfly Notebook project
"""

import os
import subprocess
import sys

# Same multiple-OpenMP-runtime workaround the app's bundled sklearn applies
# (Python_env/Lib/site-packages/sklearn/__init__.py): the ORS libraries run on
# libomp140 while numpy/scipy load libiomp5md, and letting both initialize
# avoids a hard 'OMP: Error #15' process abort. Must run before ANY numpy
# import -- including the indirect one when the bridge handlers first touch
# numpy.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

# The round-9 drop filter's Qt base. QtCore only, at module top: this glue
# is imported by Dragonfly itself (a Qt6 process) and by the ORS-capable
# tier-2/3 suites (PyQt6 is installed in the embedded environment); the
# ORS-free application process never imports this module (the package
# __init__'s DFNB_ORS_FREE_APP marker).
from PyQt6.QtCore import QEvent, QObject  # noqa: E402

from OrsLibraries.logger import Logger
from ORSServiceClass.actionAndMenu.menu import Menu
from ORSServiceClass.decorators.infrastructure import menuItem
from ORSServiceClass.OrsPlugin.orsPlugin import OrsPlugin

from .notebook_drop_policy import ipynb_drop_decision  # noqa: E402
# The plugin version's ONE source is dragonflynotebook_core (the module
# both this ORS process and the ORS-free notebook application import --
# round 15); re-exported here so Dragonfly-side code keeps the natural
# DragonflyNotebook.__version__ spelling.
from .dragonflynotebook_core import __version__  # noqa: E402,F401

log = Logger('DragonflyNotebook')

# the documented fallback interpreter (the standard Dragonfly install)
_FALLBACK_PYTHON = r'C:\Program Files\Dragonfly\Python_env\python.exe'

_BRIDGE = None


def get_bridge_service():
    """The Dragonfly process's one bridge service (created on first click,
    listening on ``dfnb_bridge_<pid>``)."""
    global _BRIDGE
    if _BRIDGE is None or not _BRIDGE.is_listening:
        from .bridge_service import NotebookBridgeService
        from . import dragonflynotebook_lib as dfnb
        _BRIDGE = NotebookBridgeService(
            dfnb.BRIDGE_HANDLERS,
            discard_hook=dfnb.discard_bridge_object)
        if not _BRIDGE.is_listening:
            _BRIDGE = None
            raise RuntimeError(
                'The Dragonfly Notebook bridge pipe could not be opened.')
    return _BRIDGE


def close_bridge_service():
    """Closes the bridge when the last plugin instance is gone."""
    global _BRIDGE
    if _BRIDGE is not None:
        try:
            _BRIDGE.close()
        except Exception:
            pass
        _BRIDGE = None


def _child_python_executable():
    """The interpreter the child app runs on: ``sys.executable`` inside
    Dragonfly IS the embedded Python_env python; the documented install
    path is the fallback when it is missing."""
    candidate = sys.executable or ''
    if candidate and os.path.isfile(candidate):
        return candidate
    return _FALLBACK_PYTHON


def child_app_environment():
    """The child's environment: the parent's, minus the variables that
    must NOT cross the process boundary."""
    env = dict(os.environ)
    env['KMP_DUPLICATE_LIB_OK'] = 'True'
    # ambient ORS auto-imports must not apply in the notebook process
    env.pop('PYTHONPATH', None)
    # the child uses its own Python_env PyQt6 plugin path
    env.pop('QT_PLUGIN_PATH', None)
    return env


# ---------------------------------------------------------------------------
# the .ipynb drop filter (round 9)
# ---------------------------------------------------------------------------

_DROP_FILTER = None


def _show_launch_failure(exc):
    """The ONE-box launch-failure report, extracted from startupDefault so
    the menu click AND the drop filter share the exact same failure
    surface (one box per failure path, titled with the plugin display
    name)."""
    from ORSServiceClass.messagebox.orsMessageBox import OrsTMessageBox
    OrsTMessageBox.message(
        None,
        message='The Dragonfly Notebook application could not be '
                'started: {}'.format(exc),
        caption='Dragonfly Notebook',
        icon=OrsTMessageBox.Critical,
        buttons=OrsTMessageBox.Ok)


class _IpynbDropFilter(QObject):
    """The application-level .ipynb drop filter (round 9).

    Dragonfly's file-drop dispatcher is C++ with hardcoded extension
    lists and offers NO hook a plugin can register .ipynb with (probed
    round 9), so this filter intercepts the drag one level up: installed
    on the QApplication, it sees every DragEnter / DragMove / Drop
    delivered anywhere in the application and CONSUMES the ones the
    shared drop policy claims (ONLY local .ipynb file URLs) -- a
    consumed drag never reaches the C++ dispatcher. Every other drag
    (mixed .ipynb + other files, other extensions, non-URL drags)
    returns False here and hits the platform path untouched.

    On a consumed Drop the launcher runs (``_launch_notebook_for_path``
    by default, resolvable at EVENT time so a test can monkeypatch the
    module function): the same one-click path as the menu, with the
    dropped file as the spawned application's argv[2]. A launcher
    failure is logged and reported through the shared ONE-box rule --
    never an escaping exception (one out of an eventFilter is fatal to
    the receiver's event delivery).
    """

    _DRAG_TYPES = (QEvent.Type.DragEnter, QEvent.Type.DragMove,
                   QEvent.Type.Drop)

    def __init__(self, launcher=None):
        super().__init__()
        # None -> the module-level launcher, resolved per event (see the
        # class docstring's testability note).
        self._launcher = launcher

    def eventFilter(self, obj, event):  # noqa: ARG002 -- Qt calls per event
        event_type = event.type()
        if event_type not in self._DRAG_TYPES:
            return False
        try:
            mime = event.mimeData()
        except RuntimeError:
            return False  # a dying drag source: never raise into Qt
        if mime is None or ipynb_drop_decision(mime) != 'consume':
            return False
        event.accept()
        if event_type != QEvent.Type.Drop:
            return True  # consumed: the dispatcher never sees the drag
        path = mime.urls()[0].toLocalFile()
        try:
            (self._launcher or _launch_notebook_for_path)(path)
        except Exception as exc:
            log.error('Dragonfly Notebook: the dropped notebook {} could '
                      'not be opened: {}'.format(path, exc))
            _show_launch_failure(exc)
        return True


def _launch_notebook_for_path(path):
    """The drop filter's launcher: exactly the menu click's path (a new
    plugin instance owning the new child application process), with the
    dropped file as the child's argv[2]. startupDefault's own guard logs
    and shows the ONE failure box on a spawn failure."""
    DragonflyNotebook.startupDefault(openMainForm=False, file_path=path)


def _ensure_drop_filter():
    """Installs the application-level .ipynb drop filter when the FIRST
    plugin instance starts (idempotent). A context without a QApplication
    (headless hosts) installs nothing: there is no drag to intercept."""
    global _DROP_FILTER
    if _DROP_FILTER is not None:
        return
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    _DROP_FILTER = _IpynbDropFilter()
    app.installEventFilter(_DROP_FILTER)
    log.info('Dragonfly Notebook: .ipynb drop filter installed (drop a '
             'notebook file onto the Dragonfly window to open it).')


def _remove_drop_filter():
    """Removes the application-level filter -- called when the LAST plugin
    instance goes (see ``DragonflyNotebook.cleanup``; the guard there
    counts the surviving instances first)."""
    global _DROP_FILTER
    if _DROP_FILTER is None:
        return
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.removeEventFilter(_DROP_FILTER)
    _DROP_FILTER = None


class DragonflyNotebook(OrsPlugin):

    # One child application process per instance: the user can open several
    # notebook windows (one click, one window).
    multiple = True
    savable = False
    keepAlive = True
    canBeGenericallyOpened = False
    showInToolbar = False

    # NO UIDescriptors since round 8: there is no in-process form to dock.
    # (getMainFormName() then returns None and startupDefault opens no
    # widget — the launcher spawns the app process instead.)
    UIDescriptors = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._child = None
        # The .ipynb drop filter lives while at least one instance does
        # (installed on the first instance's start, removed when the last
        # goes -- see cleanup).
        _ensure_drop_filter()

    def launch_notebook_app(self, file_path=None):
        """Starts the bridge (once per Dragonfly process) and spawns ONE
        standalone notebook application connected to it. ``file_path``
        (the round-9 drop / launcher handoff) is appended as the child's
        argv[2] -- the application opens it right after showing its
        window. Returns the child process handle (None when the spawn
        failed -- the error is logged, not raised into the menu click)."""
        service = get_bridge_service()
        app_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'notebook_app.py')
        if not os.path.isfile(app_path):
            raise RuntimeError(
                'notebook_app.py is missing from the installed plugin '
                'folder ({!r}); reinstall the plugin.'.format(app_path))
        creationflags = 0
        if sys.platform == 'win32':
            # DETACHED_PROCESS: the child gets NO console window (the
            # user's explicit choice for the notebook app)
            creationflags = getattr(subprocess, 'DETACHED_PROCESS', 0)
        command = [_child_python_executable(), app_path, service.pipe_name]
        if file_path:
            command.append(file_path)
        try:
            self._child = subprocess.Popen(
                command,
                creationflags=creationflags,
                env=child_app_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True)
        except OSError as exc:
            self._child = None
            raise RuntimeError(
                'The Dragonfly Notebook application could not be started: '
                '{}.'.format(exc)) from exc
        return self._child

    def cleanup(self):
        """The child app process belongs to this instance: kill it when the
        plugin goes away (Dragonfly closing); close the bridge -- and
        remove the application-level .ipynb drop filter -- when the last
        instance is gone."""
        child, self._child = self._child, None
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except Exception:
                        child.kill()
            except Exception:
                pass
        try:
            if not [one for one in type(self).getAllInstances()
                    if one is not self]:
                close_bridge_service()
                _remove_drop_filter()
        except Exception:
            pass
        super().cleanup()

    @classmethod
    def startupDefault(cls, openMainForm=True, file_path=None):
        """The launcher: one instance (and one child notebook application
        process) per menu click. ``file_path`` (the round-9 drop entry)
        rides along as the child's argv[2]. A failure shows exactly ONE
        message box titled with the plugin display name (the one-box
        failure rule, shared with the drop filter)."""
        plugin = super().startupDefault(openMainForm=openMainForm)
        if plugin is None:
            return None
        try:
            plugin.launch_notebook_app(file_path=file_path)
        except Exception as exc:
            log.error('Dragonfly Notebook: {}'.format(exc))
            _show_launch_failure(exc)
        return plugin

    @classmethod
    @menuItem('Utilities')
    def runDragonflyNotebook(cls):
        # The menuItem decorator returns what this method RETURNS (the
        # exemplar docstring: the method 'should return a valid instance of
        # Menu') -- the tier-3 suite calls it with returnAsMenuInstances=True
        # and pins the registered menu. Clicking it runs startupDefault,
        # which spawns ONE notebook application process per click.
        return Menu(title='Run Dragonfly Notebook',
                    id_='DragonflyNotebook',
                    section='090_Plugins',
                    action=cls.getActionStringForStartupDefault())
