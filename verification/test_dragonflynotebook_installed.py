r"""INSTALL-PATH smoke test for the Dragonfly Notebook Dragonfly plugin.

Run AFTER Install.ps1, with Dragonfly closed.

Round 8 -- process separation. The plugin at its installed location is now
a LAUNCHER: the OrsPlugin class starts the bridge service (a QLocalServer
named ``dfnb_bridge_<pid>``) and spawns ONE standalone notebook
application process per menu click; the editor UI and the IPython kernel
live in that application (``notebook_app.py``), which never loads ORS and
reaches the session only through the bridge client. This suite verifies
the installed tree at
  %LOCALAPPDATA%\...\pythonUserExtensions\Plugins\DragonflyNotebook\
(the data root is probed the same way Install.ps1 probes it: Comet\Dragonfly*,
ORS\Dragonfly*, %LOCALAPPDATA%\Dragonfly*), imported the way the app's
plugin manager sees it (namespace package
``pythonUserExtensions.Plugins.DragonflyNotebook``):

  (1) the launcher class pins: NO UIDescriptors any more (no MainForm, no
      FunctionsDock -- the in-process forms are gone), the Utilities menu
      item, and the LOADER-CONTRACT pin (the app's user-plugin loader
      star-imports every plugin folder and registers whatever the
      star-import bound -- it must bind the plugin CLASS, never the
      same-named submodule a lazy-only __init__ lets shadow it in the
      package __dict__: the round-8b registration bug);
  (2) sha256 pins: the installed sources are byte-identical to the
      reviewed project's files (a stale install is caught by hash, not by
      a confusing AttributeError deep in the form pins), and the three
      modules the architecture retired are GONE from the installed tree
      (Install.ps1 removes the previous install directory entirely);
  (3) the launcher mechanics: the bridge service starts on demand with the
      pid-suffixed pipe name, and the spawn the launcher issues -- the
      installed notebook_app.py, DETACHED_PROCESS, the stripped
      PYTHONPATH/QT_PLUGIN_PATH, the DEVNULL streams -- is driven through
      a recording subprocess stub, so the pin never spawns a real child;
  (4) the standalone form headless: the REAL DragonflyNotebookForm
      constructed the way notebook_app.py constructs it -- a plain
      QMainWindow over a REAL local bridge service (this suite process
      runs under dfpy_ors, so the bridge handlers reach ORS) -- pinning
      the structure, the per-run BUSY bar (indeterminate, no percentage),
      the Stop-flip run button, the disconnected banner, the defaults and
      the save round-trip; round 11 adds the markdown cell's two modes
      here: a loaded markdown cell opens RENDERED (editor hidden, browser
      showing, the top-right '✎ Edit' button visible), the button flips
      back to EDIT, running the cell renders it again, a freshly added
      markdown cell starts EDIT, and the modes -- keyed by the CELL's id,
      never the widget index -- survive a structural delete's rebuild;
      round 12 hardens the rendered mode: it is run-INERT (the browser
      consumes the Ctrl/Shift/Alt+Enter chords outright, a repeat run is
      a no-op, focus lands on the browser deterministically) and
      flat-chrome (no run button, no type label, no accent bar, no cell
      border or background -- the dfnb_rendered property's QSS rules), a
      frame click selects the rendered cell, and Shift+Enter advancing
      onto one focuses its browser; round 20 adds the nbformat multiline
      text payloads (every text field may be a str or a LIST of str
      lines, the foreign-notebook crash class that round closes): a list
      stream text renders both lines, a list text/plain renders, a list
      image/png embeds byte-exact (lines joined before the whitespace
      strip), the real open chain (from_file + _load_notebook_document)
      keeps a notebook whose stored outputs are list-typed and shows the
      stream text in the cell browser, and a renderer that RAISES cannot
      kill the window (the open falls back to a fresh empty notebook
      behind exactly one user-facing failure entry); round 21 adds the
      highlighter's block-alignment regression pin: a source STARTING
      with blank lines (typed, pasted or loaded from a file) must still
      color import/def/class correctly -- the pin reads the Qt-APPLIED
      formats on the real document blocks, with and without the leading
      newline, through both the edit path and the load path;
  (5) the picker dialogs headless, BRIDGE-FED: the object combos fill
      through ``dfnb.list_objects`` over the real pipe against real ORS
      fixtures, the measured-properties chooser through
      ``list_measured_properties``, and the one-message-per-failure
      contract is observed through the form logger (under the
      ``running_in_test`` convention the failure box is a LOG entry, so
      the pin counts log records, never a modal box);
  (6) round 9, headless: the dark-theme text pins (the editor
      stylesheet's light text on the dark background, the raised timing/
      status/prompt colors, the output browser's light text), the
      ORS-verbatim scrollbar rules ported into the form stylesheet
      (SCOPED: every QScrollBar selector line names the cells panel or
      the output browsers -- child dialogs inherit ancestor stylesheets),
      the code editors' AlwaysOff scrollbar policies, the GROW-WITH-
      CONTENT anti-clip pin (a 40-line and a hard-wrapped cell must leave
      the editor's own vertical scrollbar maximum at 0 -- the layout's
      visual-line ground truth -- through the real form hooks too) and
      the output browsers' mirrored fit (a text output sizes its browser
      to the document at vbar max 0, a 600x300 figure shows fully; round
      21: every output renders at its FULL content height past the
      retired 400 px cap -- vertical scrollbar policy AlwaysOff, no
      nested vertical scrolling; a tall rendered markdown
      cell takes its full content height too; round 22: NO horizontal
      scrolling either -- both policies AlwaysOff, and an image larger
      than the viewport is SCALED DOWN uniformly to the available width,
      aspect preserved, never upscaled, on every content path (rendered
      outputs, live display payloads, markdown embeds) and re-fitted
      through ONE debounced pass when the window is resized, the width
      tracking min(intrinsic, available) at every size; a wide embedded
      PNG in a rendered markdown cell scales too, and the far-valve and
      scroll-preservation pins below are untouched), the
      Qt-free .ipynb drop decision truth table, the cell editors'
      ignore-the-.ipynb-drag contract (no URL text inserted, snippet
      drags still accepted), the form-level drop (a valid local .ipynb
      opens, a corrupt one is refused with exactly ONE log entry and the
      notebook untouched, a mixed drag is refused), the drop-filter
      lifecycle (installed by the first plugin instance, idempotent,
      removed on demand) and the filter's own consume/pass event contract
      with a monkeypatched launcher; plus round 21's scroll
      preservation: structural actions (delete, move, insert below,
      append) keep the viewport on the current cell of a 25-cell
      notebook with 40-line STORED OUTPUTS instead of jumping to the
      top (the stored outputs above the current cell settle their
      geometry over several zero-turns, so the deferred settle chains
      BOUNDED passes while the layout is still moving or the frame is
      out of view; the outputless first fixture and a visibility-only
      retry both let a mid-cascade pass slip through; round 22b: the
      FIGURE-RICH shape of the same contract -- the debounced image pass
      rescales stored figures AFTER the settle finishes, so the anchor's
      quiet passes hand the viewport back to the current cell (delete
      and resize-narrower, pumped past the 140 ms debounce, stored
      figure scaled, no scrollbar either way) while a current cell the
      user scrolled far off-screen is never yanked back), the full-height
      fit's far safety valve (_MAX_FULL_HEIGHT,
      engaged only past Qt's own widget limit: the height pins at the
      ceiling and that browser flips to AsNeeded, every normal fit
      stays AlwaysOff), and opening a notebook still lands at the top;
  (7) the spawned application, offscreen: a REAL second process --
      ``notebook_app.py`` from the installed tree against this suite's
      bridge -- driven through the app's documented DFNB_SMOKE hooks and
      pinned end to end: the app boots, connects, stays ORS-free by its
      own DFNB-APP-ORSFREE marker (the round-8b process-identity pin), a
      dfnb round-trip crosses the pipe, the busy bar shows no percentage
      text, the GUI
      stays responsive while a cell sleeps (the live DFNB-TICKS), the
      Stop path interrupts the running cell (DFNB-STOP-OK), and closing
      the bridge shows the disconnected state (DFNB-DISCONNECTED); plus
      the round-9 argv handoff: a child spawned with the staged notebook
      as argv[2] opens it after the form shows (DFNB-APP-UP stays first,
      then DFNB-APP-OPENED names the file) and a corrupt one reports
      DFNB-APP-OPEN-FAIL;
  (8) the kernel smoke, subprocess-guarded (installed mode): the ENGINE
      pins -- lazy start, streams, stdin refusal, inline figure, the
      pushed dfnb, the ORS-free namespace, GUI-timer freedom through a
      sleeping cell, the request_stop interrupt, the queued stop (a stop
      requested before the run started still ends it interrupted, not as
      an engine failure), restart, and the clean-quit stop teardown
      (stop() ends the live environment child with exit 0 -- the quit
      line the old teardown never delivered -- and frees the in-use
      lock, never the TerminateProcess fallback);
  (9) round 10, the shipped example EXECUTED: the repo's
      examples/spheres_example.ipynb loaded through the shipped reader
      and the form's real open path (_load_notebook_document), every
      code cell run through the REAL in-process kernel over the real
      bridge handlers, ZERO error outputs, the three published objects
      present in the session, the exported measured properties exactly
      the three expected names (add mode on a fresh MultiROI), and the
      watershed separating exactly the 40 painted spheres (40 marker
      components, 40 labels -- the rejection-sampled generator's
      guarantee);
  (10) rounds 16+17, the custom python environments on a TEMP root
      (DFNB_ENVS_ROOT, the seam notebook_envs.envs_root() honors): the
      fresh combo (the embedded entry + the two administrative entries,
      the pip button disabled with its reason in the tooltip), the
      suppressed creation and delete picks under the suite seam (a log
      record + revert each), the New Environment dialog's live name
      validation, the REAL venv creation streamed through the
      _EnvProcessDialog (start + event-loop pump, never exec), the
      base-package install (round 17's second creation dialog) pinned
      OFFLINE through a hand-built wheel, the user pick switching the
      notebook (selection, metadata, label, the pip rule), a hand-built
      wheel installed OFFLINE through the venv's own pip (the pip
      button's FULL command text, round 17), a real cell
      importing the wheel's package FROM the venv's site-packages while
      the kernel holds the in-use lock (round 18: the pip button STAYS
      ENABLED then, its tooltip carrying the file-in-use risk note --
      the old in-use disable locked the button permanently after the
      first cell run, and a restart could never clear it), a kernel
      RESTART under the same hold (the button still enabled at
      restart_finished), a
      shell-escape cell ('!python -c ...') streaming the venv's own
      interpreter path back, the stdlib extension family importing in
      the venv child (round 17's scan-discovered runtime DLL copy + the
      runner's directory arm -- sqlite3 and tkinter included), the
      round-17 pip rule in the cells themselves ('%pip3 list' refused
      with the explained magic error, '!pip3 list' refused with the
      button note: pip runs only from the pip button, nothing
      spawned), the switch back to embedded
      auto-confirmed under test (kernel shut down, lock freed, metadata
      dropped), the metadata round-trip through save + the open path,
      the missing-environment fallback to the embedded Python (one log
      record + a status note), the streaming dialog's close refusal
      while a child runs, a pip failure surfacing pip's own diagnostic
      through the exit code, the styled delete picker (round 18: the
      QListWidget dialog on the plugin's dialog stylesheet, constructed
      and pinned headless), and the delete flow: a hold this window
      cannot stop (the direct lock, another window's stand-in) refuses,
      the unselected delete removes only its own tree, and the
      delete with THIS window's kernel running on the environment
      (round 18) stops the kernel first, frees the lock, removes the
      tree and silently switches back to the embedded Python, and the
      spawn-failure path end to end (round 18 review: the venv's
      interpreter deleted between the selection and the click -- no
      exception escapes the pip slot, one status line, exactly one
      'Dragonfly Notebook' log entry naming the OSError, the flag
      dropped and the button re-enabled).

``DFNB_INSTALL_ROOT`` overrides the installed-root probe with one explicit
data root: the pre-install staging seam. With install=false the suite can
be exercised against a STAGED copy of the reviewed sources (laid out
exactly like an install under a temp root) -- the same bytes Install.ps1
will copy -- without touching the real %LOCALAPPDATA% tree; the real run
after Install.ps1 leaves the variable unset and probes the live roots.

Run (from the generated project root -- note the dfpy_ors launcher):

  uv run --no-project --python "C:\Program Files\Dragonfly\Python_env\python.exe" \
      verification/dfpy_ors.py verification/test_dragonflynotebook_installed.py

Provenance (structure mirrors DragonflyH5Importer's
verification/test_h5volumeimporter_installed.py, itself the
MatcherDragonfly\verification\test_labelmatcher_installed.py exemplar):
  * result() lines, the installed-import section and the sha256 pins: the
    tier-3 exemplars one-for-one.
  * ``app.setProperty("running_in_test", True)`` -- the form's failure
    paths log instead of going modal headless (the form module's
    ``_show_message`` seam, the same convention OrsTMessageBox follows at
    orsMessageBox.py lines 141-144). The property is installed BEFORE
    anything else: this suite has NO message-box stub, so an unexpected
    failure box must log, never hang the gate. The failure-path pins
    (open failures, save failure, the engine-failure chain-stop, the
    empty-picker OKs) observe the LOG records instead.
  * the multi-root probe: the same parent dirs the template's Install.ps1
    probes, so the suite finds the plugin wherever the installer put it.
  * the sha256 source pins: the installed modules must be byte-identical
    to the generated project's files.

NOT covered here (needs the live application; verified manually at install
time -- see README.md's checklist): the launcher click inside the live
Dragonfly UI, Dragonfly's GUI usability while a notebook cell runs (the
property process separation buys -- the crash class rounds 5-7 chased is
closed by construction, and the spawned-app section proves the notebook's
own GUI stays free), real pointer drags from Dragonfly's object panel,
the picker dialogs' modality (they are constructed and their widgets and
``parameters()`` are pinned headless, but ``exec()`` never runs here),
live-ORS kernel runs from real notebook cells (the spawned app runs the
scripted sleep cell; real data cells are pinned at the handler layer by
the tier-2 suite). The kernel's engine behavior is pinned by the SEPARATE
subprocess kernel smoke (section 8), a HARD GATE: exit 0 AND the
KERNEL-SMOKE-OK marker or the suite FAILS -- a smoke failure, timeout or
unstartable helper is never a skip (the smoke is proven reliable; a red
smoke must be a red gate).
"""

import base64  # noqa: E402
import hashlib  # noqa: E402
import importlib  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import zipfile  # noqa: E402

# Same multiple-OpenMP-runtime workaround as the core: MUST precede numpy,
# so the ORS/libs OpenMP mix never aborts the process with 'OMP: Error #15'.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import numpy as np  # noqa: E402  (the fixture builders)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The suite's own child pythons are console-subsystem binaries; without
# CREATE_NO_WINDOW every gate run puts a printing console window on the
# user's desktop for as long as the suite runs (the round-13 report -- the
# production app spawn is already DETACHED_PROCESS; this covers the
# harness's own spawns only). 0 where the platform lacks the flag.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def result(ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + detail, flush=True)
    if not ok:
        raise AssertionError(detail)


class _RecordingLogHandler(logging.Handler):
    """Captures the form logger's failure-path entries: under the
    ``running_in_test`` convention the ONE-message-box-per-failure contract
    surfaces as a log record, never a modal box."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


# ---------------------------------------------------------------------------
print("=== (1) import from the installed location ===", flush=True)

# The live app puts the Dragonfly user root on sys.path at startup (that is
# how pythonUserExtensions and its Plugins namespace import). Replicate it
# here, probing the same roots Install.ps1 probes. DFNB_INSTALL_ROOT
# overrides the probe (the pre-install staging seam).
_LOCAL = os.environ.get("LOCALAPPDATA", "")


def _find_user_root():
    """The first Dragonfly data root whose Plugins folder holds the plugin
    (the installer's probe order: Comet, ORS, then %LOCALAPPDATA% itself);
    DFNB_INSTALL_ROOT overrides with one explicit root."""
    override = os.environ.get("DFNB_INSTALL_ROOT", "")
    if override:
        if os.path.isdir(os.path.join(override, "pythonUserExtensions",
                                      "Plugins", "DragonflyNotebook")):
            return override
        return None
    for parent in (os.path.join(_LOCAL, "Comet"),
                   os.path.join(_LOCAL, "ORS"),
                   _LOCAL):
        if not parent or not os.path.isdir(parent):
            continue
        for name in sorted(os.listdir(parent)):
            if not name.startswith("Dragonfly"):
                continue
            candidate = os.path.join(parent, name)
            if os.path.isdir(os.path.join(candidate, "pythonUserExtensions",
                                          "Plugins", "DragonflyNotebook")):
                return candidate
    return None


user_root = _find_user_root()
assert user_root is not None, (
    "no installed DragonflyNotebook found under %LOCALAPPDATA% "
    "(checked Comet/Dragonfly*, ORS/Dragonfly*, %LOCALAPPDATA%/Dragonfly*; "
    "DFNB_INSTALL_ROOT may override) -- run Install.ps1 first, then re-run "
    "this suite")
result(os.path.isdir(os.path.join(user_root, "pythonUserExtensions")),
       "the Dragonfly data root with the installed plugin: " + user_root)
if user_root not in sys.path:
    sys.path.insert(0, user_root)

import pythonUserExtensions.Plugins.DragonflyNotebook as installed_pkg  # noqa: E402

expected_root = os.path.join(user_root, "pythonUserExtensions", "Plugins", "DragonflyNotebook")
result(installed_pkg.__file__.lower().startswith(expected_root.lower()),
       "imported from the installed location: {}".format(installed_pkg.__file__))

# Round 14 (the round-13 window-spam fix), pinned at the mechanism: the
# package __init__ replaced platform._syscmd_ver BEFORE any submodule
# import -- on this frozen 3.10 interpreter every platform.win32_ver()
# call runs `cmd /c ver` as a subprocess (uncached), and a subprocess of
# a console-less parent (the DETACHED app child, the CREATE_NO_WINDOW
# gate children, Dragonfly's own GUI process loading the ORS glue)
# allocates a NEW VISIBLE console window. numpy reaches it at import
# time, debugpy at kernel start (both live-reproduced in the round-13
# audit probes). This pin holds the replacement in place in THIS process
# (the import above ran the __init__) and checks it answers win32_ver's
# parse -- the in-process sys.getwindowsversion numbers, the stdlib's
# own fallback source when the `ver` output cannot be parsed.
import platform as _stdlib_platform  # noqa: E402

result(_stdlib_platform._syscmd_ver.__name__ == '_syscmd_ver_no_shell',
       "the package __init__ replaced platform._syscmd_ver (the `cmd /c "
       "ver` shell-out every win32_ver() call spawns -- a visible console "
       "window in every console-less host: the round-13 window spam)")
_ver_tuple = _stdlib_platform._syscmd_ver()
_winver = sys.getwindowsversion()
result(len(_ver_tuple) == 3
       and _ver_tuple[2] == '{}.{}.{}'.format(
           *(_winver.platform_version or _winver[:3])),
       "the replacement feeds win32_ver the same major.minor.build "
       "in-process (sys.getwindowsversion().platform_version -- the "
       "stdlib's own fallback source): {!r}".format(_ver_tuple[2]))

# The package __init__ re-exports the CLASS -- EAGERLY in every ORS-capable
# host (the round-8b registration contract the loader pin below holds),
# skipped marker-first in the app process (DFNB_ORS_FREE_APP=1, process
# identity, ORS importability only the fallback probe) -- and elsewhere the
# PEP 562 fallback can only fail LOUDLY (an ORS-free host cannot import the
# glue, so it never silently binds a wrong attribute) -- so the attribute
# is the class while its __module__ still names the submodule it was
# defined in.
DragonflyNotebookPlugin = installed_pkg.DragonflyNotebook
result(DragonflyNotebookPlugin.__module__ == "pythonUserExtensions.Plugins.DragonflyNotebook.DragonflyNotebook",
       "plugin class module: " + DragonflyNotebookPlugin.__module__)

# The launcher identity (round 8): several notebooks at once (one spawned
# application per click), kept alive, and NO in-process forms -- the
# UIDescriptors list is EMPTY (no MainForm, no FunctionsDock: the editor
# lives in the standalone application process).
result(DragonflyNotebookPlugin.multiple is True,
       "multiple is True (one spawned app per click)")
result(DragonflyNotebookPlugin.keepAlive is True, "keepAlive is True")
result(DragonflyNotebookPlugin.canBeGenericallyOpened is False,
       "canBeGenericallyOpened is False")
result(DragonflyNotebookPlugin.UIDescriptors == [],
       "NO UIDescriptors (round 8 removed the in-process MainForm and "
       "FunctionsDock; got {})".format(DragonflyNotebookPlugin.UIDescriptors))
menuItem = DragonflyNotebookPlugin.runDragonflyNotebook(returnAsMenuInstances=True)
result(menuItem is not None and menuItem.title == "Run Dragonfly Notebook"
       and menuItem.section == "090_Plugins",
       "Utilities > Plugins menu item 'Run Dragonfly Notebook' (section {})".format(menuItem.section))

# The REGISTRATION contract, replicated the way Dragonfly's user-plugin
# loader consumes this package
# (ORSServiceClass/OrsPlugin/allCurrentUserPythonPlugins.py:
# exec('from pythonUserExtensions.Plugins.DragonflyNotebook import *'),
# then the platform registers whatever the star-import bound by evaluating
# DragonflyNotebook.getDescriptor() on it -- abstractPlugin.py's
# classmethod). Round 8b: the lazy-only __init__ let the SUBMODULE shadow
# the class in the package __dict__ (the lazy import itself plants
# pkg.__dict__['DragonflyNotebook'] = <submodule>, and PEP 562
# __getattr__ only fires on a __dict__ miss), so the loader bound the
# MODULE, the registration eval AttributeErrored, OrsPluginManager logged
# 'Descriptor not found for ...Plugins\\DragonflyNotebook' and the plugin
# silently never registered (no Utilities menu item). The guarded EAGER
# re-export keeps the CLASS in __dict__ before any star-import happens
# (probed on the embedded interpreter: under the lazy-only shape even a
# fresh first-access star-import binds the submodule). This pin mirrors
# the loader's ORDER -- the star-import comes FIRST, before anything else
# in the process could matter. (Placement is order, not defense: under the
# canonical shape the same-named submodule is already in sys.modules when
# __init__ finishes, so no normal re-import -- plain, importlib, same- or
# differently-named -- re-shadows the package __dict__ afterward; a
# re-shadow would take a FRESH import of the same-named submodule, which
# cannot happen while it stays in sys.modules.)
from ORSServiceClass.OrsPlugin.orsPlugin import OrsPlugin  # noqa: E402
loader_locals = {}
exec('from pythonUserExtensions.Plugins.DragonflyNotebook import *',
     loader_locals)
bound = loader_locals['DragonflyNotebook']
result(isinstance(bound, type) and not isinstance(bound, types.ModuleType),
       "the loader's star-import binds the plugin CLASS, not the same-named "
       "submodule (got {})".format(type(bound).__name__))
result(bound is installed_pkg.DragonflyNotebook,
       "the bound class IS the package attribute (identity)")
result(isinstance(bound, type) and issubclass(bound, OrsPlugin),
       "the bound class subclasses OrsPlugin")
result(callable(getattr(bound, 'getDescriptor', None)),
       "the bound class carries getDescriptor (the platform's registration "
       "eval calls it)")
result(bound.__module__ == "pythonUserExtensions.Plugins.DragonflyNotebook.DragonflyNotebook",
       "the bound class's __module__ names the submodule it was defined in")

import pythonUserExtensions.Plugins.DragonflyNotebook.dragonflynotebook_core as installed_core  # noqa: E402
import pythonUserExtensions.Plugins.DragonflyNotebook.mainform_dragonflynotebook as installed_form  # noqa: E402
import pythonUserExtensions.Plugins.DragonflyNotebook.notebook_cells as installed_cells  # noqa: E402
import pythonUserExtensions.Plugins.DragonflyNotebook.notebook_drop_policy as installed_drop_policy  # noqa: E402
import pythonUserExtensions.Plugins.DragonflyNotebook.notebook_envs as installed_envs  # noqa: E402

# The installed sources must be the reviewed ones: sha256 against the
# generated project's own files (a stale install is caught by hash, not by
# a confusing AttributeError deep in the form pins). Round 8's set: the
# launcher glue, the ORS-free package core, the bridge handler layer, the
# kernel, the cells, the standalone form, and the four round-8 modules
# (the app entry, the protocol, the notebook-side client, the service).
# Round 9 adds the Qt-free .ipynb drop policy module (shared by the cell
# editors, the form and the glue's application-level drop filter).
# Round 16 adds the environment runner (dfnb_env_kernel.py: the script the
# kernel spawns as a subprocess on a custom environment's own python --
# the installed tree must carry it or every custom-environment cell dies
# at spawn).
package_dir = os.path.join(_ROOT, "DragonflyNotebook")
SOURCE_FILES = ("__init__.py", "DragonflyNotebook.py",
                "dragonflynotebook_core.py", "dragonflynotebook_lib.py",
                "notebook_kernel.py", "notebook_cells.py",
                "mainform_dragonflynotebook.py", "notebook_app.py",
                "notebook_bridge_protocol.py", "dfnb_client.py",
                "bridge_service.py", "notebook_drop_policy.py",
                "notebook_envs.py", "dfnb_env_kernel.py")
for name in SOURCE_FILES:
    with open(os.path.join(package_dir, name), "rb") as handle:
        ref = hashlib.sha256(handle.read()).hexdigest()
    with open(os.path.join(os.path.dirname(installed_pkg.__file__), name), "rb") as handle:
        got = hashlib.sha256(handle.read()).hexdigest()
    result(got == ref, "installed {} is the reviewed source (sha256 match)".format(name))

# And the modules the round-8 architecture retired must be GONE from the
# installed tree: Install.ps1 removes the previous install directory
# entirely before copying, so their presence would mean a stale, partial
# install (the old Dragonfly-side dock/pickers/progress dialog).
for name in ("notebook_progress_dialog.py", "functions_dock.py",
             "picker_dialogs.py"):
    result(not os.path.isfile(os.path.join(os.path.dirname(installed_pkg.__file__), name)),
           "retired module {} is absent from the installed tree".format(name))

# ---------------------------------------------------------------------------
print("\n=== (2) the launcher mechanics ===", flush=True)

from PyQt6.QtCore import (QBuffer, QEvent, QEventLoop, QPoint, QPointF,  # noqa: E402
                          QRect, QMimeData, Qt, QUrl)
from PyQt6.QtGui import (QDragEnterEvent, QDropEvent, QFontMetrics, QImage,  # noqa: E402
                         QKeyEvent, QMouseEvent)
from PyQt6.QtWidgets import (QAbstractSlider, QApplication, QCheckBox,  # noqa: E402
                             QDialog, QFrame, QLabel, QMainWindow,
                             QPushButton, QScrollArea, QTextBrowser)

from ORSModel import Channel as OrsChannel  # noqa: E402  (fixtures)
from ORSModel import createChannelFromNumpyArray  # noqa: E402
from OrsHelpers.arrayhelper import ArrayHelper  # noqa: E402
from OrsHelpers.datasethelper import DatasetHelper  # noqa: E402

# The glue MODULE, named explicitly: under the canonical __init__ shape the
# package attribute 'DragonflyNotebook' IS the plugin class (the eager
# re-export the loader pin above holds), so `import ...DragonflyNotebook as
# x` binds the CLASS -- the same one-name shadowing every shipped plugin
# lives with, seen from the other side. importlib.import_module names the
# submodule itself, whatever the re-export shape.
installed_glue = importlib.import_module(
    "pythonUserExtensions.Plugins.DragonflyNotebook.DragonflyNotebook")
import pythonUserExtensions.Plugins.DragonflyNotebook.dragonflynotebook_lib as installed_lib  # noqa: E402
import pythonUserExtensions.Plugins.DragonflyNotebook.bridge_service as installed_bridge  # noqa: E402

# Bound on purpose (the PyQt ownership lesson the tier-2 loopback taught:
# an unbound parentless application wrapper is destroyed by the next GC,
# taking timer delivery and queued signal delivery with it).
app = QApplication.instance() or QApplication(["dragonflynotebook_install_smoke"])
app.setProperty("running_in_test", True)  # failure boxes log, never go modal


made = []
temp_dir = tempfile.mkdtemp(prefix="dfnb_smoke_")


def track(obj):
    made.append(obj)
    return obj


# The bridge the launcher starts: one per Dragonfly process, named after
# its pid so two Dragonfly instances never collide.
pluginInstance = track(DragonflyNotebookPlugin(varName=None))
service = installed_glue.get_bridge_service()
result(service is not None and service.is_listening,
       "get_bridge_service() started the bridge service")
result(service.pipe_name == "dfnb_bridge_{}".format(os.getpid()),
       "the pipe is pid-suffixed: {}".format(service.pipe_name))

# Round 9: the FIRST plugin instance installs the application-level .ipynb
# drop filter (Explorer drop -> launcher), idempotently; the filter is
# removed when the last instance goes. This suite runs under dfpy_ors, so
# the instance construction here is exactly the live first-click path.
drop_filter = installed_glue._DROP_FILTER
result(drop_filter is not None and isinstance(drop_filter, installed_glue._IpynbDropFilter),
       "the first plugin instance installed the application-level .ipynb "
       "drop filter")
installed_glue._ensure_drop_filter()
result(installed_glue._DROP_FILTER is drop_filter,
       "_ensure_drop_filter() is idempotent (the same filter object stays)")
installed_glue._remove_drop_filter()
result(installed_glue._DROP_FILTER is None,
       "_remove_drop_filter() removed the application-level filter")
installed_glue._remove_drop_filter()
result(installed_glue._DROP_FILTER is None,
       "_remove_drop_filter() with nothing installed stays a no-op")
installed_glue._ensure_drop_filter()
result(installed_glue._DROP_FILTER is not None
       and installed_glue._DROP_FILTER is not drop_filter,
       "_ensure_drop_filter() re-installs a fresh filter after a removal")

# The spawn the launcher issues, recorded instead of executed (the pin
# never spawns a real child here -- section 7 does, for real). The glue's
# module-global subprocess symbol is swapped for a stub capturing the ONE
# Popen call with its exact arguments.
real_subprocess = installed_glue.subprocess
spawns = []


class _SubprocessStub:
    DEVNULL = subprocess.DEVNULL
    DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)

    @staticmethod
    def Popen(*args, **kwargs):
        spawns.append((args, kwargs))
        return object()


installed_glue.subprocess = _SubprocessStub
try:
    pluginInstance.launch_notebook_app()
finally:
    installed_glue.subprocess = real_subprocess
    pluginInstance._child = None
result(len(spawns) == 1, "launch_notebook_app spawned exactly one child (recorded)")
# spawns[0] = (Popen's positional args tuple, its kwargs); the command list
# is the single positional argument.
positional, kwargs = spawns[0]
result(len(positional) == 1,
       "the Popen call carries exactly one positional argument (the "
       "command list)")
argv = list(positional[0])
installed_dir = os.path.dirname(installed_pkg.__file__)
result(list(argv) == [sys.executable,
                      os.path.join(installed_dir, "notebook_app.py"),
                      service.pipe_name],
       "the child is [embedded python, installed notebook_app.py, the pipe "
       "name]: {}".format(list(argv)))
result(kwargs.get("creationflags") == getattr(subprocess, "DETACHED_PROCESS", 0),
       "the child is spawned DETACHED_PROCESS (no console window)")
child_env = kwargs.get("env") or {}
result(child_env.get("KMP_DUPLICATE_LIB_OK") == "True"
       and "PYTHONPATH" not in child_env
       and "QT_PLUGIN_PATH" not in child_env,
       "the child env keeps KMP_DUPLICATE_LIB_OK and strips PYTHONPATH / "
       "QT_PLUGIN_PATH (the ambient ORS auto-imports must not cross)")
result(kwargs.get("stdin") == subprocess.DEVNULL
       and kwargs.get("stdout") == subprocess.DEVNULL
       and kwargs.get("stderr") == subprocess.DEVNULL
       and kwargs.get("close_fds") is True,
       "the child's stdin/stdout/stderr are DEVNULL (the launcher's choice)")

# One instance per click; the bridge closes when the last instance is gone.
installed_glue.close_bridge_service()
result(service.is_listening is False,
       "close_bridge_service() closed the launcher's bridge")

# ---------------------------------------------------------------------------
print("\n=== (3) the standalone form, headless, over a real bridge ===", flush=True)

# The suite's OWN bridge service (the section-2 launcher service is closed;
# the same pid-suffixed name re-listens) -- the form and the picker dialogs
# below talk to ORS through it exactly the way the spawned application
# would, and the spawned application of section 7 connects to it too.
bridge_service = None
form = None
try:
    bridge_service = installed_bridge.NotebookBridgeService(
        dict(installed_lib.BRIDGE_HANDLERS),
        discard_hook=installed_lib.discard_bridge_object)
    result(bridge_service.is_listening,
           "the suite's bridge service listens on {}".format(bridge_service.pipe_name))

    # The form, constructed the way notebook_app.py constructs it: a plain
    # QMainWindow with the bridge pipe (no openWidget, no plugin instance,
    # no implementation argument -- all retired with the process
    # separation).
    form = installed_form.DragonflyNotebookForm(pipe_name=bridge_service.pipe_name)
    form.show()
    app.processEvents()
    result(isinstance(form, QMainWindow), "the form is a plain QMainWindow")
    result(form.client.is_connected()
           and form.client.pipe_name == bridge_service.pipe_name,
           "the form's bridge client connected to the suite's service")
    result("Connected to Dragonfly" in form.status_label.text(),
           "the status reports the connection ({!r})".format(form.status_label.text()))
    result(form._disconnected_banner.isHidden(),
           "the disconnected banner is hidden while the bridge is up")

    # The scroll-area structure (the shipped-plugins overflow fix, shipped
    # from v1): the cells live in a resizable QScrollArea.
    aScrollArea = form.findChild(QScrollArea)
    result(aScrollArea is not None and aScrollArea.widgetResizable(),
           "the notebook cells sit in a resizable QScrollArea")

    # A fresh form is a fresh notebook: one empty code cell, NO kernel.
    result(form.document.cell_count() == 1,
           "a fresh notebook has exactly one cell (got {})".format(form.document.cell_count()))
    first_cell = form.document.get_cell(0)
    result(first_cell["cell_type"] == "code" and first_cell["source"] == "",
           "the fresh cell is an empty code cell")
    result(form.kernel.is_started is False,
           "no kernel at form construction (lazy start on the first run)")
    result(form._cell_widgets[0]["highlighter"] is not None,
           "the code cell editor carries a pygments highlighter")
    result(form._cell_widgets[0]["browser"].isHidden(),
           "the output area starts hidden (no outputs yet)")

    # Round 21 highlighter regression: a source that STARTS with blank
    # lines used to shift every token span against the document's real
    # block numbers (pygments' default stripnl deletes leading AND
    # trailing newlines before lexing, while the spans key on a block
    # index counted from the lexed text) -- import/def/class rendered
    # plain or with another line's color, and the wrong colors PERSISTED
    # until the leading blank line went. The honest ground truth is the
    # Qt-APPLIED format on the correct document block (block.layout()
    # .formats() after the deferred relex has run), never the internal
    # span map. The comment block is large so a one-block drift cannot
    # pass by accident.
    hl_source_body = '# leading explanation\n' * 60 + (
        'import os\n\n'
        'def compute(x):\n    return x + 1\n\n'
        'class Foo:\n    def bar(self):\n        pass\n')

    def _applied_color(editor, source, needle):
        position = source.find(needle)
        if position < 0:
            return None
        block_index = source.count('\n', 0, position)
        block = editor.document().firstBlock()
        index = 0
        while block.isValid() and index < block_index:
            block = block.next()
            index += 1
        if not block.isValid():
            return None
        offset = block.text().find(needle)
        if offset < 0:
            return None
        for fragment in block.layout().formats():
            if fragment.start <= offset < fragment.start + fragment.length:
                return fragment.format.foreground().color().name()
        return None

    def _hl_result(label, editor, source):
        failures = []
        for needle, expected in (
                ('import', '#c678dd'), ('def', '#c678dd'),
                ('compute', '#61afef'), ('class', '#c678dd'),
                ('Foo', '#e5c07b')):
            got = _applied_color(editor, source, needle)
            if got != expected:
                failures.append('{!r}: want {}, got {}'.format(
                    needle, expected, got))
        result(not failures, label + ' ({})'.format(
            '; '.join(failures) if failures else
            'import/def purple, compute blue, class purple, Foo amber'))

    # The edit path: a leading blank line typed at position 0 (one Enter
    # at the top of the cell) -- the mis-colored case.
    leading_source = '\n' + hl_source_body
    form._cell_widgets[0]["editor"].setPlainText(leading_source)
    app.processEvents()
    app.processEvents()
    _hl_result('a leading blank line does not shift the token colors '
               '(edit path)',
               form._cell_widgets[0]["editor"], leading_source)
    # The control: the SAME content without the leading newline also
    # colors correctly (proves the helper reads real applied formats,
    # not a constant).
    form._cell_widgets[0]["editor"].setPlainText(hl_source_body)
    app.processEvents()
    app.processEvents()
    _hl_result('the same content without the blank line colors correctly '
               '(control)',
               form._cell_widgets[0]["editor"], hl_source_body)
    form.document.is_dirty = False

    # The load path: a saved .ipynb whose first code cell source begins
    # with a newline must color correctly FROM OPEN (this used to render
    # mis-colored deterministically, no edit needed).
    hl_doc = installed_core.NotebookDocument.new_notebook()
    hl_doc.set_cell_source(0, leading_source)
    hl_path = os.path.join(temp_dir, "hl_leading_blank.ipynb")
    hl_doc.save(hl_path)
    form._load_notebook_document(hl_doc, hl_path)
    app.processEvents()
    app.processEvents()
    result(form.document.cell_count() == 1,
           "the highlighter load-path notebook opened with one cell")
    _hl_result('a leading blank line does not shift the token colors '
               '(loaded from a file)',
               form._cell_widgets[0]["editor"], leading_source)

    # The toolbar carries the notebook command set (the round-8 tools row
    # is a SECOND row -- the snippet/picker buttons -- not a toolbar
    # button, so 'tools' is gone from this set).
    expected_buttons = ("new", "open", "save", "add_code", "add_markdown",
                        "move_up", "move_down", "delete", "run_all",
                        "restart", "clear_outputs")
    result(tuple(sorted(form._toolbar_buttons)) == tuple(sorted(expected_buttons)),
           "toolbar buttons: {}".format(", ".join(sorted(form._toolbar_buttons))))

    # The toolbar restyle made the buttons a uniform height (setFixedHeight).
    form.resize(900, 700)
    app.processEvents()
    result(all(button.height() >= 24 for button in form._toolbar_buttons.values()),
           "the toolbar buttons have a uniform height (>= 24)")

    # The per-run BUSY bar (round 8, replacing the round-7 progress
    # dialog): indeterminate -- range (0, 0) -- with the percentage text
    # hidden outright; hidden until a run starts.
    busy_bar = form.busy_bar
    result(busy_bar.objectName() == "busy_bar", "the busy bar's objectName")
    result(busy_bar.minimum() == 0 and busy_bar.maximum() == 0,
           "the busy bar's range is (0, 0) -- indeterminate, NO numbers")
    result(busy_bar.isTextVisible() is False,
           "the busy bar hides its text (no percentage can render)")
    result(busy_bar.isHidden(), "the busy bar is hidden while idle")

    # The end-of-list add buttons follow the running state, and adding a
    # cell mid-run is refused (a rebuild mid-run would tear down the
    # running cell's widgets -- the runs are asynchronous now, so the
    # guard is what keeps them alive).
    form._set_running_ui(True)
    end_buttons_disabled = all(button.isEnabled() is False
                               for button in form._end_row_buttons)
    form._set_running_ui(False)
    result(end_buttons_disabled
           and all(button.isEnabled() for button in form._end_row_buttons),
           "the end-of-list add buttons follow the running state")
    form._running = True
    form._add_cell_below('code')
    result(form.document.cell_count() == 1,
           "adding a cell mid-run is refused by the guard")
    form._running = False

    # Round 7's per-cell Stop buttons are still gone (no cell record
    # carries a stop_button key, no QPushButton is named 'stop_button') --
    # and round 8's stop affordance is the RUNNING cell's own Run button,
    # which flips into the red Stop (objectName cancel_button) while its
    # cell runs. The elapsed footer stays per cell.
    records = form._cell_widgets
    result(all('stop_button' not in r for r in records),
           "no cell record carries a stop_button (rounds 7/8)")
    result(all(b.objectName() != 'stop_button'
               for b in form.findChildren(QPushButton)),
           "no QPushButton named 'stop_button' exists in the form")
    result(all(r['elapsed_label'].objectName() == 'cell_elapsed'
               and r['elapsed_label'].isHidden() for r in records),
           "every cell carries an elapsed footer, hidden until first run")
    stylesheet = form.styleSheet()
    result('QPushButton#cancel_button' in stylesheet
           and 'QPushButton#stop_button' not in stylesheet
           and 'QLabel#cell_elapsed' in stylesheet
           and 'QProgressBar#busy_bar' in stylesheet
           and 'QLabel#disconnected_banner' in stylesheet,
           "the form stylesheet carries the #cancel_button, #busy_bar and "
           "#disconnected_banner rules, no #stop_button rules, plus the "
           "scoped elapsed rule")
    result(installed_form.format_elapsed_live(2.4) == "0:02.4"
           and installed_form.format_elapsed_live(65.3) == "1:05.3",
           "format_elapsed_live renders minutes:seconds.tenths")
    result(installed_form.format_elapsed(2.31) == "2.31 s",
           "format_elapsed renders the pinned final 'S.SS s'")

    # Structural edits through the REAL form methods (a second cell so the
    # Stop-flip pin can also see the NON-running cell's button disabled).
    form.on_add_code_cell()
    result(form.document.cell_count() == 2, "add_code_cell -> 2 cells")
    form._running = True
    form._running_cell_index = 0
    form._set_running_ui(True)
    result(form._cell_widgets[0]['run_button'].objectName() == 'cancel_button'
           and form._cell_widgets[0]['run_button'].text() == 'Stop'
           and form._cell_widgets[0]['run_button'].isEnabled(),
           "the RUNNING cell's Run button flipped into the red Stop "
           "(objectName cancel_button)")
    result(form._cell_widgets[1]['run_button'].objectName() == 'run_button'
           and form._cell_widgets[1]['run_button'].isEnabled() is False
           and all(button.isEnabled() is False
                   for button in form._toolbar_buttons.values()),
           "every OTHER run affordance disables for the run (the second "
           "cell's Run button and the whole toolbar)")
    form._running = False
    form._running_cell_index = -1
    form._set_running_ui(False)
    result(form._cell_widgets[0]['run_button'].objectName() == 'run_button'
           and form._cell_widgets[0]['run_button'].text() == 'Run',
           "the Stop flips back to Run when the run ends")

    form._focus_cell(0)
    form._cell_widgets[0]["editor"].setPlainText("2 + 3")
    form.on_move_cell_down()
    result(form.document.get_cell(1)["source"] == "2 + 3",
           "move down carried the typed source with the cell")
    form._focus_cell(0)  # the moved cell took focus; delete the empty one
    form.on_delete_cell()
    result(form.document.cell_count() == 1 and
           form.document.get_cell(0)["source"] == "2 + 3",
           "delete removed the empty cell, the typed one survived")

    # Save through the REAL form method (path pre-set: no Save As dialog),
    # then reload through the core and compare.
    notebook_path = os.path.join(temp_dir, "roundtrip.ipynb")
    form.file_path = notebook_path
    saved = form.on_save_notebook()
    result(saved is True and os.path.isfile(notebook_path),
           "on_save_notebook wrote {}".format(os.path.basename(notebook_path)))
    reloaded = installed_core.NotebookDocument.from_file(notebook_path)
    result(reloaded.cell_count() == 1 and
           reloaded.get_cell(0)["source"] == "2 + 3",
           "the saved .ipynb reloads with the typed source")
    result(form.document.is_dirty is False, "save cleared the dirty flag")
    result(form._cell_widgets[0]["prompt_label"].text() == "In [ ]:",
           "a fresh unexecuted code cell shows the 'In [ ]:' prompt")
    form.document.record_execution(0, 3, [])
    form._update_cell_header(0)
    result(form._cell_widgets[0]["prompt_label"].text() == "In [3]:",
           "an executed cell's prompt reads 'In [3]:'")

    # The output renderer: streams, a red traceback, a base64 figure and a
    # corrupt-payload fallback (pure presentation -- no kernel involved).
    html_out = installed_cells.render_outputs_html([
        {"output_type": "stream", "name": "stdout", "text": "hello <b>world</b>"},
        {"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
         "traceback": ["Traceback (most recent call last):", "  boom"]},
        {"output_type": "display_data",
         "data": {"image/png": "iVBORw0KGgoAAAANSUhEUg==", "text/plain": "<Figure 1>"},
         "metadata": {}},
        {"output_type": "execute_result", "data": {"text/plain": "'done'"},
         "metadata": {}, "execution_count": 3},
    ])
    result("hello &lt;b&gt;world&lt;/b&gt;" in html_out, "streams render HTML-escaped")
    result("#F14C4C" in html_out and "ZeroDivisionError: division by zero" in html_out,
           "errors render red with ename: evalue")
    result("data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==" in html_out,
           "figures embed as base64 data URLs")
    result("<Figure 1>" not in html_out, "a figure's '<Figure>' text twin is suppressed")
    result("&#x27;done&#x27;" in html_out,
           "execute_result renders its text/plain (HTML-escaped quotes)")
    corrupt_html = installed_cells.render_outputs_html([
        {"output_type": "display_data",
         "data": {"image/png": "!!!not base64!!!", "text/plain": "fallback"},
         "metadata": {}}])
    result("fallback" in corrupt_html and "data:image/png" not in corrupt_html,
           "a corrupt image payload falls back to the text/plain")

    # Round 20, the nbformat multiline payloads: every text field may be a
    # str OR a LIST of str lines (the multiline form Jupyter writes -- the
    # foreign-notebook crash class this round closes). The renderer joins
    # the lines and never raises on a payload shape.
    list_stream_html = installed_cells.render_outputs_html([
        {"output_type": "stream", "name": "stdout",
         "text": ["hello\n", "world\n"]}])
    result("hello" in list_stream_html and "world" in list_stream_html,
           "a list-typed stream text renders both lines (the exact crash "
           "shape: str methods on a list used to raise)")
    list_plain_html = installed_cells.render_outputs_html([
        {"output_type": "display_data",
         "data": {"text/plain": ["plain one\n", "plain two\n"]},
         "metadata": {}}])
    result("plain one" in list_plain_html and "plain two" in list_plain_html,
           "a list-typed text/plain renders its joined text")
    tiny_png = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    tiny_png_bytes = base64.b64decode(tiny_png)
    list_png_html = installed_cells.render_outputs_html([
        {"output_type": "display_data",
         "data": {"image/png": [tiny_png[:40] + "\n", tiny_png[40:]],
                  "text/plain": "<Figure>"},
         "metadata": {}}])
    png_match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)",
                          list_png_html)
    result(png_match is not None
           and base64.b64decode(png_match.group(1)) == tiny_png_bytes,
           "a list-typed image/png embeds byte-exact (the lines are joined "
           "BEFORE the whitespace strip, so no bracket or quote survives "
           "into the payload)")

    # The ORS object-drag payload parser (the console's 'guids' XML shape,
    # read with ast.literal_eval instead of eval): the cross-process OS
    # drag from Dragonfly's object panel into the notebook window still
    # lands here (notebook_cells is unchanged by round 8).
    result(installed_cells.guids_from_ors_xml(
        b"<payload><guids>['1111AAAA', '2222BBBB']</guids></payload>")
        == ["1111AAAA", "2222BBBB"],
        "the ORS drag payload parses its guids list")
    result(installed_cells.guids_from_ors_xml(b"not xml") == []
           and installed_cells.guids_from_ors_xml(
               b"<payload><guids>[1, 2]</guids></payload>") == ["1", "2"],
           "a non-XML payload is empty; non-str guids coerce to str")

    # The tools row's insert fast path: insert_snippet_into_current_cell
    # writes at the cursor of the focused cell and refuses to guess
    # without one.
    form._focus_cell(0)
    form._cell_widgets[0]["editor"].setPlainText("")
    inserted = form.insert_snippet_into_current_cell("a = dfnb.probe()")
    result(inserted is True and
           form._cell_widgets[0]["editor"].toPlainText() == "a = dfnb.probe()",
           "insert_snippet_into_current_cell wrote at the focused cell's cursor")
    form._current_cell_index = -1
    result(form.insert_snippet_into_current_cell("x = 1") is False,
           "insert without a focused cell is refused (status note, no write)")

    # OPEN -> edit -> save -> reload through the REAL form methods: a
    # two-cell .ipynb built by the core opens into the form, the code cell
    # is edited through its editor, the save writes it, and an independent
    # reload sees the edit.
    opened = installed_core.NotebookDocument.new_notebook()  # seeds one code cell
    opened.set_cell_source(0, "2 + 3")
    opened.add_cell("markdown", index=0, source="# Probe heading")
    result(opened.cell_count() == 2,
           "the open fixture built 2 cells (got {})".format(opened.cell_count()))
    open_path = os.path.join(temp_dir, "openme.ipynb")
    opened.save(open_path)
    form.document = installed_core.NotebookDocument.from_file(open_path)
    form.file_path = open_path
    form._rebuild_cells()
    result(form.document.cell_count() == 2 and len(form._cell_widgets) == 2,
           "the opened notebook rebuilt 2 cell widgets")
    result(form.cellsLayout.count() == form.document.cell_count() + 2,
           "rebuild invariant: cells + the trailing stretch + the end-of-list "
           "row ({} items for {} cells)".format(form.cellsLayout.count(),
                                                form.document.cell_count()))
    result(form._cell_widgets[0]["prompt_label"].isHidden()
           and not form._cell_widgets[1]["prompt_label"].isHidden(),
           "the markdown cell's prompt is hidden, the code cell's is not")
    result(form._cell_widgets[0]["editor"].toPlainText() == "# Probe heading",
           "the markdown cell's editor carries its source")
    result(not form._cell_widgets[0]["browser"].isHidden(),
           "the markdown cell shows its rendered preview")
    result(form._cell_widgets[1]["editor"].toPlainText() == "2 + 3",
           "the code cell's editor carries its source")
    form._cell_widgets[1]["editor"].setPlainText("6 * 7")
    saved_again = form.on_save_notebook()
    reopened = installed_core.NotebookDocument.from_file(open_path)
    result(saved_again is True and reopened.get_cell(1)["source"] == "6 * 7",
           "edit -> save -> reload round-trip through the real form methods")

    # Round 11, the markdown cell modes (the sibling of the open case
    # above): a markdown cell is EDIT (source editor visible, rendered
    # browser hidden) or RENDERED (editor hidden, the browser showing the
    # markdown, a top-right '✎ Edit' button flipping back). The mode is
    # keyed by the CELL's id in a form set -- not by the widget index --
    # so the teardown/rebuild of every structural edit carries the modes
    # through with zero bookkeeping, and nothing lands in the .ipynb (the
    # reader/writer bytes stay pinned): a LOADED notebook opens RENDERED,
    # a freshly ADDED markdown cell starts EDIT.
    modes_doc = installed_core.NotebookDocument.new_notebook()
    modes_doc.set_cell_source(0, "1 + 1")
    modes_doc.add_cell("markdown", source="# Modes heading")
    modes_doc.add_cell("code", source="2 + 2")
    modes_doc.add_cell("markdown", source="## Second heading")
    modes_doc.add_cell("raw", source="plain raw text, never a mode target")
    modes_path = os.path.join(temp_dir, "modes.ipynb")
    modes_doc.save(modes_path)
    form.document = installed_core.NotebookDocument.from_file(modes_path)
    form.file_path = modes_path
    form._rebuild_cells()
    app.processEvents()
    result(form.document.cell_count() == 5
           and form.document.get_cell(1)["cell_type"] == "markdown"
           and form.document.get_cell(3)["cell_type"] == "markdown"
           and form.document.get_cell(4)["cell_type"] == "raw",
           "the modes fixture built 5 cells with markdown at 1 and 3 and "
           "raw at 4")
    md_first = form._cell_widgets[1]
    md_second = form._cell_widgets[3]
    result(md_first["editor"].isHidden()
           and not md_first["browser"].isHidden()
           and md_second["editor"].isHidden()
           and not md_second["browser"].isHidden(),
           "loaded markdown cells open RENDERED (editor hidden, browser "
           "showing)")
    # Round 12: the rendered mode is also FLAT-CHROME -- no run button, no
    # type label, no accent bar, and the dfnb_rendered property the
    # stylesheet's flat rules match on, on BOTH the frame and the browser.
    result(all(record["run_button"].isHidden()
               and record["type_label"].isHidden()
               and record["cell_bar"].isHidden()
               and record["frame"].property("dfnb_rendered") is True
               and record["browser"].property("dfnb_rendered") is True
               for record in (md_first, md_second)),
           "loaded RENDERED markdown cells carry the flat chrome (run "
           "button, type label and accent bar hidden, dfnb_rendered on "
           "frame and browser)")
    result(not form._cell_widgets[0]["frame"].property("dfnb_rendered")
           and not form._cell_widgets[0]["browser"].property("dfnb_rendered")
           and not form._cell_widgets[2]["frame"].property("dfnb_rendered")
           and not form._cell_widgets[2]["browser"].property("dfnb_rendered")
           and not form._cell_widgets[4]["frame"].property("dfnb_rendered")
           and not form._cell_widgets[4]["browser"].property("dfnb_rendered")
           and not form._cell_widgets[0]["run_button"].isHidden()
           and not form._cell_widgets[4]["run_button"].isHidden(),
           "code and raw cells never carry the rendered property and keep "
           "their run buttons visible")
    result(md_first["edit_button"].objectName() == "markdown_edit_button"
           and not md_first["edit_button"].isHidden()
           and not md_second["edit_button"].isHidden(),
           "the loaded markdown cells carry a visible '✎ Edit' button "
           "(objectName markdown_edit_button)")
    result(QFontMetrics(md_first["edit_button"].font()).inFont("\u270e"),
           "the button's font carries the pencil glyph U+270E (no tofu: "
           "the vision pass cannot see the glyph when no markdown cell "
           "is in frame, so the font is pinned directly; probe-confirmed "
           "on the app's offscreen font)")
    raw_record = form._cell_widgets[4]
    result(form._cell_widgets[0]["edit_button"] is None
           and form._cell_widgets[2]["edit_button"] is None
           and raw_record["edit_button"] is None,
           "code cells carry no edit button; raw cells neither")
    result(not raw_record["editor"].isHidden()
           and not raw_record["browser"].isHidden(),
           "a raw cell keeps the plain build (editor and preview both "
           "visible, no mode machinery -- round 11 changed markdown only)")
    result("QPushButton#markdown_edit_button" in form.styleSheet()
           and "QPushButton#markdown_edit_button:hover" in form.styleSheet(),
           "the form stylesheet carries the #markdown_edit_button rules "
           "(round 11, in the run/cancel rule family)")
    # Round 12: the flat-chrome rules -- one neutralizing variant per
    # existing QFrame[dfnb_cell] state combination, plus the rendered
    # browser rule. The base rules must stay verbatim beside them: the
    # browser base rule is pinned at the dark-theme section below, the
    # frame base rules right here.
    result('QFrame[dfnb_cell="true"][dfnb_rendered="true"]' in form.styleSheet()
           and 'QFrame[dfnb_cell="true"][dfnb_rendered="true"][hovered="true"]'
           in form.styleSheet()
           and 'QFrame[dfnb_cell="true"][dfnb_rendered="true"][current="true"]'
           in form.styleSheet()
           and 'QTextBrowser#cell_output[dfnb_rendered="true"]'
           in form.styleSheet(),
           "the form stylesheet carries the dfnb_rendered flat-chrome rules "
           "(round 12: base, hovered, current and the rendered browser)")
    result('background: #252526; border: 1px solid #3C3C3C; '
           'border-radius: 6px;' in form.styleSheet()
           and 'QFrame[dfnb_cell="true"][hovered="true"] '
           '{ border-color: #4A4A4A; }' in form.styleSheet()
           and 'QFrame[dfnb_cell="true"][current="true"] '
           '{ border-color: #4F94CD; }' in form.styleSheet(),
           "the BASE cell-frame rules stay verbatim beside the rendered "
           "variants (cell background + border, the hover and current "
           "border colors)")

    # The flip back: clicking the button is EDIT -- editor visible, browser
    # hidden, the button itself hidden (QAbstractButton.click() emits
    # clicked synchronously; no event loop needed).
    md_first["edit_button"].click()
    result(not md_first["editor"].isHidden()
           and md_first["browser"].isHidden()
           and md_first["edit_button"].isHidden(),
           "the edit button flips the cell back to EDIT (editor shown, "
           "browser and button hidden)")
    # Round 12: the flip also restores the full chrome and hands the
    # keyboard to the editor -- the cell is fully runnable again.
    result(not md_first["run_button"].isHidden()
           and not md_first["type_label"].isHidden()
           and not md_first["cell_bar"].isHidden()
           and not md_first["frame"].property("dfnb_rendered")
           and not md_first["browser"].property("dfnb_rendered"),
           "the EDIT flip restores the full chrome (run button, type "
           "label and accent bar visible, dfnb_rendered falsy on frame "
           "and browser)")
    # Round 16, the focus discipline: every focus pin below asserts the
    # WINDOW's focus target (``form.focusWidget()`` -- the widget the
    # flip's setFocus() armed, which receives the keyboard the moment the
    # window is active), never ``QApplication.focusWidget()`` (the app-
    # level focus exists only while the OS grants this process the
    # foreground -- a desktop state the suite cannot control; on a live
    # desktop with another application holding the foreground, Qt's
    # optimistic activeWindow collapses at the first focus-widget
    # deletion and every app-level pin dies with it, probe-confirmed).
    # The user-facing contract is the window-level one: a human clicking
    # the ✎ button HAS the window active, so the armed target IS the
    # keyboard owner.
    result(form.focusWidget() is md_first["editor"],
           "the ✎ Edit flip leaves the keyboard on the editor")
    result(md_first["editor"].toPlainText() == "# Modes heading",
           "the EDIT editor still carries the cell's source")

    # Running the markdown cell renders it again -- the browser is re-set
    # from the editor's text (the rendered view is the editor's truth; the
    # markdown round-trip is compared by content, the way the source pins
    # above compare editor text).
    form._run_cell_at(1)
    result(md_first["editor"].isHidden()
           and not md_first["browser"].isHidden()
           and not md_first["edit_button"].isHidden()
           and "Modes heading" in md_first["browser"].toMarkdown()
           and form.focusWidget() is md_first["browser"],
           "running the markdown cell renders it (editor hidden, browser "
           "re-set from the editor text, edit button back, focus ON the "
           "browser -- round 12 places it deterministically)")

    # Round 12, the inert heart: a RENDERED markdown cell consumes the run
    # chords outright. The synthetic key events go straight to the browser
    # (the same named-binding discipline as the drag events above: hand-
    # built Qt events, posted then pumped through the loop). Ctrl+Enter
    # does NOTHING: no kernel start, no execution count, no source touch,
    # the cell stays rendered with the focus still on its browser.
    ctrl_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                           Qt.KeyboardModifier.ControlModifier)
    QApplication.postEvent(md_first["browser"], ctrl_enter)
    app.processEvents()
    result(form._running is False
           and all(form.document.get_cell(i).get("execution_count") is None
                   for i in range(form.document.cell_count()))
           and form.document.get_cell(1)["source"] == "# Modes heading"
           and md_first["editor"].isHidden()
           and not md_first["browser"].isHidden()
           and form.focusWidget() is md_first["browser"],
           "a Ctrl+Enter posted to the rendered browser does NOTHING (no "
           "run, no insert -- the browser consumes the chord)")
    # Round 13: Shift+Enter is the ONE chord that acts -- the filter still
    # consumes it, but emits skipForward through the REAL installed wiring
    # (this posted-event pin is also the proof the form connected the
    # signal): the form advances to the next EDITABLE cell -- the code cell
    # at 2, the rendered markdown itself stepped over -- and focus lands on
    # that cell's editor. Nothing runs.
    shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                            Qt.KeyboardModifier.ShiftModifier)
    QApplication.postEvent(md_first["browser"], shift_enter)
    app.processEvents()
    result(form._running is False
           and all(form.document.get_cell(i).get("execution_count") is None
                   for i in range(form.document.cell_count()))
           and form.document.get_cell(1)["source"] == "# Modes heading"
           and md_first["editor"].isHidden()
           and not md_first["browser"].isHidden()
           and form._current_cell_index == 2
           and form.focusWidget() is form._cell_widgets[2]["editor"],
           "a Shift+Enter posted to the rendered browser runs NOTHING but "
           "ADVANCES: the current cell becomes the next editable one (the "
           "code cell at 2 -- the rendered markdown is skipped) and its "
           "editor takes the keyboard (round 13)")

    # The filter's own consumption, pinned at the mechanism level (the
    # posted chords above pin the OUTCOME on a real browser; this pins the
    # class itself, which a read-only QTextBrowser alone cannot prove):
    # every run chord dies, everything else passes through -- plain
    # Return, and Ctrl+S (the form's window-level save shortcut) included.
    # Fresh QKeyEvents: the two posted above are Qt-owned and deleted
    # after their delivery.
    chord_filter = installed_cells._RenderedMarkdownKeyFilter(
        md_first["browser"])
    # Round 13: the emit half -- a recorder on the fresh filter hears
    # exactly ONE skipForward (the Shift call below); the Ctrl and Alt
    # re-checks afterwards emit nothing more.
    skips = []
    chord_filter.skipForward.connect(lambda: skips.append(True))
    filt_ctrl = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                          Qt.KeyboardModifier.ControlModifier)
    filt_shift = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Enter,
                           Qt.KeyboardModifier.ShiftModifier)
    filt_alt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                         Qt.KeyboardModifier.AltModifier)
    filt_plain = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                           Qt.KeyboardModifier.NoModifier)
    filt_ctrl_s = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_S,
                            Qt.KeyboardModifier.ControlModifier)
    result(chord_filter.eventFilter(md_first["browser"], filt_ctrl) is True
           and chord_filter.eventFilter(md_first["browser"], filt_shift)
           is True
           and chord_filter.eventFilter(md_first["browser"], filt_alt)
           is True
           and chord_filter.eventFilter(md_first["browser"], filt_plain)
           is False
           and chord_filter.eventFilter(md_first["browser"], filt_ctrl_s)
           is False,
           "the rendered-markdown key filter consumes exactly the run "
           "chords (Ctrl/Shift/Alt+Enter) and passes everything else "
           "through -- plain Return and Ctrl+S included")
    result(skips == [True]
           and chord_filter.eventFilter(md_first["browser"], filt_ctrl)
           is True
           and chord_filter.eventFilter(md_first["browser"], filt_alt)
           is True
           and skips == [True],
           "the Shift chord -- and only the Shift chord -- emits "
           "skipForward exactly once (the Ctrl and Alt re-checks emit "
           "nothing; the events are hand-called, so safe to reuse)")

    # the COMBINED run chord carries the Shift bit too: consumed like
    # every run chord AND it emits skipForward -- pinned so a future
    # modifier refactor cannot silently drop the Shift-in-the-chord case
    # (the filter keys on the Shift bit, not on the exact modifier set).
    filt_ctrl_shift = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier
        | Qt.KeyboardModifier.ShiftModifier)
    result(chord_filter.eventFilter(md_first["browser"], filt_ctrl_shift)
           is True
           and skips == [True, True],
           "the combined Ctrl+Shift+Enter chord is consumed like every "
           "run chord AND emits skipForward (the Shift bit is what the "
           "filter keys on -- round 13)")

    # A repeat run on the ALREADY-RENDERED cell is a no-op: no re-render
    # (the browser's markdown is byte-identical), no source touch, still
    # rendered. Round 13 shifted the focus this pin finds: the Shift
    # advance above moved it to the next editable cell's editor, and the
    # no-op run leaves it exactly there.
    rendered_before = md_first["browser"].toMarkdown()
    form._run_cell_at(1)
    result(md_first["browser"].toMarkdown() == rendered_before
           and md_first["editor"].isHidden()
           and not md_first["browser"].isHidden()
           and form.focusWidget() is form._cell_widgets[2]["editor"],
           "a repeat _run_cell_at on the rendered cell is a no-op (no "
           "re-render, still rendered; focus untouched on the advanced "
           "cell's editor -- round 13)")

    # Frame-click selection: a press on a rendered cell's frame makes it
    # the current cell and focuses its browser -- the only selection
    # surface a rendered cell has (its editor is hidden). The press is a
    # hand-built QMouseEvent delivered synchronously through sendEvent.
    clicked = []
    md_first["frame"].frameClicked.connect(lambda: clicked.append(True))
    form._current_cell_index = -1  # prove the click, not a stale index
    frame_press = QMouseEvent(QEvent.Type.MouseButtonPress,
                              QPointF(10.0, 10.0),
                              Qt.MouseButton.LeftButton,
                              Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(md_first["frame"], frame_press)
    app.processEvents()
    result(clicked == [True] and form._current_cell_index == 1
           and form.focusWidget() is md_first["browser"],
           "a press on a rendered cell's frame emits frameClicked, selects "
           "the cell and focuses its browser")
    # An EDIT/code frame click only selects -- no focus steal.
    form._on_frame_clicked(0)
    result(form._current_cell_index == 0
           and form.focusWidget() is md_first["browser"],
           "a frame click on a code cell only selects it (no focus steal "
           "from the rendered browser)")

    # A freshly ADDED markdown cell starts in EDIT: you write first, run to
    # render (its id joins the EDIT set before the widget is built).
    form._focus_cell(0)
    form.on_add_markdown_cell()
    app.processEvents()
    added = form._cell_widgets[1]  # inserted right below the focused cell 0
    result(form.document.cell_count() == 6
           and form.document.get_cell(1)["cell_type"] == "markdown"
           and form.document.get_cell(1)["id"] in form._markdown_edit_ids
           and not added["editor"].isHidden()
           and added["browser"].isHidden()
           and added["edit_button"].isHidden()
           and not added["run_button"].isHidden()
           and not added["frame"].property("dfnb_rendered")
           and not added["browser"].property("dfnb_rendered"),
           "the freshly added markdown cell starts in EDIT (editor shown, "
           "browser and edit button hidden, id in the EDIT set, full "
           "chrome -- run button visible, no rendered property)")

    # Round 13: Shift+Enter advancing from an EDIT markdown cell SKIPS the
    # RENDERED markdown right below it and lands on the next EDITABLE cell
    # -- the code cell at 3 -- focus on its editor (the advance never
    # stalls on a rendered cell and never lands on one). The advance run
    # renders the source cell; the flip afterwards restores it to EDIT for
    # the survival pin below.
    form._run_cell_at(1, advance=True)
    app.processEvents()
    result(form.focusWidget() is form._cell_widgets[3]["editor"]
           and form._current_cell_index == 3,
           "Shift+Enter advancing from the EDIT markdown SKIPS the rendered "
           "markdown at 2 and lands on the next EDITABLE cell (the code "
           "cell at 3), focus on its editor (round 13)")
    form._on_markdown_edit_button_clicked(1)
    result(form.document.get_cell(1)["id"] in form._markdown_edit_ids
           and not form._cell_widgets[1]["editor"].isHidden(),
           "the source cell is flipped back to EDIT for the survival pin "
           "below (the ✎ flip revives a rendered cell)")

    # Round 13, the skip machinery pinned directly: _next_editable_index
    # skips RENDERED markdowns, treats EDIT-mode markdown and raw cells as
    # valid stops, and reports None when no editable cell follows. The
    # cells stand at [0] code, [1] EDIT markdown, [2] RENDERED markdown,
    # [3] code, [4] RENDERED markdown, [5] raw.
    result(form._next_editable_index(1) == 3
           and form._next_editable_index(0) == 1
           and form._next_editable_index(3) == 5
           and form._next_editable_index(5) is None,
           "_next_editable_index skips the rendered markdown at 2 (lands "
           "on the code cell at 3), takes the EDIT markdown at 1 as a "
           "valid target, skips the rendered markdown at 4 to the RAW "
           "cell at 5, and returns None past the end")

    # The edit buttons follow the run-disable sweep with the other
    # run-adjacent affordances (and come back when the run ends).
    form._set_running_ui(True)
    edit_disabled_midrun = all(
        record["edit_button"] is None
        or record["edit_button"].isEnabled() is False
        for record in form._cell_widgets)
    form._set_running_ui(False)
    result(edit_disabled_midrun
           and all(record["edit_button"] is None
                   or record["edit_button"].isEnabled()
                   for record in form._cell_widgets),
           "the markdown edit buttons follow the running state (disabled "
           "mid-run, back after)")

    # Mode survival across a structural rebuild: the two markdown cells in
    # EDIT stay EDIT after a THIRD cell is deleted through the form's real
    # delete handler -- the pin that holds the id-keyed set (an index-keyed
    # mode would survive only by luck of the numbering).
    form._on_markdown_edit_button_clicked(2)  # the loaded markdown, at 2 now
    form._focus_cell(0)
    form.on_delete_cell()
    app.processEvents()
    survivor_new = form._cell_widgets[0]   # the added markdown, was index 1
    survivor_old = form._cell_widgets[1]   # the loaded markdown, was index 2
    result(form.document.cell_count() == 5
           and form.document.get_cell(0)["cell_type"] == "markdown"
           and form.document.get_cell(1)["cell_type"] == "markdown"
           and not survivor_new["editor"].isHidden()
           and not survivor_old["editor"].isHidden()
           and survivor_new["browser"].isHidden()
           and survivor_old["browser"].isHidden()
           and survivor_new["edit_button"].isHidden()
           and survivor_old["edit_button"].isHidden()
           and form.document.get_cell(0)["id"] in form._markdown_edit_ids
           and form.document.get_cell(1)["id"] in form._markdown_edit_ids,
           "both markdown survivors are STILL in EDIT after the delete's "
           "rebuild (the mode set is keyed by cell id, not index)")

    # The section's open-failure pins below expect the form still holding
    # the openme notebook this case's sibling left it with: restore the
    # shared form state (the reloaded file carries the edited '6 * 7')
    # before moving on -- the mode set's leftover ids cannot collide (ids
    # are fresh per cell) and no pin downstream reads them.
    form.document = installed_core.NotebookDocument.from_file(open_path)
    form.file_path = open_path
    form._rebuild_cells()
    app.processEvents()

    # The open-failure paths (both families) leave the current notebook
    # untouched and surface ONE failure entry each, titled with the plugin
    # display name: a corrupt file raises ValueError, a missing/locked/
    # unreadable one raises OSError -- and an exception escaping this Qt
    # slot would be FATAL to the whole application (PyQt6 aborts on
    # unhandled slot exceptions). Under running_in_test the entry is a LOG
    # record on the form logger (the box seam logs instead of showing), so
    # the pin counts records -- a headless gate can never hang on a dialog.
    form_logger = logging.getLogger("DragonflyNotebook")
    log_handler = _RecordingLogHandler()
    form_logger.addHandler(log_handler)
    try:
        corrupt_path = os.path.join(temp_dir, "corrupt.ipynb")
        with open(corrupt_path, "w", encoding="utf-8") as handle:
            handle.write("{definitely not json")
        missing_path = os.path.join(temp_dir, "does_not_exist.ipynb")
        records_before = len(log_handler.records)
        corrupt_loaded = form._open_document_or_report(corrupt_path)
        missing_loaded = form._open_document_or_report(missing_path)
        entries = log_handler.records[records_before:]
        result(corrupt_loaded is None and missing_loaded is None,
               "a corrupt and a missing notebook both load to None")
        result(len(entries) == 2
               and all(entry.startswith("Dragonfly Notebook:") for entry in entries)
               and all("could not be opened" in entry for entry in entries),
               "each open failure logged exactly ONE 'Dragonfly Notebook' "
               "entry naming the open failure ({} entries)".format(len(entries)))
        result(form.document.cell_count() == 2 and form.file_path == open_path,
               "a failed open leaves the current notebook and path untouched")

        # The save-failure path honors the same seam: with running_in_test
        # the failure is LOGGED, never shown modal -- a headless gate
        # hitting a real save failure must not hang on a dialog.
        def _raising_save(_path):
            raise OSError('the save-failure pin (simulated disk failure)')

        form.document.save = _raising_save
        try:
            save_failed = form.on_save_notebook()
        finally:
            del form.document.save  # un-shadow: the class method again
        result(save_failed is False, "a failed save returns False")
        result("could not be saved" in form.status_label.text(),
               "the status line names the save failure")
        save_entries = log_handler.records[records_before + len(entries):]
        result(len(save_entries) == 1
               and "could not be saved" in save_entries[0],
               "the save failure logged exactly ONE entry ({} entries)"
               .format(len(save_entries)))
    finally:
        form_logger.removeHandler(log_handler)

    # Round 20, the real open chain on a foreign-shaped notebook: a raw
    # nbformat 4.5 file whose stream output carries the LIST text form
    # (the multiline spelling Jupyter writes -- the exact crash class
    # this round closes) opens through the REAL chain,
    # NotebookDocument.from_file + _load_notebook_document, exactly the
    # section-9 construction, and the cell browser shows the stream text.
    list_notebook_json = (
        '{"cells": [{"cell_type": "code", "execution_count": 1, '
        '"id": "liststream01", "metadata": {}, "outputs": [{"name": '
        '"stdout", "output_type": "stream", "text": ["from ", '
        '"the list form\\n"]}], "source": ["print(\'hi\')\\n"]}], '
        '"metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    list_path = os.path.join(temp_dir, "list_stream.ipynb")
    with open(list_path, "w", encoding="utf-8") as handle:
        handle.write(list_notebook_json)
    list_document = installed_core.NotebookDocument.from_file(list_path)
    result(list_document.cell_count() == 1,
           "the list-payload notebook loads through the real reader")
    form._load_notebook_document(list_document, list_path)
    app.processEvents()
    result(form.document is list_document and form.file_path == list_path,
           "the open chain kept the list-payload document (no fallback)")
    result(form.document.get_cell(0)["outputs"][0]["text"]
           == ["from ", "the list form\n"],
           "the stored outputs stay verbatim (the list form is not rewritten)")
    result("from" in form._cell_widgets[0]["browser"].toPlainText()
           and "the list form" in form._cell_widgets[0]["browser"].toPlainText(),
           "the code cell's browser shows the list-typed stream text")

    # Round 20, containment: even a renderer that RAISES cannot kill the
    # window on the open path. The monkeypatch simulates any renderer
    # fault; the open must fall back to a fresh empty notebook with
    # exactly ONE user-facing failure entry (the _show_message convention
    # -- under running_in_test a log record) and no escaping exception.
    def _raising_render(_outputs):
        raise RuntimeError('boom')

    original_renderer = installed_form.render_outputs_html
    log_handler_r20 = _RecordingLogHandler()
    form_logger.addHandler(log_handler_r20)
    try:
        installed_form.render_outputs_html = _raising_render
        contained_doc = installed_core.NotebookDocument.new_notebook()
        contained_doc.set_cell_source(0, "1 + 1")
        contained_doc.get_cell(0)["outputs"] = [
            {"output_type": "stream", "name": "stdout", "text": "out\n"}]
        contained_path = os.path.join(temp_dir, "contained.ipynb")
        contained_doc.save(contained_path)
        records_before_r20 = len(log_handler_r20.records)
        contained_loaded = installed_core.NotebookDocument.from_file(
            contained_path)
        form._load_notebook_document(contained_loaded, contained_path)
        entries_r20 = log_handler_r20.records[records_before_r20:]
        result(form.document is not contained_loaded
               and form.document.cell_count() == 1
               and form.document.get_cell(0)["cell_type"] == "code"
               and form.document.get_cell(0)["source"] == ""
               and form.file_path is None,
               "a renderer failure on the open path falls back to a fresh "
               "empty notebook (one empty code cell, no file path)")
        notices = [entry for entry in entries_r20
                   if "could not be opened" in entry]
        result(len(notices) == 1
               and notices[0].startswith("Dragonfly Notebook:")
               and "RuntimeError" in notices[0] and "boom" in notices[0],
               "the fallback surfaced exactly ONE failure entry naming the "
               "exception ({} entries on the logger, the guarded internal "
               "records are not the user-facing failure)".format(
                   len(entries_r20)))
    finally:
        installed_form.render_outputs_html = original_renderer
        form_logger.removeHandler(log_handler_r20)
    app.processEvents()

    # The containment pins above left the form on the fallback document:
    # restore the shared form to the section's openme notebook (the state
    # the open-failure block's restore left it with) for the pins below.
    form.document = installed_core.NotebookDocument.from_file(open_path)
    form.file_path = open_path
    form._rebuild_cells()
    app.processEvents()

    # The floating window keeps sane width bounds with content inside.
    form.resize(900, 700)
    app.processEvents()
    frame = form._cell_widgets[0]["frame"]
    result(100 <= frame.width() <= 900,
           "the cell frame fills the window, not beyond it (width {})".format(frame.width()))

    # The Run All engine-failure chain-stop (the round-6b rule, re-pinned
    # for the asynchronous round-8 run path): an ENGINE failure reaches the
    # form through the kernel's run_failed signal -- the exact signal the
    # worker thread emits when the kernel cannot start or execute -- and
    # the form must stop the whole Run All chain on it, reflect it in the
    # status (never the 'Run All finished' summary) and, under
    # running_in_test, log instead of going modal. The chain state is set
    # the way an in-flight Run All leaves it and the REAL signal is
    # emitted; no cell is run in this suite (the spawned application of
    # section 7 runs the real ones).
    form.document.add_cell('code')
    form._rebuild_cells()
    code_indices = [i for i in range(form.document.cell_count())
                    if form.document.get_cell(i)['cell_type'] == 'code']
    result(len(code_indices) >= 2,
           "the Run All engine-failure pin needs >= 2 code cells (got {})".format(
               len(code_indices)))
    log_handler2 = _RecordingLogHandler()
    form_logger.addHandler(log_handler2)
    try:
        form._run_all_active = True
        form._run_all_total = len(code_indices)
        form._run_all_ran = 1
        form.kernel.run_failed.emit('kernel start failure (the Run All pin)')
        app.processEvents()
        status_text = form.status_label.text()
        result('The cell did not run' in status_text
               and 'kernel start failure' in status_text
               and 'Run All finished' not in status_text,
               "the status reflects the engine failure, never the 'Run All "
               "finished' summary (the failed cell is not counted as run): "
               "{!r}".format(status_text))
        result(form._run_all_active is False,
               "one engine failure stopped the whole Run All chain")
        result(len(log_handler2.records) == 1
               and 'Dragonfly Notebook:' in log_handler2.records[0],
               "the engine failure logged exactly ONE entry under "
               "running_in_test ({} entries)".format(len(log_handler2.records)))
    finally:
        form_logger.removeHandler(log_handler2)

    # ------------------------------------------------------------------
    print("\n=== (4) the picker dialogs, bridge-fed ===", flush=True)

    # The object combos fill THROUGH the bridge (dfnb.list_objects over the
    # real pipe) against the fixtures created below; the one-message-per-
    # failure contract is observed through the form logger (under
    # running_in_test a failure box is a log record, never a modal). The
    # failure path needs an EMPTY scene for the picked class, so it runs
    # before the ROI fixtures -- the import_roi picker then has nothing to
    # offer and its OK must log exactly one entry and refuse.
    log_handler3 = _RecordingLogHandler()
    form_logger.addHandler(log_handler3)
    try:
        fail_dialog = installed_form.SnippetDialog("import_roi", form.client, form)
        result(fail_dialog.source_combo.count() == 0,
               "the import_roi combo is empty (no ROI exists yet)")
        fail_dialog._on_accept()
        entries = log_handler3.records
        result(len(entries) == 1
               and entries[0].startswith("Dragonfly Notebook:")
               and "No ROI is available to pick" in entries[0],
               "OK with nothing to pick logged exactly ONE 'Dragonfly "
               "Notebook' entry ({} entries)".format(len(entries)))
        result(fail_dialog.result() != QDialog.DialogCode.Accepted,
               "the empty-scene OK is refused (no accept)")
    finally:
        form_logger.removeHandler(log_handler3)

    # The DEAD-BRIDGE failure path: the export picker is the only dialog
    # constructing TWO bridge-fed combos at build time (the MultiROI source
    # and the Channel reference), so an unreachable bridge fails BOTH fills
    # and exactly-one-box-per-failure-path means the open logs exactly ONE
    # 'Dragonfly Notebook' entry, not two. The bridge is dead the way the
    # client's own disconnect contract defines it (a client that never
    # connected: every call raises the clear disconnect RuntimeError); a
    # single-combo picker over the same dead client must still log exactly
    # one entry (the behavior the export fix may not change).
    dead_client = installed_form.DfnbClient(
        pipe_name='dfnb_bridge_dead_bridge_pin')
    try:
        log_handler_dead = _RecordingLogHandler()
        form_logger.addHandler(log_handler_dead)
        try:
            dead_export = installed_form.SnippetDialog(
                "export_properties", dead_client, form)
            result(dead_export.source_combo.count() == 0
                   and dead_export.reference_combo.count() == 0,
                   "a dead bridge leaves BOTH export combos empty")
            dead_entries = log_handler_dead.records
            result(len(dead_entries) == 1
                   and dead_entries[0].startswith("Dragonfly Notebook:")
                   and "could not be read from Dragonfly" in dead_entries[0],
                   "opening the export picker with the bridge unreachable "
                   "logged exactly ONE 'Dragonfly Notebook' entry ({} "
                   "entries)".format(len(dead_entries)))
            before_single = len(log_handler_dead.records)
            dead_single = installed_form.SnippetDialog(
                "import_roi", dead_client, form)
            single_entries = log_handler_dead.records[before_single:]
            result(dead_single.source_combo.count() == 0
                   and len(single_entries) == 1
                   and single_entries[0].startswith("Dragonfly Notebook:")
                   and "could not be read from Dragonfly" in single_entries[0],
                   "a single-combo picker with the bridge unreachable still "
                   "logged exactly ONE entry ({} entries)"
                   .format(len(single_entries)))
        finally:
            form_logger.removeHandler(log_handler_dead)
    finally:
        dead_client.close()

    # The fixtures: a float32 2x2x2 volume and a label MultiROI carrying
    # one measured property ('Area'), both deleted at teardown. They are
    # ALSO the spawned application's round-trip targets (section 7).
    probe_volume = track(createChannelFromNumpyArray(
        np.zeros((2, 2, 2), np.float32)))
    probe_volume.setTitle("Probe Volume")
    label_array = np.array([[[1, 2], [0, 1]], [[2, 0], [1, 2]]], np.uint8)
    shape_source = OrsChannel()
    shape_source.copyShapeFromStructuredGrid(probe_volume)
    label_channel = OrsChannel()
    label_channel.copyShapeFromStructuredGrid(shape_source)
    createChannelFromNumpyArray(label_array, channelGUID=label_channel.getGUID(),
                                ZAxis=True)
    shape_source.deleteObject()
    probe_multiroi = track(DatasetHelper.createMultiROIFromDataset(label_channel, None))
    label_channel.deleteObject()
    result(probe_multiroi is not None, "the label MultiROI fixture built")
    probe_multiroi.setLabelCount(int(label_array.max()))
    probe_multiroi.setTitle("Probe Labels")
    probe_multiroi.setScalarValuesSlotCount(1)
    probe_multiroi.setScalarValueDescription(0, "Area", 0)
    ors_column = ArrayHelper.ConvertNumpyToOrsArray(
        np.asarray([0.0, 5.0, 7.0], dtype=np.float64))
    probe_multiroi.setScalarValues(ors_column, 0, 0)
    ors_column.deleteObject()

    def _refuse_unknown_kind():
        try:
            installed_form.SnippetDialog("bogus", form.client)
            return False
        except ValueError:
            return True

    result(_refuse_unknown_kind(), "an unknown snippet kind is refused")

    # import_volume: the combo filled THROUGH the bridge (the fixture is
    # the scene's only Channel, its unique title is the item data),
    # parameters() feeds generate_snippet into the exact dfnb line.
    dlg_volume = installed_form.SnippetDialog("import_volume", form.client, form)
    result(dlg_volume.source_combo.count() == 1
           and dlg_volume.source_combo.itemText(0) == "Probe Volume",
           "the import_volume combo lists the fixture through the bridge "
           "({} items)".format(dlg_volume.source_combo.count()))
    dlg_volume.source_combo.setCurrentIndex(0)
    params_volume = dlg_volume.parameters()
    result(params_volume["source"] == "Probe Volume"
           and params_volume["variable"] == "volume_np"
           and params_volume["timestep"] == 0,
           "import_volume parameters: source/variable/timestep")
    line_volume = installed_core.generate_snippet("import_volume", **params_volume)
    result("dfnb.import_volume_as_np('Probe Volume')" in line_volume,
           "the generated line names the dfnb call and the picked object")

    # the snippet-drop path (round 8 regression pin): the cell editor's
    # _drop_snippet used to import the DELETED picker_dialogs module, so
    # a snippet-mime drop raised ModuleNotFoundError; it now emits
    # snippetDropped and the FORM runs the picker through the bridge
    # client it owns, inserting at the DROPPED-ON editor (the round-4
    # drop-target semantics), never the focused cell.
    drop_editor = installed_cells.NotebookCellEditor()
    seen_drops = []
    drop_editor.snippetDropped.connect(seen_drops.append)
    drop_editor._drop_snippet("import_volume")
    result(seen_drops == ["import_volume"],
           "a snippet-mime drop announces itself through snippetDropped "
           "(no dead import of the deleted picker_dialogs)")
    drop_editor.snippetDropped.disconnect(seen_drops.append)
    real_spd = installed_form.snippet_parameters_dialog
    try:
        installed_form.snippet_parameters_dialog = (
            lambda kind, client, parent=None:
            {"variable": "volume_np", "source": "Probe Volume",
             "timestep": 0} if kind == "import_volume" else None)
        expected_drop_line = installed_core.generate_snippet(
            "import_volume", variable="volume_np", source="Probe Volume",
            timestep=0)
        form._on_snippet_dropped(drop_editor, "import_volume")
        result(drop_editor.toPlainText() == expected_drop_line,
               "the dropped snippet lands at the DROPPED-ON editor "
               "({!r})".format(drop_editor.toPlainText()[:60]))
        result(form.document.is_dirty is True,
               "a dropped snippet marks the document dirty")
        result("Snippet inserted" in form.status_label.text(),
               "the drop sets the Snippet-inserted status")
        form._on_snippet_dropped(drop_editor, "import_properties")
        result(drop_editor.toPlainText() == expected_drop_line,
               "a cancelled/failed picker inserts nothing (the stub "
               "returned None for the other kind)")
    finally:
        installed_form.snippet_parameters_dialog = real_spd

    # publish_volume: array + assign-to defaults, the explicit spacing
    # (units in the labels) and the optional reference volume.
    dlg_publish = installed_form.SnippetDialog("publish_volume", form.client, form)
    result(dlg_publish.array_edit.text() == "volume_np"
           and dlg_publish.variable_edit.text() == "new_volume",
           "publish_volume defaults: array/assign-to")
    result(dlg_publish.reference_combo.isEnabled() is False,
           "the reference picker starts disabled (checkbox off)")
    dlg_publish.title_edit.setText("My Result")
    dlg_publish.spacing_check.setChecked(True)
    result(all(spin.isEnabled() for spin in dlg_publish.spacing_spins),
           "checking 'Set explicit spacing' enables the three spin boxes")
    dlg_publish.spacing_spins[1].setValue(0.5)
    params_publish = dlg_publish.parameters()
    result(params_publish["title"] == "My Result"
           and params_publish["spacing"] == (1.0, 0.5, 1.0)
           and params_publish["reference"] is None,
           "publish_volume parameters: title/spacing/no reference")
    line_publish = installed_core.generate_snippet("publish_volume", **params_publish)
    result("new_volume = dfnb.publish_np_as_volume(volume_np" in line_publish
           and "title='My Result'" in line_publish
           and "spacing=(1.0, 0.5, 1.0)" in line_publish,
           "the generated publish line carries array, title and spacing")

    # The locale-safe spacing boxes: ',' and '.' are both decimal
    # separators whatever the machine locale is.
    spacing_spin = dlg_publish.spacing_spins[0]
    result(spacing_spin.valueFromText("1,5") == 1.5
           and spacing_spin.valueFromText("2.25") == 2.25,
           "the spacing box parses both '1,5' and '2.25'")
    result(spacing_spin.textFromValue(1.5) == "1.5",
           "the spacing box formats with the C locale")
    dlg_publish.reference_check.setChecked(True)
    dlg_publish.reference_combo.setCurrentIndex(0)
    result(dlg_publish.parameters()["reference"] == "Probe Volume",
           "a checked reference volume reaches the parameters")

    def _pin_dialog_look(dlg, label):
        """One PASS line for the pickers' applied dark look: the object
        name the dialog stylesheet scopes on, plus stylesheet identity
        against the module constant."""
        result(dlg.objectName() == "snippet_dialog"
               and dlg.styleSheet() == installed_form._DIALOG_STYLESHEET,
               "the {} picker carries objectName snippet_dialog and the "
               "dialog stylesheet on itself".format(label))

    # The remaining kinds construct (the wiring is what can break).
    for kind in ("import_roi", "import_multiroi"):
        dlg_kind = installed_form.SnippetDialog(kind, form.client, form)
        result(hasattr(dlg_kind, "source_combo") and hasattr(dlg_kind, "timestep_spin"),
               "the {} picker built with its source + timestep widgets".format(kind))
        _pin_dialog_look(dlg_kind, kind)
    for kind in ("publish_roi", "publish_multiroi"):
        dlg_kind = installed_form.SnippetDialog(kind, form.client, form)
        result(hasattr(dlg_kind, "title_edit") and hasattr(dlg_kind, "reference_combo"),
               "the {} picker built with its title + reference widgets".format(kind))
        _pin_dialog_look(dlg_kind, kind)

    # import_properties: the chooser refreshed itself from the fixture at
    # construction (THROUGH the bridge -- list_measured_properties), all-
    # checked default, none-checked blocks OK (the warning is a log record
    # under running_in_test), checked labels reach the generated line.
    dlg_props = installed_form.SnippetDialog("import_properties", form.client, form)
    result(dlg_props.properties_list.count() == 1,
           "the properties chooser refreshed from the fixture ({} item)".format(
               dlg_props.properties_list.count()))
    result(dlg_props.properties_list.item(0).text() == "Area",
           "the chooser item is the measured property's description")
    result(dlg_props.selected_properties() == ["Area"],
           "properties start all-checked")
    dlg_props.properties_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    result(dlg_props.selected_properties() == [], "unchecking empties the selection")
    log_handler4 = _RecordingLogHandler()
    form_logger.addHandler(log_handler4)
    try:
        dlg_props._on_accept()  # the warning logs (running_in_test)
        entries = log_handler4.records
        result(len(entries) == 1 and "Check at least one" in entries[0],
               "OK with properties available but none checked logged exactly "
               "ONE entry ({})".format(len(entries)))
    finally:
        form_logger.removeHandler(log_handler4)
    result(dlg_props.result() != QDialog.DialogCode.Accepted,
           "OK with properties available but none checked is refused")
    dlg_props.properties_list.item(0).setCheckState(Qt.CheckState.Checked)
    dlg_props.source_combo.setCurrentIndex(0)
    params_props = dlg_props.parameters()
    result(params_props["source"] == "Probe Labels"
           and params_props["properties"] == ["Area"],
           "import_properties parameters: source + checked columns")
    line_props = installed_core.generate_snippet("import_properties", **params_props)
    result("dfnb.import_measured_properties('Probe Labels'" in line_props
           and "properties=['Area']" in line_props,
           "the generated import-properties line carries source + columns")

    # export_properties: the MODE radio swaps two field pages. Mode A
    # (write into an existing MultiROI): source + timestep + the
    # properties VARIABLE; the success accept shows no entry (the accept
    # itself is the pin).
    dlg_export = installed_form.SnippetDialog("export_properties", form.client, form)
    result(dlg_export.mode_existing_radio.isChecked()
           and not dlg_export.mode_publish_radio.isChecked()
           and dlg_export.mode_stack.currentIndex() == 0,
           "the export picker starts in the write-into-existing mode")
    result(dlg_export.properties_edit.text() == "props_df",
           "the properties variable defaults to props_df")
    dlg_export.source_combo.setCurrentIndex(0)
    dlg_export._on_accept()
    result(dlg_export.result() == QDialog.DialogCode.Accepted,
           "export mode A accepts with a picked MultiROI (no failure entry)")
    params_export = dlg_export.parameters()
    result(params_export["source"] == "Probe Labels"
           and params_export["timestep"] == 0
           and params_export["properties"] == "props_df",
           "export mode A parameters: source/timestep/properties variable")
    line_export = installed_core.generate_snippet("export_properties",
                                                  **params_export)
    result(line_export == "dfnb.export_measured_properties('Probe Labels', props_df)",
           "the generated export line is the bare write call")
    dlg_export.timestep_spin.setValue(2)
    result(installed_core.generate_snippet(
               "export_properties", **dlg_export.parameters()).endswith(
               "timestep=2)"),
           "a nonzero timestep reaches the generated export line")
    dlg_export.timestep_spin.setValue(0)

    # round 10: the wipe checkbox. It lives ONLY on the existing-mode page
    # (wipe is meaningless in publish mode -- a fresh MultiROI has no
    # existing properties to replace), defaults UNCHECKED = add mode,
    # today's behavior; unchecked parameters carry NO 'wipe' key so the
    # generated line stays the minimal pre-wipe shape.
    wipe_check = dlg_export.findChild(QCheckBox, "wipe_check")
    result(wipe_check is not None,
           "the export picker carries the wipe_check checkbox")
    result(wipe_check.isChecked() is False,
           "the wipe checkbox defaults to unchecked (add mode)")
    params_no_wipe = dlg_export.parameters()
    result("wipe" not in params_no_wipe,
           "unchecked wipe leaves no 'wipe' key in the parameters")
    result(installed_core.generate_snippet(
               "export_properties", **params_no_wipe)
           == "dfnb.export_measured_properties('Probe Labels', props_df)",
           "the unchecked-wipe generated line is today's bare write call")
    wipe_check.setChecked(True)
    params_wipe = dlg_export.parameters()
    result(params_wipe.get("wipe") is True,
           "a checked wipe reaches the parameters as wipe=True")
    result(installed_core.generate_snippet(
               "export_properties", **params_wipe)
           == "dfnb.export_measured_properties('Probe Labels', props_df, "
              "wipe=True)",
           "the checked-wipe generated line carries wipe=True")
    wipe_check.setChecked(False)

    # mode B (publish a new MultiROI from an array): the same fields as the
    # publish_multiroi picker plus the properties variable; the explicit
    # spacing is offered only when no reference volume is inherited.
    dlg_export.mode_publish_radio.setChecked(True)
    result(dlg_export.mode_stack.currentIndex() == 1,
           "checking the publish radio swaps to the publish page")
    result(dlg_export.array_edit.text() == "mask_np"
           and dlg_export.variable_edit.text() == "new_mroi"
           and dlg_export.title_edit.text() == "Measured segmentation",
           "export mode B defaults: array/assign-to/title")
    dlg_export.spacing_check.setChecked(True)
    result(all(spin.isEnabled() for spin in dlg_export.spacing_spins),
           "mode B: checking 'Set explicit spacing' enables the spins")
    dlg_export.reference_check.setChecked(True)
    result(dlg_export.spacing_row.isHidden()
           and dlg_export.spacing_check.isChecked() is False,
           "mode B: a reference volume hides and unchecks the spacing row")
    dlg_export.reference_check.setChecked(False)
    result(not dlg_export.spacing_row.isHidden(),
           "mode B: unchecking the reference shows the spacing row again")
    dlg_export.reference_check.setChecked(True)
    dlg_export.reference_combo.setCurrentIndex(0)
    params_publish_mode = dlg_export.parameters()
    result(params_publish_mode["array"] == "mask_np"
           and params_publish_mode["variable"] == "new_mroi"
           and params_publish_mode["title"] == "Measured segmentation"
           and params_publish_mode["reference"] == "Probe Volume"
           and params_publish_mode["properties"] == "props_df"
           and "spacing" not in params_publish_mode,
           "export mode B parameters: array/variable/title/reference/"
           "properties, no spacing under a reference")
    line_publish_mode = installed_core.generate_snippet(
        "export_properties", **params_publish_mode)
    result(line_publish_mode == (
        "new_mroi = dfnb.publish_np_as_multiroi(mask_np, "
        "title='Measured segmentation', reference='Probe Volume', "
        "properties=props_df)"),
           "the generated publish-mode line is the publish call with the "
           "properties variable")
    # round 10: the publish mode never carries a wipe -- the checkbox lives
    # only on the existing-mode page, so no publish parameter set (and no
    # publish line) can ever contain 'wipe'
    result("wipe" not in params_publish_mode and "wipe" not in line_publish_mode,
           "the publish-mode parameters and line never contain 'wipe'")

    # ------------------------------------------------------------------
    # round 10: the pickers' own dark look. Every picker constructed in
    # this section carries the object name and the stylesheet ON ITSELF
    # (_DIALOG_STYLESHEET's selectors are all scoped to #snippet_dialog /
    # #dfnb_message, so nothing else in the process can pick them up --
    # and nothing else in the process picks them UP through parenting
    # either, because the sheet is applied on the dialog, not inherited).
    dialog_sheet = installed_form._DIALOG_STYLESHEET
    result(bool(dialog_sheet.strip()),
           "the dialog stylesheet constant is non-empty")
    for dlg in (fail_dialog, dead_export, dead_single, dlg_volume,
                dlg_publish, dlg_props, dlg_export):
        _pin_dialog_look(dlg, dlg.kind)
    result("QDialog#snippet_dialog { background: #252526; color: #D4D4D4; }"
           in dialog_sheet
           and "QMessageBox#dfnb_message { background: #252526; "
               "color: #D4D4D4; }" in dialog_sheet,
           "the constant opens with the two dialog background rules")
    result("QDialog#snippet_dialog QLineEdit {" in dialog_sheet
           and "selection-background-color: #094771" in dialog_sheet
           and "QDialog#snippet_dialog QLineEdit:focus "
               "{ border-color: #4F94CD; }" in dialog_sheet,
           "the dialog stylesheet carries the QLineEdit rule (dark frame, "
           "selection color, focus border)")
    result("QDialog#snippet_dialog QComboBox::drop-down { border: none; }"
           in dialog_sheet
           and "QDialog#snippet_dialog QComboBox QAbstractItemView {"
           in dialog_sheet
           and "QDialog#snippet_dialog QComboBox QAbstractItemView {\n"
               "    background: #252526" in dialog_sheet,
           "the combo popup rule is present and scoped")
    result("QDialog#snippet_dialog QListWidget#properties_list {"
           in dialog_sheet
           and "QDialog#snippet_dialog QListWidget#properties_list::item "
               "{ color: #D4D4D4; padding: 2px 4px; }" in dialog_sheet
           and "QDialog#snippet_dialog QListWidget#properties_list::item:"
               "selected { background: #094771; }" in dialog_sheet
           and "QDialog#snippet_dialog QListWidget::indicator {"
           in dialog_sheet,
           "the properties-list rule set (frame, items, selection, check "
           "indicators) is present")
    result("QDialog#snippet_dialog QScrollBar" in dialog_sheet,
           "the scrollbar blocks are re-scoped to the dialog")
    result("QDialog#snippet_dialog QDialogButtonBox QPushButton"
           in dialog_sheet
           and "QMessageBox#dfnb_message QPushButton" in dialog_sheet
           and "QPushButton:default" in dialog_sheet
           and "rgba(79, 148, 205, 0.12)" in dialog_sheet,
           "the button-box rule covers both dialogs and the default "
           "button echoes the run button")
    # The leak guard: no BARE top-level type selector may exist in the
    # constant -- every selector must name one of the scoped dialog
    # object names (snippet, message, and round 16's env dialogs).
    # The regex keys on a type name followed only by optional blanks and
    # the opening brace: a scoped selector's '#' (QDialog#snippet_dialog)
    # blocks the match, a bare 'QLineEdit {' would match. The second pin
    # is the same rule over EVERY rule line (any line carrying a '{'):
    # each must name one of the two scopes, which also catches bare
    # pseudo-element selectors the first regex cannot see.
    leak_pattern = re.compile(
        r"^(QLineEdit|QLabel|QComboBox|QSpinBox|QDoubleSpinBox|QCheckBox|"
        r"QRadioButton|QListWidget|QPushButton|QScrollBar|QDialog|"
        r"QMessageBox)\b[ \t]*\{", re.M)
    result(leak_pattern.search(dialog_sheet) is None,
           "no bare top-level type selector in the dialog stylesheet")
    rule_lines = [line for line in dialog_sheet.splitlines() if "{" in line]
    result(bool(rule_lines) and all(
               line.lstrip().startswith(("QDialog#snippet_dialog",
                                         "QDialog#env_dialog",
                                         "QMessageBox#dfnb_message"))
               for line in rule_lines),
           "every one of the {} rule lines names #snippet_dialog, "
           "#env_dialog or #dfnb_message".format(len(rule_lines)))
    # The message-box look: under running_in_test the failure paths log,
    # so the seam itself is pinned with a recording stub (the same
    # swap-and-restore convention the launcher's subprocess stub above
    # uses) -- the app property is cleared for the ONE call and restored
    # in the finally.
    real_running = app.property("running_in_test")
    real_message_box = installed_form.QMessageBox
    boxes = []

    class _MessageBoxStub:
        """Stands in for QMessageBox: records the objectName/stylesheet
        assignments and counts the exec calls; never modal. Carries the
        StandardButton/Icon tokens BOTH box-building seams reference
        (_show_message and _confirm_discarding_changes)."""

        class StandardButton:
            Ok = 1
            Save = 1 << 2
            Discard = 1 << 3
            Cancel = 1 << 4

        class Icon:
            Question = 1 << 5

        def __init__(self, *args, **kwargs):
            boxes.append(self)
            self.object_names = []
            self.stylesheets = []
            self.exec_calls = 0
            self.buttons = []

        def setObjectName(self, name):
            self.object_names.append(name)

        def setStyleSheet(self, sheet):
            self.stylesheets.append(sheet)

        def setWindowTitle(self, title):
            self.window_title = title

        def setIcon(self, icon):
            self.icon = icon

        def setText(self, text):
            self.text = text

        def addButton(self, button):
            # the return stands in for the real button widget: a tuple,
            # never identical to any StandardButton token the form code
            # compares against
            self.buttons.append(button)
            return ("stub_button", button)

        def setDefaultButton(self, button):
            self.default_button = button

        def clickedButton(self):
            # a non-button sentinel: _confirm_discarding_changes then
            # takes the discard path (True) without reaching
            # on_save_notebook
            return ("stub_clicked", None)

        def exec(self):
            self.exec_calls += 1
            return 0x00000400  # canned token; the confirm path reads
            # clickedButton(), never exec()'s return

    installed_form.QMessageBox = _MessageBoxStub
    try:
        app.setProperty("running_in_test", False)
        installed_form._show_message(form, "x")
    finally:
        installed_form.QMessageBox = real_message_box
        app.setProperty("running_in_test", real_running)
    result(len(boxes) == 1
           and boxes[0].object_names == ["dfnb_message"]
           and boxes[0].stylesheets == [dialog_sheet]
           and boxes[0].exec_calls == 1,
           "the failure box took objectName dfnb_message and the dialog "
           "stylesheet before its exec (one box, one exec)")

    # The discard-confirmation box gets the same seam: the form builds
    # its Save/Discard/Cancel box directly, so it is pinned through the
    # method itself with the same stub. The suite RESETS is_dirty below
    # (section 5) to keep this box out of the open paths' way -- here the
    # dirty state is entered ON PURPOSE (saved + restored in the
    # finally): it is what makes the confirm path run.
    real_dirty = form.document.is_dirty
    installed_form.QMessageBox = _MessageBoxStub
    try:
        app.setProperty("running_in_test", False)
        form.document.is_dirty = True
        confirmed = form._confirm_discarding_changes("the stub pin")
    finally:
        form.document.is_dirty = real_dirty
        installed_form.QMessageBox = real_message_box
        app.setProperty("running_in_test", real_running)
    confirm_box = boxes[-1]
    result(confirmed is True
           and confirm_box is not boxes[0]
           and confirm_box.object_names == ["dfnb_message"]
           and confirm_box.stylesheets == [dialog_sheet]
           and confirm_box.exec_calls == 1
           and confirm_box.buttons == [_MessageBoxStub.StandardButton.Save,
                                       _MessageBoxStub.StandardButton.Discard,
                                       _MessageBoxStub.StandardButton.Cancel],
           "the discard-confirmation box took objectName dfnb_message and "
           "the dialog stylesheet before its exec (Save/Discard/Cancel "
           "added in order, discard path taken, no save)")

    # ------------------------------------------------------------------
    print("\n=== (5) round 9: dark-theme text, cell grow, the .ipynb drop ===", flush=True)

    # The suite state entering this section: the picker section's
    # snippet-drop pin left the document DIRTY on purpose (it pins the
    # dirty flag). The open paths below go through the discard
    # confirmation -- a dirty document would hang this headless gate on
    # the Save/Discard/Cancel box -- so the flag is reset here first; the
    # editors still carry exactly the document's sources.
    form.document.is_dirty = False

    # The dark-theme pins (the user-visible complaint): the code text
    # renders the ORS base light color on the dark editor -- NOT the
    # application palette's black that unstyled tokens fell back to -- and
    # the dim labels were raised. The output browser keeps its own dark
    # surface with light text (its scrollbars stay styled too).
    editor_sheet = installed_cells.NotebookCellEditor().styleSheet()
    result('background: #1E1E1E' in editor_sheet
           and 'color: rgb(230, 230, 230)' in editor_sheet,
           "the cell editor stylesheet is dark background + ORS light text "
           "(got {!r})".format(editor_sheet))
    form_sheet = form.styleSheet()
    result('QLabel#cell_elapsed { color: #C8C8C8; }' in form_sheet
           and 'QLabel#cell_type_label { color: #C8C8C8; }' in form_sheet
           and 'QLabel#status_label { color: #C8C8C8; }' in form_sheet
           and 'QLabel#cell_prompt { color: rgb(230, 230, 230); }' in form_sheet,
           "the timing labels, the status line and the In[n] prompts carry "
           "the raised light colors")
    result('background: #1B1B1C; color: rgb(230, 230, 230);' in form_sheet,
           "the output browser keeps its dark surface with light text")

    # round 10: the footer watermark (the h5 importer's understated-footer
    # convention) -- an always-visible gray line as the LAST widget of the
    # outer layout, below the status strip, never emitted to log or box.
    watermark = form.findChild(QLabel, "watermark_label")
    result(watermark is not None,
           "the form carries the watermark_label footer")
    result(watermark.wordWrap() is True,
           "the watermark label wraps its text")
    watermark_text = watermark.text()
    result("Aarhus University" in watermark_text
           and "marius.didziokas@chem.au.dk" in watermark_text
           and "log" in watermark_text,
           "the watermark names the affiliation, the support address and "
           "the Dragonfly log ({!r})".format(watermark_text))
    # Round 15: the watermark LEADS with the plugin's current version,
    # taken from the ONE source (dragonflynotebook_core.__version__) --
    # and the ORS glue re-exports that same object, so the version a
    # Dragonfly-side reader sees and the version the ORS-free form
    # prints can never drift apart.
    result(watermark_text.startswith('DragonflyNotebook ')
           and installed_core.__version__ in watermark_text,
           "the watermark leads with the plugin version ({})".format(
               installed_core.__version__))
    result(installed_glue.__version__ == installed_core.__version__,
           "the glue re-exports the ONE version source (glue {} == core "
           "{})".format(installed_glue.__version__,
                        installed_core.__version__))
    result(watermark_text == installed_form._WATERMARK_TEXT,
           "the watermark text is the module constant verbatim")
    outer_layout = form.centralWidget().layout()
    strip_index = outer_layout.indexOf(form.findChild(QFrame, "status_strip"))
    watermark_index = outer_layout.indexOf(watermark)
    result(0 <= strip_index < watermark_index < outer_layout.count(),
           "the watermark sits BELOW the status strip in the outer layout "
           "(strip {}, watermark {}, {} items)".format(
               strip_index, watermark_index, outer_layout.count()))
    result("QLabel#watermark_label { color: #808080; font-size: 8pt; }"
           in form_sheet,
           "the watermark's gray 8pt rule is objectName-scoped in the form "
           "stylesheet")

    # The ORS scrollbar port: every line of the form stylesheet that
    # carries a QScrollBar selector is scoped to the cells panel or the
    # output browsers (child dialogs inherit ancestor stylesheets -- an
    # unscoped rule would restyle every dialog's lists). Comments are
    # stripped first: a rule named in prose is not a rule.
    no_comments = re.sub(r"/\*.*?\*/", "", form_sheet, flags=re.S)
    scrollbar_lines = [line.strip() for line in no_comments.splitlines()
                       if "QScrollBar" in line]
    result(len(scrollbar_lines) >= 24
           and all(("QScrollArea#mainScrollArea" in line
                    or "QTextBrowser#cell_output" in line)
                   for line in scrollbar_lines),
           "all {} QScrollBar selector lines are scoped to the cells panel "
           "or the output browsers (none app-global)".format(len(scrollbar_lines)))
    result("stop: 0.0 #121212" in form_sheet and "stop: 0 #606060" in form_sheet
           and "width: 9px" in form_sheet and "height: 9px" in form_sheet,
           "the ORS trough gradient, handle color and 9px bar geometry are "
           "ported verbatim from resource/stylesheet/main.txt")

    # The code editors have NO scrollbars of their own: the whole notebook
    # panel scrolls (each cell extends to its full code extent).
    result(all(r["editor"].verticalScrollBarPolicy()
               == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
               and r["editor"].horizontalScrollBarPolicy()
               == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
               for r in form._cell_widgets),
           "every cell editor sets BOTH scrollbar policies to AlwaysOff")

    # GROW-WITH-CONTENT, the anti-clip pin: with both bars off, an
    # underestimated fixed height silently clips the last lines. The
    # honest ground truth is the editor's own scrollbar arithmetic: once
    # the height converges, verticalScrollBar().maximum() MUST be 0 (the
    # layout's line count fits the fixed height; a nonzero max IS clipped
    # lines). documentSize() counts VISUAL LINES, not pixels (probed: 40
    # short lines -> 40.0, hard-wrapped lines -> the count the scrollbar
    # arithmetic itself uses), so the pixel comparison is
    # lines * lineSpacing.
    grow_editor = installed_cells.NotebookCellEditor()
    grow_editor.resize(600, 60)
    grow_editor.show()
    app.processEvents()
    grow_editor.setPlainText(
        "\n".join("x = {}".format(i) for i in range(40)))
    grow_editor.resizeToContent()
    app.processEvents()
    grow_vbar = grow_editor.verticalScrollBar()
    grow_lines = grow_editor.document().documentLayout().documentSize().height()
    result(grow_vbar.maximum() == 0
           and grow_editor.height() >= grow_lines * grow_editor.fontMetrics().lineSpacing(),
           "a 40-line cell grows to cover its text (vbar max {}, height {} "
           ">= {} visual lines x {} px)".format(
               grow_vbar.maximum(), grow_editor.height(), grow_lines,
               grow_editor.fontMetrics().lineSpacing()))
    # The hard-wrapped case, where the font-metrics wrap estimate
    # UNDER-counts and the layout's own visual-line count must win.
    grow_editor.setPlainText("\n".join(
        "value = arr[{}, :] + np.clip(offset_{} * 2.5, -1.0, 1.0) + "
        + "0123456789abcdef" * 20 for _ in range(6)))
    grow_editor.resizeToContent()
    app.processEvents()
    result(grow_editor.verticalScrollBar().maximum() == 0,
           "a hard-wrapped cell grows to cover its text (vbar max {} == 0; "
           "the layout's line count wins over the metrics estimate)".format(
               grow_editor.verticalScrollBar().maximum()))
    grow_editor.hide()
    grow_editor.deleteLater()

    # The same contract through the REAL form hooks (textChanged and
    # documentSizeChanged both route to resizeToContent): fill a real
    # code cell with 40 lines and the panel must never overflow it.
    # The text is restored afterwards (a programmatic fill IS a real edit
    # -- the dirty flag goes back too).
    code_record = next(r for r in form._cell_widgets
                       if r.get("highlighter") is not None)
    original_text = code_record["editor"].toPlainText()
    code_record["editor"].setPlainText(
        "\n".join("y = {} * 2".format(i) for i in range(40)))
    app.processEvents()
    result(code_record["editor"].verticalScrollBar().maximum() == 0,
           "a real cell grows with its text through the form's hooks (vbar "
           "max {} == 0)".format(
               code_record["editor"].verticalScrollBar().maximum()))
    code_record["editor"].setPlainText(original_text)
    app.processEvents()
    form.document.is_dirty = False

    # The OUTPUT browser gets the editor's grow-with-content treatment
    # (the same documentSizeChanged seam, mirrored in the form's
    # _fit_browser_to_content): QTextBrowser's sizeHint is a constant
    # 256x192 no matter what it holds, so before round 9 a matplotlib
    # figure clipped to a ~50 px band and a multi-line text output
    # clipped mid-glyph. The honest ground truth is again the widget's
    # own scrollbar arithmetic: since round 21 the browser renders at
    # its FULL content height -- the notebook renders like a document,
    # the panel is the one vertical scroll area, nothing inside a cell
    # scrolls vertically -- so verticalScrollBar().maximum() MUST be 0
    # whatever the output's height, and the vertical scrollbar policy is
    # AlwaysOff. Round 22: horizontal scrolling is gone too -- BOTH
    # policies are AlwaysOff, and a figure larger than the viewport is
    # SCALED DOWN uniformly to the available width instead of scrolling
    # (the round-22 image fit, below; the source EDITOR's behavior is
    # not this pin's subject -- code editing keeps its round-9 wrap-and-
    # grow contract). The outputs ride the form's REAL paths -- the
    # live streaming chunk (_append_live_html, what a running cell
    # grows) and the authoritative re-render (_render_outputs, what
    # run-finished writes) -- and the output area is restored to its
    # empty state afterwards (the document is untouched:
    # _render_outputs only repaints the widget).
    output_record = code_record
    output_index = form._cell_widgets.index(output_record)
    output_browser = output_record["browser"]
    result(output_browser.verticalScrollBarPolicy()
           == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
           and output_browser.horizontalScrollBarPolicy()
           == Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
           "an output browser scrolls in NEITHER direction (both policies "
           "AlwaysOff; round 22 removed the horizontal bar)")
    form._render_outputs(output_index, [])
    app.processEvents()
    form._running = True
    form._running_cell_index = output_index
    try:
        form._append_live_html(installed_cells.render_stream_chunk(
            "stdout",
            "\n".join("line {} some output".format(i) for i in range(4))))
    finally:
        form._running = False
        form._running_cell_index = -1
    app.processEvents()
    output_doc = output_browser.document().size().height()
    output_expected = int(math.ceil(output_doc)) \
        + installed_form._OUTPUT_HEIGHT_FUDGE
    result(abs(output_browser.height() - output_expected) <= 4
           and output_browser.verticalScrollBar().maximum() == 0,
           "a 4-line text output sizes its browser to the document "
           "(height {} ~ {} + {} fudge, vbar max {})".format(
               output_browser.height(), int(math.ceil(output_doc)),
               installed_form._OUTPUT_HEIGHT_FUDGE,
               output_browser.verticalScrollBar().maximum()))

    # The TALL output: a 40-line stream output lays out to ~528 px, well
    # past the 400 px cap round 21 retired, and the browser must take
    # the FULL height (content plus the frame allowance) and show every
    # line -- no internal vertical scrolling, the panel scrolls instead.
    form._render_outputs(output_index, [
        {"output_type": "stream", "name": "stdout",
         "text": "\n".join("line {} some output".format(i)
                           for i in range(40))}])
    app.processEvents()
    tall_doc = output_browser.document().size().height()
    result(output_browser.height()
           == int(math.ceil(tall_doc))
           + installed_form._OUTPUT_HEIGHT_FUDGE
           and output_browser.height() > 400
           and output_browser.verticalScrollBar().maximum() == 0
           and output_browser.verticalScrollBarPolicy()
           == Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
           "a 40-line output renders at its full content height "
           "(browser {} = ceil({:.1f}) + {} fudge, past the retired "
           "cap, vbar max {}, policy AlwaysOff)".format(
               output_browser.height(), tall_doc,
               installed_form._OUTPUT_HEIGHT_FUDGE,
               output_browser.verticalScrollBar().maximum()))

    # The FIGURE output (the visual-judge defect): a 600x300 PNG embedded
    # the way a kernel inline figure arrives (QImage -> QBuffer PNG ->
    # base64 data URL, through render_outputs_html as display_data) must
    # leave the browser tall enough for the WHOLE image -- height at
    # least the image's 300 px and vbar max 0.
    figure_img = QImage(600, 300, QImage.Format.Format_RGB32)
    figure_img.fill(0xFF3A5F9E)
    figure_buf = QBuffer()
    figure_buf.open(QBuffer.OpenModeFlag.ReadWrite)
    result(figure_img.save(figure_buf, "PNG"),
           "the figure pin encoded its 600x300 PNG")
    figure_b64 = base64.b64encode(bytes(figure_buf.data())).decode("ascii")
    figure_buf.close()
    form._render_outputs(output_index, [
        {"output_type": "display_data", "data": {"image/png": figure_b64},
         "metadata": {}}])
    app.processEvents()
    result(output_browser.height() >= 300
           and output_browser.verticalScrollBar().maximum() == 0,
           "a 600x300 figure output shows fully (height {}, vbar max {} "
           "-- the round-9 defect was a ~50 px clip band)".format(
               output_browser.height(),
               output_browser.verticalScrollBar().maximum()))

    # The round-22 readback helpers: the image fragments of a document
    # (their QTextImageFormat width/height, in document order -- the
    # scaled geometry the fit wrote) and a real-time pump that lets the
    # ONE debounced image-fit pass fire after a form resize (the
    # resize filter arms the single-shot timer; the pump spins
    # processEvents past the 140 ms window).
    def image_fragment_formats(document):
        out = []
        block = document.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isImageFormat():
                    imf = frag.charFormat().toImageFormat()
                    out.append((imf.width(), imf.height()))
                it += 1
            block = block.next()
        return out

    def settle_image_fit(seconds=0.4):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    # The WIDE output, line case (round 21): a 220-character line WRAPS
    # (the renderer emits pre-wrap), so no scrollbar is needed in either
    # direction and two consecutive fits settle on one height. (Round 22:
    # the horizontal policy assertion below is AlwaysOff -- no bar in
    # either direction.)
    wide_line = "x" * 220
    form._render_outputs(output_index, [
        {"output_type": "stream", "name": "stdout",
         "text": wide_line}])
    app.processEvents()
    wide_line_doc = output_browser.document().size().height()
    form._fit_browser_to_content(output_browser)
    wide_line_first = output_browser.height()
    form._fit_browser_to_content(output_browser)
    app.processEvents()
    result(output_browser.height()
           == int(math.ceil(wide_line_doc))
           + installed_form._OUTPUT_HEIGHT_FUDGE
           and output_browser.height() == wide_line_first
           and output_browser.verticalScrollBar().maximum() == 0
           and output_browser.horizontalScrollBarPolicy()
           == Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
           "a 220-character line output wraps and does not clip (height "
           "{} = ceil({:.1f}) + {} fudge, two fits one height, vbar max "
           "{}, both policies AlwaysOff)".format(
               output_browser.height(), wide_line_doc,
               installed_form._OUTPUT_HEIGHT_FUDGE,
               output_browser.verticalScrollBar().maximum()))

    # The WIDE output, image case (round 22, rewritten from the round-21
    # scroll-overflow form): a 1200x100 PNG is wider than the ~700 px
    # browser viewport, and instead of a horizontal scrollbar it is
    # SCALED DOWN uniformly to the available width -- aspect preserved
    # within 1 px, no scrollbar in either direction, and the laid-out
    # document no wider than the viewport. The fit ran through the REAL
    # path (_render_outputs' direct call at viewport width) plus the
    # debounced pass that the documentSizeChanged seam armed.
    wide_img = QImage(1200, 100, QImage.Format.Format_RGB32)
    wide_img.fill(0xFF3A5F9E)
    wide_buf = QBuffer()
    wide_buf.open(QBuffer.OpenModeFlag.ReadWrite)
    result(wide_img.save(wide_buf, "PNG"),
           "the wide pin encoded its 1200x100 PNG")
    wide_b64 = base64.b64encode(bytes(wide_buf.data())).decode("ascii")
    wide_buf.close()
    form._render_outputs(output_index, [
        {"output_type": "display_data", "data": {"image/png": wide_b64},
         "metadata": {}}])
    app.processEvents()
    wide_avail = installed_cells.available_image_width(output_browser)
    wide_fmts = image_fragment_formats(output_browser.document())
    wide_expected_h = round(100 * wide_avail / 1200)
    result(len(wide_fmts) == 1
           and abs(wide_fmts[0][0] - wide_avail) <= 0.5
           and abs(wide_fmts[0][1] - wide_expected_h) <= 1.0
           and output_browser.horizontalScrollBar().maximum() == 0
           and output_browser.verticalScrollBar().maximum() == 0
           and output_browser.document().size().width()
           <= output_browser.viewport().width()
           and output_browser.height()
           == int(math.ceil(output_browser.document().size().height()))
           + installed_form._OUTPUT_HEIGHT_FUDGE,
           "a viewport-wider-than-panel image renders SCALED to the "
           "available width ({}x{} -> {:.0f}x{:.0f}, avail {}, aspect "
           "within 1 px, hbar max {}, vbar max {}, doc w {:.0f} <= "
           "viewport {}, full-height fit)".format(
               1200, 100, wide_fmts[0][0], wide_fmts[0][1], wide_avail,
               output_browser.horizontalScrollBar().maximum(),
               output_browser.verticalScrollBar().maximum(),
               output_browser.document().size().width(),
               output_browser.viewport().width(),))

    # Uniform-scaling exactness on a SECOND intrinsic size (round 22):
    # a 900x300 figure gets the same width fit with its own proportional
    # height -- the scale is generic, not tuned to one shape.
    mid_img = QImage(900, 300, QImage.Format.Format_RGB32)
    mid_img.fill(0xFF5F3A9E)
    mid_buf = QBuffer()
    mid_buf.open(QBuffer.OpenModeFlag.ReadWrite)
    result(mid_img.save(mid_buf, "PNG"),
           "the uniform pin encoded its 900x300 PNG")
    mid_b64 = base64.b64encode(bytes(mid_buf.data())).decode("ascii")
    mid_buf.close()
    form._render_outputs(output_index, [
        {"output_type": "display_data", "data": {"image/png": mid_b64},
         "metadata": {}}])
    app.processEvents()
    mid_avail = installed_cells.available_image_width(output_browser)
    mid_fmts = image_fragment_formats(output_browser.document())
    result(len(mid_fmts) == 1
           and abs(mid_fmts[0][0] - min(900, mid_avail)) <= 0.5
           and abs(mid_fmts[0][1] - round(300 * mid_fmts[0][0] / 900))
           <= 1.0
           and output_browser.horizontalScrollBar().maximum() == 0
           and output_browser.verticalScrollBar().maximum() == 0,
           "a 900x300 figure scales with the SAME width-fit rule "
           "({:.0f}x{:.0f}, avail {}, aspect preserved, hbar max {}, vbar "
           "max {})".format(mid_fmts[0][0], mid_fmts[0][1], mid_avail,
                            output_browser.horizontalScrollBar().maximum(),
                            output_browser.verticalScrollBar().maximum()))

    # NEVER upscaling (round 22): a small 100x50 image in a wide viewport
    # keeps its intrinsic size -- the fit only ever shrinks.
    small_img = QImage(100, 50, QImage.Format.Format_RGB32)
    small_img.fill(0xFF3A5F9E)
    small_buf = QBuffer()
    small_buf.open(QBuffer.OpenModeFlag.ReadWrite)
    result(small_img.save(small_buf, "PNG"),
           "the no-upscale pin encoded its 100x50 PNG")
    small_b64 = base64.b64encode(bytes(small_buf.data())).decode("ascii")
    small_buf.close()
    form._render_outputs(output_index, [
        {"output_type": "display_data", "data": {"image/png": small_b64},
         "metadata": {}}])
    app.processEvents()
    small_fmts = image_fragment_formats(output_browser.document())
    result(len(small_fmts) == 1 and small_fmts[0][0] == 100.0
           and small_fmts[0][1] == 50.0
           and output_browser.horizontalScrollBar().maximum() == 0,
           "a small image is NOT upscaled (fragment {}x{} stays "
           "intrinsic, hbar max {})".format(
               small_fmts[0][0], small_fmts[0][1],
               output_browser.horizontalScrollBar().maximum()))

    # The RESIZE battery (round 22): the user drags the window -- the
    # viewport resize filter arms the ONE debounced pass, which re-fits
    # every cell browser with the CURRENT widths. Wide (1400), narrow
    # (560), wide again; at each width the figure width must track
    # min(intrinsic, available) -- shrunk in the narrow window, restored
    # to the intrinsic 900 in the wide ones (never past it) -- with no
    # scrollbar in either direction, and two consecutive settles must be
    # idempotent. Each step pumps PAST the 140 ms debounce, so the pin
    # exercises the real filter -> timer -> pass chain, not a direct
    # call.
    form._render_outputs(output_index, [
        {"output_type": "display_data", "data": {"image/png": mid_b64},
         "metadata": {}}])
    settle_image_fit()
    resize_expectations = ((1400, 900), (560, 900), (1400, 900))
    for resize_width, resize_height in resize_expectations:
        form.resize(resize_width, resize_height)
        settle_image_fit()
        battery_avail = installed_cells.available_image_width(output_browser)
        battery_fmts = image_fragment_formats(output_browser.document())
        battery_expected_w = min(900, battery_avail)
        result(len(battery_fmts) == 1
               and abs(battery_fmts[0][0] - battery_expected_w) <= 0.5
               and abs(battery_fmts[0][1]
                       - round(300 * battery_expected_w / 900)) <= 1.0
               and output_browser.horizontalScrollBar().maximum() == 0
               and output_browser.verticalScrollBar().maximum() == 0,
               "after a form resize to {}x{} the figure width tracks "
               "min(intrinsic, available) ({:.0f}x{:.0f}, avail {}, hbar "
               "max {}, vbar max {})".format(
                   resize_width, resize_height, battery_fmts[0][0],
                   battery_fmts[0][1], battery_avail,
                   output_browser.horizontalScrollBar().maximum(),
                   output_browser.verticalScrollBar().maximum()))
    battery_before = image_fragment_formats(output_browser.document())
    battery_height = output_browser.height()
    settle_image_fit()
    settle_image_fit()
    result(image_fragment_formats(output_browser.document())
           == battery_before
           and output_browser.height() == battery_height,
           "two consecutive settles after the battery are idempotent "
           "(fragment {} both times, height {})".format(
               battery_before, battery_height))

    # The LIVE display payload (round 22): a display_data image arriving
    # DURING a run goes through _on_display_arrived -> _append_live_html,
    # and the fit runs THERE (once per payload, never per stream chunk).
    # The running state is driven the way the suite drives the live-text
    # path above; the appended image is the document's LAST fragment.
    form._running = True
    form._running_cell_index = output_index
    try:
        form._on_display_arrived({"image/png": wide_b64}, {})
    finally:
        form._running = False
        form._running_cell_index = -1
    app.processEvents()
    live_avail = installed_cells.available_image_width(output_browser)
    live_fmts = image_fragment_formats(output_browser.document())
    result(len(live_fmts) >= 2
           and abs(live_fmts[-1][0] - min(1200, live_avail)) <= 0.5
           and abs(live_fmts[-1][1] - round(100 * live_fmts[-1][0] / 1200))
           <= 1.0
           and output_browser.horizontalScrollBar().maximum() == 0,
           "a live display_data image is scaled on arrival (last fragment "
           "{:.0f}x{:.0f}, avail {}, hbar max {})".format(
               live_fmts[-1][0], live_fmts[-1][1], live_avail,
               output_browser.horizontalScrollBar().maximum()))

    # Idempotence: two consecutive fits settle on the SAME fixed height
    # (an unchanged setFixedHeight must not re-enter the layout).
    form._fit_browser_to_content(output_browser)
    fit_height_first = output_browser.height()
    form._fit_browser_to_content(output_browser)
    app.processEvents()
    result(output_browser.height() == fit_height_first,
           "two consecutive fits settle on one fixed height ({} both "
           "times)".format(fit_height_first))

    # Restore the cell's output area (the document was never touched:
    # this cell's stored outputs are empty, so the browser hides again).
    form._render_outputs(
        output_index,
        form.document.get_cell(output_index).get("outputs") or [])
    app.processEvents()

    # The Qt-free drop decision truth table (the ONE function every
    # consumer shares: the cell editors, the form window and the glue's
    # application-level filter).
    def _mime(*paths):
        mime = QMimeData()
        mime.setUrls([QUrl(p) for p in paths])
        return mime

    result(installed_drop_policy.ipynb_drop_decision(
        _mime("file:///C:/x/nb.ipynb")) == "consume",
        "a single local .ipynb URL is consumed")
    result(installed_drop_policy.ipynb_drop_decision(
        _mime("file:///C:/x/nb.IPYNB")) == "consume",
        "the suffix test is case-insensitive")
    result(installed_drop_policy.ipynb_drop_decision(
        _mime("file:///C:/x/a.ipynb", "file:///C:/x/b.ipynb")) == "consume",
        "an all-.ipynb multi-file drag is consumed (first URL wins)")
    result(installed_drop_policy.ipynb_drop_decision(
        _mime("file:///C:/x/a.ipynb", "file:///C:/x/b.txt")) == "pass",
        "a MIXED drag passes through (never half-consumed)")
    result(installed_drop_policy.ipynb_drop_decision(
        _mime("file:///C:/x/b.txt")) == "pass",
        "a non-.ipynb file drag passes through")
    result(installed_drop_policy.ipynb_drop_decision(
        _mime("https://host/nb.ipynb")) == "pass",
        "a remote URL is never consumed")
    result(installed_drop_policy.ipynb_drop_decision(QMimeData()) == "pass"
           and installed_drop_policy.ipynb_drop_decision(None) == "pass",
        "an empty or missing mime passes through")

    # The cell editors IGNORE a .ipynb file drag (it must propagate up to
    # the form's window-level handler; QPlainTextEdit accepts URL drags by
    # default and would insert the file URL as text) while the round-8
    # snippet drag stays accepted.
    ipynb_mime = QMimeData()
    ipynb_mime.setUrls([QUrl.fromLocalFile(open_path)])
    target_editor = code_record["editor"]
    editor_text_before = target_editor.toPlainText()
    editor_drag = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction,
                                  ipynb_mime, Qt.MouseButton.LeftButton,
                                  Qt.KeyboardModifier.NoModifier)
    target_editor.dragEnterEvent(editor_drag)
    result(not editor_drag.isAccepted(),
           "the editor ignores the .ipynb drag-enter (it propagates)")
    editor_drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                             ipynb_mime, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier)
    editor_drop.setDropAction(Qt.DropAction.CopyAction)
    target_editor.dropEvent(editor_drop)
    result(not editor_drop.isAccepted()
           and target_editor.toPlainText() == editor_text_before,
           "the editor ignores the .ipynb drop: nothing inserted")
    snippet_mime = QMimeData()
    snippet_mime.setData(installed_core.SNIPPET_MIME, b"import_volume")
    snippet_drag = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction,
                                   snippet_mime, Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier)
    target_editor.dragEnterEvent(snippet_drag)
    result(snippet_drag.isAccepted(),
           "a dfnb snippet drag is still accepted by the editor (round 8)")

    # The form-level corrupt open: ONE log entry, the notebook untouched.
    # The untouched claim is a PRE/POST SNAPSHOT, not a constant: earlier
    # suite sections legitimately grow this document (the Run All pin
    # adds a cell), so the honest pin is that the refused open changes
    # NOTHING -- cell count, path and every cell source captured right
    # before the attempt must be identical after it.
    pre_refusal_count = form.document.cell_count()
    pre_refusal_path = form.file_path
    pre_refusal_sources = [form.document.get_cell(i)["source"]
                           for i in range(pre_refusal_count)]
    log_handler_r9 = _RecordingLogHandler()
    form_logger.addHandler(log_handler_r9)
    try:
        records_before_r9 = len(log_handler_r9.records)
        corrupt_reason = form._open_notebook_at(corrupt_path)
        r9_entries = log_handler_r9.records[records_before_r9:]
        result(corrupt_reason is not None
               and "could not be read" in corrupt_reason,
               "the drop entry refuses a corrupt file with a reason "
               "({!r})".format(corrupt_reason))
        result(len(r9_entries) == 1
               and r9_entries[0].startswith("Dragonfly Notebook:"),
               "the corrupt open logged exactly ONE 'Dragonfly Notebook' "
               "entry ({} entries)".format(len(r9_entries)))
        result(form.document.cell_count() == pre_refusal_count
               and form.file_path == pre_refusal_path
               and [form.document.get_cell(i)["source"]
                    for i in range(form.document.cell_count())]
               == pre_refusal_sources,
               "the refused open left the notebook and path untouched "
               "({} cells, path unchanged, sources unchanged)".format(
                   pre_refusal_count))
    finally:
        form_logger.removeHandler(log_handler_r9)

    # The form-level drop: a valid local .ipynb opens (same tail as the
    # dialog open: document swapped, cells rebuilt, title updated); a
    # mixed drag is refused without touching the state.
    dropped_doc = installed_core.NotebookDocument.new_notebook()
    dropped_doc.set_cell_source(0, "1 + 1")
    dropped_doc.add_cell("markdown", source="# dropped via argv")
    dropped_path = os.path.join(temp_dir, "dropped_pin.ipynb")
    dropped_doc.save(dropped_path)
    # The synthetic events keep their QMimeData in a NAMED binding:
    # PyQt6 does NOT transfer mime ownership to a QDropEvent or a
    # QDragEnterEvent, so a temporary mime is garbage-collected the moment
    # the constructor returns -- the event keeps a DANGLING mimeData
    # pointer and the next mimeData() call inside the handler is an
    # access violation (the first staged run of this block crashed exactly
    # there, inside ipynb_drop_decision). A real OS drag never hits this:
    # Qt owns the mime for the event's lifetime; only hand-built test
    # events need the binding.
    valid_mime = _mime(QUrl.fromLocalFile(dropped_path).toString())
    form_drop = QDropEvent(QPointF(30, 30), Qt.DropAction.CopyAction,
                           valid_mime,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
    form_drop.setDropAction(Qt.DropAction.CopyAction)
    form.dropEvent(form_drop)
    app.processEvents()
    result(form_drop.isAccepted()
           and form.document.cell_count() == 2
           and os.path.normcase(os.path.normpath(form.file_path))
           == os.path.normcase(os.path.normpath(dropped_path))
           and "dropped_pin" in form.windowTitle(),
           "dropping a valid .ipynb on the form opened it ({} cells, "
           "title {!r})".format(form.document.cell_count(),
                                form.windowTitle()))
    mixed_mime = _mime(QUrl.fromLocalFile(dropped_path).toString(),
                       QUrl.fromLocalFile(corrupt_path + ".txt"))
    mixed_drop = QDropEvent(QPointF(30, 30), Qt.DropAction.CopyAction,
                            mixed_mime,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    mixed_drop.setDropAction(Qt.DropAction.CopyAction)
    form.dropEvent(mixed_drop)
    result(not mixed_drop.isAccepted()
           and os.path.normcase(os.path.normpath(form.file_path))
           == os.path.normcase(os.path.normpath(dropped_path)),
           "a mixed drag is refused by the form (state untouched)")

    # The application-level filter's own event contract: a consumed drag
    # never reaches Dragonfly's C++ dispatcher; a consumed Drop launches
    # the app with the dropped path (the launcher is resolved at EVENT
    # time, so the monkeypatch stands in for the spawn); anything else
    # passes through untouched.
    real_launcher = installed_glue._launch_notebook_for_path
    launched_paths = []
    try:
        installed_glue._launch_notebook_for_path = launched_paths.append
        filt = installed_glue._DROP_FILTER
        result(filt is not None,
               "the round-9 filter is installed for the event pins")
        filter_enter_mime = _mime("file:///C:/x/nb.ipynb")
        filter_enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction,
                                       filter_enter_mime,
                                       Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier)
        consumed_enter = filt.eventFilter(form, filter_enter)
        result(consumed_enter is True and filter_enter.isAccepted(),
               "the filter consumes the .ipynb drag-enter")
        filter_drop_mime = _mime("file:///C:/x/nb.ipynb")
        filter_drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                                 filter_drop_mime,
                                 Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier)
        filter_drop.setDropAction(Qt.DropAction.CopyAction)
        consumed_drop = filt.eventFilter(form, filter_drop)
        result(consumed_drop is True
               and launched_paths == ["C:/x/nb.ipynb"],
               "the filter's consumed Drop launched the app with the "
               "dropped path (got {!r})".format(launched_paths))
        mixed_filter_mime = _mime("file:///C:/x/nb.ipynb",
                                  "file:///C:/x/b.txt")
        mixed_filter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction,
                                       mixed_filter_mime,
                                       Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier)
        result(filt.eventFilter(form, mixed_filter) is False,
               "a mixed drag passes through the filter untouched")
        other_filter_mime = _mime("file:///C:/x/b.txt")
        other_filter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction,
                                       other_filter_mime,
                                       Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier)
        result(filt.eventFilter(form, other_filter) is False,
               "a non-.ipynb drag passes through the filter untouched")
        non_drag = QEvent(QEvent.Type.HoverEnter)
        result(filt.eventFilter(form, non_drag) is False,
               "a non-drag event passes through the filter untouched")
    finally:
        installed_glue._launch_notebook_for_path = real_launcher

    # ------------------------------------------------------------------
    # Round 21: the rendered markdown preview's full-height fit and the
    # panel's scroll preservation across structural actions. The form at
    # this point holds the dropped two-cell notebook (code + markdown)
    # from the drop pins above; the tall markdown cell reuses it and the
    # scroll repro grows it, and a fresh reload at the end restores the
    # dropped shape (and pins the open-at-top contract).

    # A tall RENDERED markdown cell takes its full content height too
    # (the SAME browser the outputs use, the SAME fit): 45 headings lay
    # out to ~950 px, far past the retired cap, and the browser must
    # show every line -- no internal vertical scrolling, the panel
    # scrolls instead. The render goes through the REAL user flow: the
    # Edit flip puts the cell's id back into the EDIT set, the long
    # source is typed into the editor, and _run_cell_at renders it (a
    # markdown run never starts the kernel).
    markdown_index = next(
        i for i in range(form.document.cell_count())
        if form.document.get_cell(i)["cell_type"] == "markdown")
    markdown_cell = form.document.get_cell(markdown_index)
    form._on_markdown_edit_button_clicked(markdown_index)
    markdown_source = "\n".join(
        "## Section {} -- a heading with some prose below it".format(i)
        for i in range(45))
    form._cell_widgets[markdown_index]["editor"].setPlainText(
        markdown_source)
    app.processEvents()
    result(form._run_cell_at(markdown_index) is False,
           "a markdown cell's run renders it instead of the kernel")
    app.processEvents()
    md_browser = form._cell_widgets[markdown_index]["browser"]
    md_doc_h = md_browser.document().size().height()
    result(md_browser.height()
           == int(math.ceil(md_doc_h)) + installed_form._OUTPUT_HEIGHT_FUDGE
           and md_browser.height() > 400
           and md_browser.verticalScrollBar().maximum() == 0
           and md_browser.verticalScrollBarPolicy()
           == Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
           "a tall rendered markdown cell renders at its full content "
           "height (browser {} = ceil({:.1f}) + {} fudge, past the "
           "retired cap, vbar max {}, policy AlwaysOff)".format(
               md_browser.height(), md_doc_h,
               installed_form._OUTPUT_HEIGHT_FUDGE,
               md_browser.verticalScrollBar().maximum()))

    # The GENERIC promise covers markdown too (round 22): a rendered
    # markdown cell whose source embeds a WIDE LOCAL PNG (a real temp
    # file, the absolute path in the markdown image syntax) is scaled to
    # the available width exactly like an output figure -- same
    # fit_document_images, same width arithmetic. The render goes
    # through the real user flow (Edit flip, editor text, run), and the
    # cell is left RENDERED for the flip-back pin below.
    form._on_markdown_edit_button_clicked(markdown_index)
    md_wide_img = QImage(2000, 400, QImage.Format.Format_RGB32)
    md_wide_img.fill(0xFF3A5F9E)
    md_png_path = os.path.join(tempfile.mkdtemp(prefix="dfnb_r22_md_"),
                               "md_wide.png")
    result(md_wide_img.save(md_png_path, "PNG"),
           "the markdown wide pin encoded its 2000x400 PNG")
    form._cell_widgets[markdown_index]["editor"].setPlainText(
        "## wide image\n\n![figure]({})\n".format(
            md_png_path.replace("\\", "/")))
    app.processEvents()
    result(form._run_cell_at(markdown_index) is False,
           "the markdown wide-image cell renders instead of the kernel")
    app.processEvents()
    # The markdown render now ARMS the debounced pass instead of fitting
    # directly (round 22 review: a render-time direct fit lands while
    # the main scrollbar is still up and scales ~15 px narrow, then the
    # debounced pass widens it -- a visible pop), so the settled width
    # exists only after that pass runs: the pump here is the honest
    # wait, not a paper-over.
    settle_image_fit(0.35)
    md_avail = installed_cells.available_image_width(md_browser)
    md_fmts = image_fragment_formats(md_browser.document())
    result(len(md_fmts) == 1
           and md_fmts[0][0] < 2000.0
           and abs(md_fmts[0][0] - min(2000, md_avail)) <= 0.5
           and abs(md_fmts[0][1] - round(400 * md_fmts[0][0] / 2000))
           <= 1.0
           and md_browser.horizontalScrollBar().maximum() == 0,
           "a rendered markdown cell's wide embedded PNG is scaled to "
           "the available width ({:.0f}x{:.0f}, avail {}, aspect "
           "preserved, hbar max {})".format(
               md_fmts[0][0], md_fmts[0][1], md_avail,
               md_browser.horizontalScrollBar().maximum()))
    form._on_markdown_edit_button_clicked(markdown_index)
    app.processEvents()
    result(md_browser.isHidden()
           and form._cell_widgets[markdown_index]["editor"].isVisible(),
           "flipping a rendered markdown cell back to EDIT hides the "
           "browser and shows the editor")

    # Round 22 review: the generic fit is image-FORMAT agnostic -- a
    # data:image/jpeg figure decodes and sizes through the SAME
    # QImageReader-on-decoded-bytes path a PNG takes (probed: both
    # formats report their true sizes) and must scale identically.
    # Pinned as a direct fragment on a standalone visible browser (the
    # cheapest honest probe: the same available_image_width and
    # fit_document_images every render path calls).
    jpeg_img = QImage(1600, 300, QImage.Format.Format_RGB32)
    jpeg_img.fill(0xFF5F9E3A)
    jpeg_buf = QBuffer()
    jpeg_buf.open(QBuffer.OpenModeFlag.ReadWrite)
    result(jpeg_img.save(jpeg_buf, "JPEG"),
           "the jpeg data-URL pin encoded its 1600x300 JPEG")
    jpeg_b64 = base64.b64encode(bytes(jpeg_buf.data())).decode("ascii")
    jpeg_buf.close()
    jpeg_browser = QTextBrowser()
    jpeg_browser.resize(800, 300)
    jpeg_browser.show()
    app.processEvents()
    jpeg_avail = installed_cells.available_image_width(jpeg_browser)
    jpeg_browser.setHtml(
        '<p><img src="data:image/jpeg;base64,{}"></p>'.format(jpeg_b64))
    app.processEvents()
    jpeg_changed = installed_cells.fit_document_images(
        jpeg_browser.document(), jpeg_avail)
    jpeg_fmts = image_fragment_formats(jpeg_browser.document())
    result(jpeg_changed >= 1
           and len(jpeg_fmts) == 1
           and abs(jpeg_fmts[0][0] - min(1600, jpeg_avail)) <= 0.5
           and abs(jpeg_fmts[0][1] - round(300 * jpeg_fmts[0][0] / 1600))
           <= 1.0,
           "a data:image/jpeg figure scales like a PNG (fragment {} "
           "avail {}, changed {})".format(jpeg_fmts, jpeg_avail,
                                          jpeg_changed))
    jpeg_browser.hide()
    jpeg_browser.deleteLater()
    app.processEvents()

    # The far safety valve (round 21): the full-height fit has no
    # user-facing cap, but Qt itself refuses widgets taller than
    # QWIDGETSIZE_MAX (16777215 px) -- past that setFixedHeight clamps
    # while the vertical policy stays AlwaysOff and the bottom of the
    # output is unreachable. The valve constant is SHRUNK to 1000 here
    # to exercise the valve path itself: a ~100-line output whose fitted
    # height exceeds the ceiling lands AT the ceiling and the browser's
    # vertical policy flips to AsNeeded (the extreme output stays
    # reachable), a second fit under the valve is idempotent, and with
    # the constant restored a refit of a normal output is full-height
    # again at AlwaysOff with vbar max 0.
    real_max_full_height = installed_form._MAX_FULL_HEIGHT
    try:
        installed_form._MAX_FULL_HEIGHT = 1000
        valve_source = "\n".join(
            "## Valve section {} -- a heading with some prose below it"
            .format(i) for i in range(100))
        form._cell_widgets[markdown_index]["editor"].setPlainText(
            valve_source)
        app.processEvents()
        form._run_cell_at(markdown_index)
        app.processEvents()
        valve_doc_h = md_browser.document().size().height()
        result(md_browser.height() <= installed_form._MAX_FULL_HEIGHT
               and int(math.ceil(valve_doc_h))
               + installed_form._OUTPUT_HEIGHT_FUDGE > 1000
               and md_browser.verticalScrollBarPolicy()
               == Qt.ScrollBarPolicy.ScrollBarAsNeeded
               and md_browser.verticalScrollBar().maximum() > 0,
               "the far valve pins the height at _MAX_FULL_HEIGHT and "
               "flips that browser to AsNeeded (fitted {} vs ceiling "
               "{}, browser {}, vbar max {})".format(
                   int(math.ceil(valve_doc_h))
                   + installed_form._OUTPUT_HEIGHT_FUDGE,
                   installed_form._MAX_FULL_HEIGHT, md_browser.height(),
                   md_browser.verticalScrollBar().maximum()))
        fitted_under_valve = md_browser.height()
        form._fit_browser_to_content(md_browser)
        result(md_browser.height() == fitted_under_valve,
               "the fit stays idempotent under the valve (two fits, one "
               "height {})".format(md_browser.height()))
    finally:
        installed_form._MAX_FULL_HEIGHT = real_max_full_height
    form._fit_browser_to_content(md_browser)
    app.processEvents()
    result(md_browser.height()
           == int(math.ceil(md_browser.document().size().height()))
           + installed_form._OUTPUT_HEIGHT_FUDGE
           and md_browser.verticalScrollBarPolicy()
           == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
           and md_browser.verticalScrollBar().maximum() == 0,
           "with the constant restored the refit is full-height again "
           "(browser {}, policy AlwaysOff, vbar max {})".format(
               md_browser.height(),
               md_browser.verticalScrollBar().maximum()))

    # The scroll-preservation repro (round 21): a 25-cell notebook whose
    # CODE CELLS CARRY 40-LINE STORED OUTPUTS -- recorded on the document
    # BEFORE the rebuild, so they render through the real builder at
    # _add_cell_widget time -- scrolled to ~60 percent, then a STRUCTURAL
    # action. Before round 21 every action collapsed the scroll value
    # to 0: the rebuild tears the widgets down (the scroll area's value
    # falls to 0) and the synchronous _focus_cell ensure lands while the
    # rebuilt heights are still unsettled, so the viewport ended at the
    # top once the layout settled taller. The deferred settle re-ensures
    # the current cell's frame after the layout has settled -- and with
    # tall stored outputs ABOVE the current cell, the rebuilt layout
    # keeps growing for several event-loop turns after that, so the
    # settle must chain BOUNDED zero-timer passes while the geometry is
    # still moving or the frame is out of view (the single-pass shape
    # left the current frame ~1200 px below the viewport with current
    # 15/23/24, stable across pumping). The first fixture's outputless
    # cells let that slip through; these are the shapes that catch it.
    scroll_bar = form.scrollArea.verticalScrollBar()

    def _frame_in_viewport(index):
        frame = form._cell_widgets[index]["frame"]
        top_left = frame.mapTo(form.scrollArea.viewport(), QPoint(0, 0))
        return form.scrollArea.viewport().rect().intersects(
            QRect(top_left, frame.size()))

    def _pump():
        app.processEvents()
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)

    for _ in range(23):
        form.document.add_cell("code")
    for _index in range(form.document.cell_count()):
        if form.document.get_cell(_index)["cell_type"] != "code":
            continue
        form.document.set_cell_source(
            _index, "\n".join("y = {} * {}".format(_index, _j)
                              for _j in range(12)))
        form.document.record_execution(
            _index, _index + 1,
            [{"output_type": "stream", "name": "stdout",
              "text": "\n".join("stored output line {} of cell {}"
                                .format(_j, _index)
                                for _j in range(40))}])
    form._rebuild_cells()
    _pump()
    result(form.document.cell_count() == 25
           and scroll_bar.maximum() > 0
           and form._cell_widgets[2]["browser"].height() > 300,
           "the scroll repro built its 25-cell notebook with 40-line "
           "stored outputs rendered through the real builder (scroll "
           "max {}, code cell 2's browser {} px)".format(
               scroll_bar.maximum(),
               form._cell_widgets[2]["browser"].height()))

    def _scroll_repro(label, action, current):
        scroll_bar.setValue(int(scroll_bar.maximum() * 0.6))
        _pump()
        before = scroll_bar.value()
        form._set_current_cell(current)
        action()
        _pump()
        after = scroll_bar.value()
        index = form._current_cell_index
        result(after > 0 and _frame_in_viewport(index),
               "{} keeps the viewport on the current cell (scroll {} -> "
               "{}, current {}, frame in viewport)".format(
                   label, before, after, index))

    _scroll_repro("deleting a visible middle cell",
                  form.on_delete_cell, 15)
    _scroll_repro("moving a cell up", form.on_move_cell_up, 23)
    # 24 cells remain after the delete above, so the move-down current
    # stays inside the range (22 swaps with 23).
    _scroll_repro("moving a cell down", form.on_move_cell_down, 22)
    _scroll_repro("inserting a cell below (the Alt+Enter insertion)",
                  lambda: form._navigate_after_run(12, insert_after=True),
                  12)
    _scroll_repro("appending at the end (the Shift+Enter append)",
                  lambda: form._navigate_after_run(
                      form.document.cell_count() - 1, insert_after=False),
                  form.document.cell_count() - 1)

    # The round-22b anchor pin: the FIGURE-RICH shape of the same
    # contract. The repro above carried tall TEXT outputs; the debounced
    # image pass (round 22) rescales STORED figures about 140 ms after a
    # structural action, and that height change lands AFTER the round-21
    # settle chain has finished -- without the anchor the viewport
    # drifts off the current cell in exactly the most common real
    # notebook (matplotlib outputs). Re-record the same notebook with a
    # 1200x400 stored display_data figure per code cell, then (a) a
    # delete and (b) a resize narrower, each pumped PAST the image
    # debounce; the viewport must still be on the current cell, the
    # stored figure scaled, and no scrollbar in either direction. The
    # control pins the no-yank rule: with the current cell scrolled FAR
    # off-screen (the user's own decision), a resize must not move the
    # scroll value.
    anchor_img = QImage(1200, 400, QImage.Format.Format_RGB32)
    anchor_img.fill(0xFF3A5F9E)
    anchor_buf = QBuffer()
    anchor_buf.open(QBuffer.OpenModeFlag.ReadWrite)
    result(anchor_img.save(anchor_buf, "PNG"),
           "the anchor pin encoded its 1200x400 PNG")
    anchor_b64 = base64.b64encode(bytes(anchor_buf.data())).decode("ascii")
    anchor_buf.close()
    while form.document.cell_count() > 14:
        form.document.remove_cell(form.document.cell_count() - 1)
    for _index in range(form.document.cell_count()):
        form.document.set_cell_source(_index, "import numpy")
        form.document.record_execution(
            _index, _index + 1,
            [{"output_type": "display_data",
              "data": {"image/png": anchor_b64}, "metadata": {}}])
    form._rebuild_cells()
    _pump()
    settle_image_fit(1.0)
    result(form.document.cell_count() == 14,
           "the anchor pin rebuilt its 14-cell figure-rich notebook "
           "(each cell a stored 1200x400 display_data figure)")

    form._focus_cell(8)
    settle_image_fit(0.3)
    anchor_current = 8
    anchor_top = form._cell_widgets[anchor_current]["frame"].mapTo(
        form.scrollArea.viewport(), QPoint(0, 0)).y()
    scroll_bar.setValue(scroll_bar.value() + anchor_top - 30)
    settle_image_fit(0.3)
    result(_frame_in_viewport(anchor_current),
           "the anchor pin's baseline has the current figure cell in "
           "view near the viewport top (current {}, top {})".format(
               anchor_current, anchor_top))

    # (a) the structural delete: pump past the round-21 settle AND the
    # image debounce; the anchor's quiet pass must hand the viewport
    # back to the (now) current cell after the figures above shrank.
    form.on_delete_cell()
    settle_image_fit(0.9)
    anchor_current = form._current_cell_index
    anchor_browser = form._cell_widgets[anchor_current]["browser"]
    anchor_avail = installed_cells.available_image_width(anchor_browser)
    anchor_fmts = image_fragment_formats(anchor_browser.document())
    result(_frame_in_viewport(anchor_current)
           and len(anchor_fmts) >= 1
           and anchor_fmts[0][0] <= anchor_avail + 0.5
           and anchor_browser.horizontalScrollBar().maximum() == 0
           and anchor_browser.verticalScrollBar().maximum() == 0,
           "after a delete the viewport is still on the current cell of "
           "the figure-rich notebook and the stored figure is scaled "
           "(current {}, fragment {} avail {}, hbar max {}, vbar max {})"
           .format(anchor_current, anchor_fmts, anchor_avail,
                   anchor_browser.horizontalScrollBar().maximum(),
                   anchor_browser.verticalScrollBar().maximum()))

    # (b) the window resize narrower: same rule through the resize
    # filter. The current frame is re-anchored to the viewport top
    # FIRST (the (a) end state differs between the anchored and
    # unanchored worlds), so an above-content shrink cannot hide behind
    # a lucky intersection.
    anchor_top = form._cell_widgets[anchor_current]["frame"].mapTo(
        form.scrollArea.viewport(), QPoint(0, 0)).y()
    scroll_bar.setValue(scroll_bar.value() + anchor_top - 30)
    settle_image_fit(0.3)
    form.resize(560, 900)
    settle_image_fit(0.9)
    anchor_current = form._current_cell_index
    anchor_browser = form._cell_widgets[anchor_current]["browser"]
    anchor_avail = installed_cells.available_image_width(anchor_browser)
    anchor_fmts = image_fragment_formats(anchor_browser.document())
    result(_frame_in_viewport(anchor_current)
           and len(anchor_fmts) >= 1
           and anchor_fmts[0][0] <= anchor_avail + 0.5
           and anchor_browser.horizontalScrollBar().maximum() == 0
           and anchor_browser.verticalScrollBar().maximum() == 0,
           "after a narrower resize the viewport is still on the current "
           "cell and the figure re-scaled (current {}, fragment {} avail "
           "{}, hbar max {}, vbar max {})".format(
               anchor_current, anchor_fmts, anchor_avail,
               anchor_browser.horizontalScrollBar().maximum(),
               anchor_browser.verticalScrollBar().maximum()))

    # The ACTIVE-anchor no-yank pin (round 22 review): a USER scroll
    # action while the anchor is ACTIVE must retire it WITHOUT an
    # ensure. The wheel step's actionTriggered fires BEFORE the
    # scrollbar value moves, so the form defers its re-read one
    # event-loop turn; the pin drives the same seam the wheel uses --
    # emit the action FIRST, then move the value (the move lands after
    # the signal) -- and pumps past the deferred hook and the quiet
    # passes: the scroll value must stay where the user left it and the
    # anchor must be retired.
    anchor_top = form._cell_widgets[anchor_current]["frame"].mapTo(
        form.scrollArea.viewport(), QPoint(0, 0)).y()
    scroll_bar.setValue(scroll_bar.value() + anchor_top - 30)
    settle_image_fit(0.3)
    form.resize(1400, 900)
    active_deadline = time.monotonic() + 2.0
    while time.monotonic() < active_deadline \
            and form._image_fit_anchor_state != 2:
        app.processEvents()
        time.sleep(0.005)
    result(form._image_fit_anchor_state == 2,
           "the widening resize re-promoted the anchor while the current "
           "cell is in view (state {})".format(
               form._image_fit_anchor_state))
    scroll_bar.actionTriggered.emit(
        QAbstractSlider.SliderAction.SliderSingleStepAdd.value)
    scroll_bar.setValue(0)
    settle_image_fit(0.6)
    result(form._image_fit_anchor_state == 0
           and scroll_bar.value() == 0
           and not _frame_in_viewport(anchor_current),
           "a user scroll while the anchor is active retires it without "
           "yanking the view back (state {}, scroll {})".format(
               form._image_fit_anchor_state, scroll_bar.value()))

    # The no-yank control, IDLE-anchor shape (the arm-time gate): with
    # the anchor retired and the current cell scrolled FAR off-screen
    # (the user's own scroll), a resize must not move the scroll value
    # -- no arm saw the frame in view, so the anchor never promotes.
    scroll_bar.setValue(0)
    settle_image_fit(0.25)
    anchor_before = scroll_bar.value()
    form.resize(1000, 900)
    settle_image_fit(0.6)
    anchor_after = scroll_bar.value()
    result(abs(anchor_after - anchor_before) <= 2
           and not _frame_in_viewport(anchor_current),
           "with the current cell scrolled far off-screen a resize does "
           "not yank the scroll ({} -> {})".format(
               anchor_before, anchor_after))

    # Opening a notebook still lands at the TOP: a load rebuilds with no
    # focus index (cell 0) and the deferred settle must not move the
    # viewport off it.
    restored_doc = installed_core.NotebookDocument.new_notebook()
    restored_doc.set_cell_source(0, "1 + 1")
    restored_doc.add_cell("markdown", source="# dropped via argv")
    form._load_notebook_document(restored_doc, dropped_path)
    _pump()
    result(scroll_bar.value() == 0 and form._current_cell_index == 0,
           "opening a notebook still lands at the top (scroll {}, "
           "current {})".format(scroll_bar.value(),
                                form._current_cell_index))

    # ------------------------------------------------------------------
    print("\n=== (6) the spawned notebook application, offscreen ===", flush=True)

    # A REAL second process: notebook_app.py from the INSTALLED tree,
    # spawned the way the launcher spawns it (the same interpreter, the
    # same stripped environment) but with PIPES for its stdout/stderr so
    # the smoke markers are observable, and QT_QPA_PLATFORM=offscreen so
    # the gate needs no display. The app's documented DFNB_SMOKE hooks
    # print one line per behavior this section pins:
    #   DFNB-APP-ORSFREE     the process is ORS-free ('ORSServiceClass'
    #                        not in sys.modules -- the round-8b marker)
    #   DFNB-APP-UP          the form is shown (app boots)
    #   DFNB-APP-CONNECTED   the bridge client connected
    #   DFNB-DFNB-OK objects=N  a real dfnb round-trip through the pipe
    #   DFNB-BUSY-BAR ...    the busy bar's no-percentage state
    #   DFNB-TICKS n         live timer ticks mid-run (GUI responsiveness)
    #   DFNB-STOP-OK         the Stop path interrupted the running cell
    #   DFNB-DISCONNECTED    the banner state after the bridge closes
    # Every wait below PUMPS this process's event loop, so the bridge
    # keeps serving the child's requests while the harness waits.
    app_path = os.path.join(installed_dir, "notebook_app.py")
    result(os.path.isfile(app_path),
           "the installed notebook_app.py exists: {}".format(app_path))
    child_env = dict(os.environ)
    child_env["KMP_DUPLICATE_LIB_OK"] = "True"
    child_env.pop("PYTHONPATH", None)      # the launcher's strip, verbatim
    child_env.pop("QT_PLUGIN_PATH", None)  # the launcher's strip, verbatim
    child_env["QT_QPA_PLATFORM"] = "offscreen"
    child_env["DFNB_SMOKE"] = "1"
    child_env["DFNB_AUTORUN"] = "1"
    child_env["DFNB_AUTOSTOP_AFTER"] = "1.0"
    stdout_lines = []
    stderr_lines = []
    spawn_error = []

    def _reader(stream, sink):
        try:
            for line in stream:
                sink.append(line.rstrip("\r\n"))
        except Exception as exc:  # the reader must never kill the harness
            spawn_error.append(str(exc))

    app_child = subprocess.Popen(
        [sys.executable, app_path, bridge_service.pipe_name],
        cwd=_ROOT, env=child_env, creationflags=_NO_WINDOW,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace")
    reader_out = threading.Thread(target=_reader,
                                  args=(app_child.stdout, stdout_lines),
                                  daemon=True)
    reader_err = threading.Thread(target=_reader,
                                  args=(app_child.stderr, stderr_lines),
                                  daemon=True)
    reader_out.start()
    reader_err.start()

    def _wait_marker(prefix, timeout_s, sink=None):
        """Pumps this process's event loop until a child prints a line
        starting with ``prefix`` (the bridge serves the child's requests
        from the pumped events). Reads ``sink`` (default: the first
        child's stdout list). Returns the line, or None on timeout."""
        sink = stdout_lines if sink is None else sink
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for line in list(sink):
                if line.startswith(prefix):
                    return line
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        return None

    try:
        def _tail():
            return ("\n".join(stdout_lines[-12:]) + "\n-- stderr --\n"
                    + "\n".join(stderr_lines[-12:]))

        line = _wait_marker("DFNB-APP-UP", 90)
        result(line is not None,
               "the spawned application booted offscreen (DFNB-APP-UP)"
               if line is not None else
               "the spawned application never booted (no DFNB-APP-UP in "
               "90 s); tail:\n{}".format(_tail()))
        line = _wait_marker("DFNB-APP-ORSFREE", 45)
        result(line == "DFNB-APP-ORSFREE True",
               "the spawned application is ORS-free by its own marker "
               "(DFNB-APP-ORSFREE True)"
               if line == "DFNB-APP-ORSFREE True" else
               "the spawned application loaded ORS or never printed the "
               "marker (DFNB-APP-ORSFREE: {!r}); tail:\n{}".format(
                   line, _tail()))
        line = _wait_marker("DFNB-APP-CONNECTED", 45)
        result(line is not None,
               "the spawned application connected to the bridge "
               "(DFNB-APP-CONNECTED)"
               if line is not None else
               "the spawned application never connected (no "
               "DFNB-APP-CONNECTED); tail:\n{}".format(_tail()))
        line = _wait_marker("DFNB-DFNB-", 45)
        roundtrip_ok = line is not None and line.startswith("DFNB-DFNB-OK objects=")
        objects_seen = -1
        if roundtrip_ok:
            try:
                objects_seen = int(line.rsplit("=", 1)[1])
            except ValueError:
                roundtrip_ok = False
        result(roundtrip_ok and objects_seen >= 1,
               "a dfnb round-trip crossed the pipe into this suite's ORS "
               "bridge: {} (the 'Probe Volume' fixture is listed)"
               .format(line)
               if roundtrip_ok and objects_seen >= 1 else
               "the dfnb round-trip failed (no DFNB-DFNB-OK with objects "
               ">= 1); tail:\n{}".format(_tail()))
        line = _wait_marker("DFNB-BUSY-BAR", 120)
        result(line == "DFNB-BUSY-BAR textVisible=False range=0-0",
               "the busy bar is indeterminate with NO percentage text "
               "({!r})".format(line)
               if line is not None else
               "the autorun cell never started (no DFNB-BUSY-BAR); "
               "tail:\n{}".format(_tail()))
        stop_line = _wait_marker("DFNB-STOP-OK", 150)
        ticks = [int(l.rsplit(" ", 1)[1]) for l in list(stdout_lines)
                 if l.startswith("DFNB-TICKS ")]
        result(stop_line is not None,
               "the Stop path interrupted the sleeping cell "
               "(DFNB-STOP-OK)"
               if stop_line is not None else
               "the stop never completed (no DFNB-STOP-OK); tail:\n{}"
               .format(_tail()))
        result(len(ticks) >= 3 and ticks == sorted(ticks)
               and len(set(ticks)) == len(ticks),
               "the GUI stayed responsive while the cell ran: {} live "
               "timer tick(s) mid-run (DFNB-TICKS)".format(len(ticks))
               if len(ticks) >= 3 else
               "too few live timer ticks mid-run ({} DFNB-TICKS lines) -- "
               "the GUI thread looks blocked; tail:\n{}".format(
                   len(ticks), _tail()))

        # The disconnect pin: close the bridge -- the spawned application
        # must show its disconnected state (DFNB-DISCONNECTED). This
        # suite's OWN form loses the same bridge, so its banner pin rides
        # the same event.
        bridge_service.close()
        line = _wait_marker("DFNB-DISCONNECTED", 30)
        result(line is not None,
               "closing the bridge showed the spawned app's disconnected "
               "state (DFNB-DISCONNECTED)"
               if line is not None else
               "the spawned application never showed its disconnected "
               "state (no DFNB-DISCONNECTED); tail:\n{}".format(_tail()))
        banner_deadline = time.monotonic() + 15
        while not form._disconnected_banner.isVisible() \
                and time.monotonic() < banner_deadline:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        result(form._disconnected_banner.isVisible()
               and "disconnected" in form.status_label.text().lower(),
               "this suite's own form shows the disconnected banner and "
               "status after the bridge closed")

        # -- round 9: the argv handoff. The drop filter launches the
        #    application with the dropped notebook as argv[2];
        #    notebook_app opens it AFTER the form shows (the DFNB-APP-UP
        #    print order stays) and reports DFNB-APP-OPENED with the file
        #    it opened, or DFNB-APP-OPEN-FAIL with the reason. The bridge
        #    is already closed here (the disconnect pin above) -- the
        #    open path needs no bridge, and the children terminate in
        #    their own finally. The child's env drops the autorun hooks:
        #    only the boot + open markers run.
        argv_env = dict(child_env)
        argv_env.pop("DFNB_AUTORUN", None)
        argv_env.pop("DFNB_AUTOSTOP_AFTER", None)

        def _spawn_argv_child(path):
            child = subprocess.Popen(
                [sys.executable, app_path, bridge_service.pipe_name, path],
                cwd=_ROOT, env=argv_env, creationflags=_NO_WINDOW,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace")
            out_lines = []
            err_lines = []
            out_reader = threading.Thread(target=_reader,
                                          args=(child.stdout, out_lines),
                                          daemon=True)
            err_reader = threading.Thread(target=_reader,
                                          args=(child.stderr, err_lines),
                                          daemon=True)
            out_reader.start()
            err_reader.start()
            return child, out_lines, err_lines, out_reader, err_reader

        def _end_argv_child(child, out_reader, err_reader):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except Exception:
                    child.kill()
                    try:
                        child.wait(timeout=5)
                    except Exception:
                        pass
            out_reader.join(timeout=3)
            err_reader.join(timeout=3)

        def _argv_tail(out_lines, err_lines):
            return ("\n".join(out_lines[-12:]) + "\n-- stderr --\n"
                    + "\n".join(err_lines[-12:]))

        # the valid handoff: a staged two-cell notebook as argv[2]
        argv_doc = installed_core.NotebookDocument.new_notebook()
        argv_doc.set_cell_source(0, "1 + 1")
        argv_doc.add_cell("markdown", source="# dropped via argv")
        argv_path = os.path.join(temp_dir, "argv_open.ipynb")
        argv_doc.save(argv_path)
        argv_child, argv_out, argv_err, argv_ro, argv_re = \
            _spawn_argv_child(argv_path)
        try:
            line = _wait_marker("DFNB-APP-OPENED", 90, sink=argv_out)
            opened_line_ok = line is not None and os.path.normcase(
                os.path.normpath(line[len("DFNB-APP-OPENED "):])) \
                == os.path.normcase(os.path.normpath(argv_path))
            result(opened_line_ok,
                   "the argv handoff opened the staged notebook in the "
                   "spawned app ({!r})".format(line)
                   if opened_line_ok else
                   "the argv child never reported DFNB-APP-OPENED with the "
                   "staged path; tail:\n{}".format(
                       _argv_tail(argv_out, argv_err)))
            up_positions = [i for i, l in enumerate(argv_out)
                            if l.startswith("DFNB-APP-UP")]
            opened_positions = [i for i, l in enumerate(argv_out)
                                if l.startswith("DFNB-APP-OPENED ")]
            result(bool(up_positions and opened_positions
                        and up_positions[0] < opened_positions[0]),
                   "DFNB-APP-UP stayed ahead of DFNB-APP-OPENED (the open "
                   "runs after the form shows)")
            result(not any(l.startswith("DFNB-APP-OPEN-FAIL")
                           for l in argv_out),
                   "the valid handoff printed no OPEN-FAIL marker")
        finally:
            _end_argv_child(argv_child, argv_ro, argv_re)

        # the corrupt handoff: the file cannot be read -- the child
        # reports OPEN-FAIL (under DFNB_SMOKE the failure box is the
        # marker path, never a modal) and never reports OPENED.
        argv_bad = os.path.join(temp_dir, "argv_corrupt.ipynb")
        with open(argv_bad, "w", encoding="utf-8") as handle:
            handle.write("{definitely not json")
        argv_child2, argv_out2, argv_err2, argv_ro2, argv_re2 = \
            _spawn_argv_child(argv_bad)
        try:
            line = _wait_marker("DFNB-APP-OPEN-FAIL", 90, sink=argv_out2)
            fail_line_ok = line is not None \
                and line.startswith("DFNB-APP-OPEN-FAIL ")
            result(fail_line_ok,
                   "the corrupt argv handoff reported DFNB-APP-OPEN-FAIL "
                   "({!r})".format(line)
                   if fail_line_ok else
                   "the corrupt argv child never reported OPEN-FAIL; "
                   "tail:\n{}".format(_argv_tail(argv_out2, argv_err2)))
            result(not any(l.startswith("DFNB-APP-OPENED")
                           for l in argv_out2),
                   "the corrupt handoff printed no OPENED marker")
        finally:
            _end_argv_child(argv_child2, argv_ro2, argv_re2)
    finally:
        if app_child.poll() is None:
            app_child.terminate()
            try:
                app_child.wait(timeout=5)
            except Exception:
                app_child.kill()
                try:
                    app_child.wait(timeout=5)
                except Exception:
                    pass
        reader_out.join(timeout=3)
        reader_err.join(timeout=3)
    result(not spawn_error, "the child's output readers saw no errors"
           if not spawn_error else
           "the child's output readers failed: {!r}".format(spawn_error))

    # ------------------------------------------------------------------
    print("\n=== (9) round 10: the shipped example notebook, executed ===",
          flush=True)

    # The gate that keeps the shipped example honest: the REPO's
    # examples/spheres_example.ipynb (a repo artifact, NOT an installed
    # plugin file -- _ROOT is the project root the sha256 pins already
    # read from) is loaded through the shipped reader and the form's real
    # open path, then every code cell runs through the REAL in-process
    # kernel over the real bridge handlers -- exactly the render
    # harness's construction (verification/_render_notebook.py), so a
    # dfnb call that drifts from the shipped surface fails HERE.
    example_path = os.path.join(_ROOT, "examples", "spheres_example.ipynb")
    result(os.path.isfile(example_path),
           "the shipped example notebook exists: examples/"
           "spheres_example.ipynb")
    # A SECOND service with an EXPLICIT pipe name: the pid-suffixed
    # default is still taken by the section-3 service this form must not
    # steal requests from.
    example_service = installed_bridge.NotebookBridgeService(
        dict(installed_lib.BRIDGE_HANDLERS),
        pipe_name="dfnb_bridge_example_tier3",
        discard_hook=installed_lib.discard_bridge_object)
    result(example_service.is_listening,
           "the example section's bridge service listens on {}".format(
               example_service.pipe_name))
    example_form = None
    try:
        example_form = installed_form.DragonflyNotebookForm(
            pipe_name=example_service.pipe_name)
        example_form.show()
        app.processEvents()
        result(example_form.client.is_connected(),
               "the example form's bridge client connected")
        # the real open path, _open_notebook_at's tail chain: the shipped
        # reader builds the document, _load_notebook_document swaps it in
        # (document + file_path + _rebuild_cells) -- the cells are never
        # re-typed here
        example_document = installed_core.NotebookDocument.from_file(
            example_path)
        example_form._load_notebook_document(example_document, example_path)
        app.processEvents()
        result(example_form.document is example_document
               and example_form.file_path == example_path,
               "the open path assigned the document and the file path")
        result(example_form.document.cell_count() == 11,
               "the example carries 11 cells (1 intro markdown + 5 "
               "markdown/code pairs)")
        result(all(example_form.document.get_cell(one)["cell_type"]
                   == ("code" if one >= 2 and one % 2 == 0 else "markdown")
                   for one in range(11)),
               "the structure is intro markdown then five (markdown, "
               "code) pairs")

        def _wait_run_done(ran_index, timeout_s=300.0):
            # the render harness's polling (verification/_render_notebook.py):
            # the runs are asynchronous by design, so the suite pumps the
            # event loop until the form's run finishes; 300 s is generous
            # for a notebook that is seconds of work
            deadline = time.monotonic() + timeout_s
            while example_form._running:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "example cell {} did not finish in {} s".format(
                            ran_index, timeout_s))
                app.processEvents()
                time.sleep(0.02)
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        code_indexes = [
            one for one in range(example_form.document.cell_count())
            if example_form.document.get_cell(one)["cell_type"] == "code"]
        result(code_indexes == [2, 4, 6, 8, 10],
               "the five code cells sit at indexes {} (the pairs' tails)"
               .format(code_indexes))
        result(len(code_indexes) == 5,
               "exactly five code cells to run")
        for ran_index in code_indexes:
            if not example_form._run_cell_at(ran_index):
                raise RuntimeError(
                    "example cell {} refused to start".format(ran_index))
            _wait_run_done(ran_index)
        result(all(example_form.document.get_cell(one)["execution_count"]
                   is not None for one in code_indexes),
               "every code cell recorded a kernel execution count")
        error_outputs = [
            output for one in code_indexes
            for output in (example_form.document.get_cell(one)["outputs"])
            if output.get("output_type") == "error"]
        result(not error_outputs,
               "all 5 code cells ran with ZERO error outputs ({})"
               .format("; ".join("{}: {}".format(o.get("ename"),
                                                 o.get("evalue"))
                                 for o in error_outputs)))

        # cell 4 (index 8) printed the marker and separated-sphere counts;
        # the pin reads the notebook's OWN stream output. With the round-13
        # seeded layout (radius-aware generator, overlaps capped at 2.0 px)
        # the seed cores stay one per sphere, so the numbers are
        # DETERMINISTIC 40/40 with seed 42 on the embedded environment's
        # scipy 1.9.3 + skimage 0.19.3 -- exact equality pinned; the printed
        # counts are the evidence (the cap bounds the necks, it does not
        # by itself guarantee distinct cores at other seeds).
        watershed_text = "".join(
            output.get("text", "")
            for output in example_form.document.get_cell(8)["outputs"]
            if output.get("output_type") == "stream")
        separated_match = re.search(r"separated spheres:\s*(\d+)",
                                    watershed_text)
        result(separated_match is not None,
               "cell 4 printed the separated-sphere count ({!r})".format(
                   watershed_text.strip()))
        if separated_match is not None:
            separated = int(separated_match.group(1))
            result(separated == 40,
                   "the marker-controlled watershed separated {} spheres "
                   "-- exactly the 40 painted (the seeded layout keeps "
                   "the seed cores one per sphere; this printed count is "
                   "the proof)".format(separated))
        markers_match = re.search(r"marker components:\s*(\d+)",
                                  watershed_text)
        result(markers_match is not None,
               "cell 4 printed the marker-component count ({!r})".format(
                   watershed_text.strip()))
        if markers_match is not None:
            markers_count = int(markers_match.group(1))
            result(markers_count == 40,
                   "the seed cores labelled {} marker components -- "
                   "exactly one per sphere (the seeded layout keeps them "
                   "distinct; this printed count is the proof, not a "
                   "guarantee of the overlap cap)".format(markers_count))
        recovery_match = re.search(
            r"recovery min/mean:\s*([\d.]+)/([\d.]+)", watershed_text)
        result(recovery_match is not None,
               "cell 4 printed the ground-truth recovery line ({!r})".format(
                   watershed_text.strip()))
        if recovery_match is not None:
            recovery_min, recovery_mean = (
                float(part) for part in recovery_match.groups())
            result(recovery_min >= 0.85 and recovery_mean >= 0.90,
                   "every label recovers its sphere (min {:.3f} >= 0.85, "
                   "mean {:.3f} >= 0.90 -- the round-13 accuracy check: "
                   "40/40 counts alone are not accuracy)".format(
                       recovery_min, recovery_mean))
        precision_match = re.search(
            r"precision min/mean:\s*([\d.]+)/([\d.]+)", watershed_text)
        result(precision_match is not None,
               "cell 4 printed the precision line ({!r})".format(
                   watershed_text.strip()))
        if precision_match is not None:
            precision_min, precision_mean = (
                float(part) for part in precision_match.groups())
            result(precision_min >= 0.93 and precision_mean >= 0.98,
                   "the labels stay on their own spheres (min {:.3f} >= "
                   "0.93, mean {:.3f} >= 0.98 -- banded with headroom "
                   "under the seeded 0.953/0.997)".format(
                       precision_min, precision_mean))

        # the round trip, read back through the bridge like any cell's
        # dfnb call would: the three published objects exist ...
        channel_titles = [entry["title"] for entry in
                          example_form.client.list_objects("Channel")]
        result("Random spheres" in channel_titles,
               "the published volume is in the session (Channel)")
        roi_titles = [entry["title"] for entry in
                      example_form.client.list_objects("ROI")]
        result("Spheres mask" in roi_titles,
               "the published mask is in the session (ROI)")
        multiroi_titles = [entry["title"] for entry in
                           example_form.client.list_objects("MultiROI")]
        result("Spheres segmentation" in multiroi_titles,
               "the published segmentation is in the session (MultiROI)")
        # ... and the exported properties are EXACTLY the three names --
        # an exact-set assertion on a fresh MultiROI also proves add mode
        # (nothing preceded them and the default write appended all three)
        properties = example_form.client.list_measured_properties(
            "Spheres segmentation")
        result(properties == ["nearest_neighbour_dist",
                              "mean_5_nearest_dist",
                              "neighbour_ratio"],
               "the exported properties are exactly the three example "
               "columns in slot order (got {!r})".format(properties))
        # the notebook's own final print shows the same round trip
        measure_text = "".join(
            output.get("text", "")
            for output in example_form.document.get_cell(10)["outputs"]
            if output.get("output_type") == "stream")
        result("nearest_neighbour_dist" in measure_text
               and "mean_5_nearest_dist" in measure_text
               and "neighbour_ratio" in measure_text,
               "cell 5's printed property list shows the round trip")
    finally:
        # every ORS object the example run created is resolved by title
        # and tracked into the suite's reversed teardown -- the section
        # must leave nothing behind (the same convention as the fixtures
        # above)
        try:
            for managed_class, made_title in (
                    (installed_lib.Channel, "Random spheres"),
                    (installed_lib.ROI, "Spheres mask"),
                    (installed_lib.MultiROI, "Spheres segmentation")):
                for instance in list(managed_class.getAllInstances()):
                    try:
                        if instance.getTitle() == made_title:
                            track(instance)
                    except Exception:
                        pass  # a dying instance is no teardown concern
        except Exception:
            pass  # best-effort: the outer teardown still runs
        if example_form is not None:
            try:
                example_form.kernel.stop()
            except Exception:
                pass
            try:
                example_form.client.close()
            except Exception:
                pass
        try:
            example_service.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    print("\n=== (10) rounds 16+17: the custom python environments ===",
          flush=True)

    # The feature runs against a TEMP environments root: DFNB_ENVS_ROOT is
    # the seam notebook_envs.envs_root() honors, so no venv is created and
    # no pip install writes anywhere outside the suite's temp tree. It is
    # set BEFORE the form's construction (the form captures envs_root() at
    # its ctor; every list/validate/create below must resolve the same
    # root). NOT covered here, on purpose: the modal exec() runs of the
    # streaming dialogs (the _EnvProcessDialog machinery is driven
    # directly through start + event-loop pump, the same headless
    # convention as the picker dialogs of section 4), the live
    # pip-install button handler (its spec dialog is modal too; the
    # command it would run is the one pinned below) and the delete
    # picker + confirm (their suppression seam is pinned below; the
    # removal itself is driven directly).
    envs_root_dir = tempfile.mkdtemp(prefix="dfnb_t3_envs_")
    os.environ["DFNB_ENVS_ROOT"] = envs_root_dir
    result(installed_envs.envs_root() == envs_root_dir,
           "DFNB_ENVS_ROOT points notebook_envs at the suite's temp root")

    # A third service with an explicit pipe name (the pid-suffixed default
    # is still held by the section-3 service, and the example section owns
    # its own): this form's client connects to it exactly the way each
    # section's form does.
    env_service = installed_bridge.NotebookBridgeService(
        dict(installed_lib.BRIDGE_HANDLERS),
        pipe_name="dfnb_bridge_envs_tier3",
        discard_hook=installed_lib.discard_bridge_object)
    result(env_service.is_listening,
           "the environments section's bridge service listens on {}"
           .format(env_service.pipe_name))

    def _wait_env_dialog(dialog, what, timeout_s=180.0):
        # The _EnvProcessDialog contract the suite drives: start() spawns
        # the no-window child, the reader thread forwards the lines and
        # the exit code through queued signals, and pumping the event
        # loop delivers them (exec() is never called here).
        deadline = time.monotonic() + timeout_s
        while dialog.exit_code is None:
            if time.monotonic() > deadline:
                result(False, "{} did not finish within {} s (transcript "
                              "tail: {!r})".format(
                                  what, timeout_s,
                                  "\n".join(dialog.transcript[-8:])))
            app.processEvents()
            time.sleep(0.02)
        for _ in range(5):
            app.processEvents()
            time.sleep(0.02)

    env_form = None
    try:
        env_form = installed_form.DragonflyNotebookForm(
            pipe_name=env_service.pipe_name)
        env_form.show()
        app.processEvents()
        result(env_form.client.is_connected(),
               "the environments form's bridge client connected")

        # A fresh form on an EMPTY environments root: the combo carries
        # exactly the embedded entry and the two administrative entries
        # (no separator when no environment exists yet), the embedded
        # Python is selected by default, and the pip button is disabled
        # with its reason.
        env_combo = env_form._env_combo
        fresh_items = [env_combo.itemText(i)
                       for i in range(env_combo.count())]
        result(fresh_items == [installed_envs.EMBEDDED_LABEL,
                               installed_envs.NEW_ENV_LABEL,
                               installed_envs.DELETE_ENV_LABEL],
               "the fresh combo carries the embedded entry and the two "
               "administrative entries only: {}".format(fresh_items))
        result(env_combo.currentData() is None
               and env_form._selected_environment is None,
               "the embedded Python is the default selection")
        result(env_form._pip_button.isEnabled() is False
               and "not pip-installable" in env_form._pip_button.toolTip(),
               "the pip button is disabled on the embedded Python with "
               "the reason in its tooltip: {!r}".format(
                   env_form._pip_button.toolTip()))
        result(env_form._env_label.text() ==
                "env: {}".format(installed_envs.EMBEDDED_LABEL),
               "the status strip names the environment: {!r}".format(
                   env_form._env_label.text()))
        result(env_combo.isEnabled(),
               "the combo is enabled while idle")

        # The '+ New Environment' entry under the suite seam: the
        # programmatic pick reaches the handler, the modal creation flow is
        # suppressed (running_in_test), the combo reverts to the current
        # selection, and the suppression is a LOG record.
        sentinel_index = env_combo.findData(installed_envs.NEW_ENV_SENTINEL)
        suppress_log = _RecordingLogHandler()
        form_logger.addHandler(suppress_log)
        try:
            env_combo.setCurrentIndex(sentinel_index)
        finally:
            form_logger.removeHandler(suppress_log)
        result(env_combo.currentIndex() == 0,
               "the suppressed creation pick reverted the combo to the "
               "embedded entry")
        result(len(suppress_log.records) == 1
               and "suppressed under test" in suppress_log.records[0],
               "the suppression logged exactly one record ({} entries)"
               .format(len(suppress_log.records)))

        # The '- Delete Environment' entry under the same seam (round
        # 17): the pick reverts and logs; the picker dialog itself never
        # opens headless.
        delete_index = env_combo.findData(
            installed_envs.DELETE_ENV_SENTINEL)
        delete_log = _RecordingLogHandler()
        form_logger.addHandler(delete_log)
        try:
            env_combo.setCurrentIndex(delete_index)
        finally:
            form_logger.removeHandler(delete_log)
        result(env_combo.currentIndex() == 0,
               "the suppressed delete pick reverted the combo to the "
               "embedded entry")
        result(len(delete_log.records) == 1
               and "delete-environment dialog is suppressed under test"
               in delete_log.records[0],
               "the delete suppression logged exactly one record ({} "
               "entries)".format(len(delete_log.records)))

        # The New Environment dialog, headless: its live validation mirrors
        # notebook_envs.validate_name (the reason under the field, OK
        # disabled until valid).
        new_dialog = installed_form.NewEnvironmentDialog(envs_root_dir,
                                                          env_form)
        for bad_text in ("", "   ", "two words", "-leading", "../escape"):
            new_dialog.name_edit.setText(bad_text)
            result(new_dialog.ok_button.isEnabled() is False
                   and new_dialog.message_label.text() != "",
                   "the name {!r} is refused with a reason".format(
                       bad_text))
        new_dialog.name_edit.setText("t3_env")
        result(new_dialog.ok_button.isEnabled()
               and new_dialog.environment_name() == "t3_env",
               "the valid name 't3_env' enables OK and round-trips")
        result(installed_envs.validate_name("t3_env", envs_root_dir)
               == "t3_env",
               "validate_name accepts and returns the same name")

        # Round 18, the delete picker's styled dialog (the plain
        # QInputDialog.getItem it replaced never took the plugin's dark
        # dialog stylesheet): constructed headless, never exec()'d -- the
        # object name puts it under the #env_dialog rules, the list
        # carries one row per environment, the first is pre-selected, OK
        # follows the selection, and the picked name round-trips.
        delete_dialog = installed_form.DeleteEnvironmentDialog(
            ["t3_env", "t3_other"], env_form)
        result(delete_dialog.objectName() == "env_dialog"
               and delete_dialog.styleSheet()
               == installed_form._DIALOG_STYLESHEET,
               "the delete picker carries the plugin's dialog stylesheet "
               "under the #env_dialog object name")
        result("QDialog#env_dialog QListWidget#env_delete_list"
               in installed_form._DIALOG_STYLESHEET,
               "the stylesheet styles the delete picker's list")
        result(delete_dialog.windowTitle() == "Dragonfly Notebook"
               and delete_dialog.env_list.count() == 2
               and delete_dialog.env_list.item(0).text() == "t3_env"
               and delete_dialog.env_list.item(1).text() == "t3_other",
               "the delete picker lists the environments under the "
               "plugin's window title")
        result(delete_dialog.env_list.currentRow() == 0
               and delete_dialog.ok_button.isEnabled()
               and delete_dialog.selected_environment() == "t3_env",
               "the delete picker pre-selects the first environment and "
               "round-trips it")
        delete_dialog.env_list.setCurrentRow(1)
        result(delete_dialog.selected_environment() == "t3_other"
               and delete_dialog.ok_button.isEnabled(),
               "picking the second environment moves the selection and "
               "keeps OK enabled")
        delete_dialog.env_list.setCurrentRow(-1)
        result(delete_dialog.ok_button.isEnabled() is False
               and delete_dialog.selected_environment() == "",
               "no selection disables OK and yields no name")
        delete_dialog.env_list.setCurrentRow(0)

        # The pip command normalizer (the round-17 button's validation
        # front AND the argv builder's shared tail).
        result(installed_envs.pip_command_words(
                   'pip install "scipy>=1.10"  numpy') ==
               ["install", "scipy>=1.10", "numpy"],
               "pip_command_words strips the launcher and splits on "
               "whitespace and quotes")
        result(installed_envs.pip_command_words("pip update pandas") ==
               ["install", "--upgrade", "pandas"],
               "pip_command_words runs update as install --upgrade")
        result(installed_envs.pip_command_words("pip uninstall numpy") ==
               ["uninstall", "numpy", "-y"],
               "pip_command_words auto-confirms an uninstall")
        try:
            installed_envs.pip_command_words("pip")
            refused = False
        except ValueError:
            refused = True
        result(refused,
               "pip_command_words refuses a bare launcher (nothing to "
               "run)")

        # The REAL venv creation, streamed through the same builder the
        # '+ New Environment' flow uses (``_creation_run_dialog``: the
        # title, the phase notes, the venv argv, the exit-code note;
        # driven directly with start + pump, never the modal exec). This
        # is the real `python -m venv` on the embedded interpreter
        # through the no-window bootstrap (~40 s measured in the round-16
        # probes).
        venv_dir = installed_envs.env_dir(envs_root_dir, "t3_env")
        create_dialog = env_form._creation_run_dialog("t3_env", venv_dir)
        _wait_env_dialog(create_dialog, "the venv creation", 180.0)
        # The phase notes ARE the transcript (``python -m venv`` is
        # silent, so round 16 added exactly this visibility): the two
        # intro notes open the run and the exit-code
        # note ends it. The venv's files are the real success proof,
        # pinned next.
        create_transcript = create_dialog.transcript
        result(create_dialog.exit_code == 0
               and create_transcript[0] == "Creating the virtual "
               "environment 't3_env' (python -m venv)."
               and "prints nothing on its own" in create_transcript[1]
               and create_transcript[-1] == "The environment was created "
               "(exit code 0).",
               "the creation streamed its phases ({} lines: {!r})"
               .format(len(create_transcript), create_transcript))
        result(os.path.isfile(os.path.join(venv_dir, "pyvenv.cfg"))
               and os.path.isfile(installed_envs.venv_python(venv_dir)),
               "the venv carries pyvenv.cfg and its own python.exe")
        result(installed_envs.list_environments(envs_root_dir)
               == ["t3_env"],
               "discovery offers exactly the created environment")
        result(create_dialog.cancel_button.isHidden()
               and not create_dialog.close_button.isHidden(),
               "the run's end flipped the dialog's Cancel to Close")

        # The hand-built wheel (the round-16 probe recipe: a pure-python
        # zip wheel with RECORD hashes) installs OFFLINE through the
        # venv's OWN pip; the builder is shared by the base-install pin
        # here and the pip-button pin below (two different packages, so
        # each install is its own event).
        def _build_t3_wheel(path, package, which):
            src = "WHERE = __file__\nWHICH = {!r}\n".format(which).encode()
            dist_info = package + "-1.0.dist-info"
            record_entries = []

            def _record(name, data):
                digest = base64.urlsafe_b64encode(
                    hashlib.sha256(data).digest()).rstrip(b"=").decode()
                record_entries.append(
                    "{},sha256={},{}".format(name, digest, len(data)))

            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr(package + "/__init__.py", src)
                _record(package + "/__init__.py", src)
                meta = ("Metadata-Version: 2.1\nName: {}\n"
                        "Version: 1.0\n".format(
                            package.replace("_", "-"))).encode()
                zf.writestr(dist_info + "/METADATA", meta)
                _record(dist_info + "/METADATA", meta)
                wheel_txt = ("Wheel-Version: 1.0\nGenerator: dfnb-tier3\n"
                             "Root-Is-Purelib: true\n"
                             "Tag: py3-none-any\n").encode()
                zf.writestr(dist_info + "/WHEEL", wheel_txt)
                _record(dist_info + "/WHEEL", wheel_txt)
                record = ("\n".join(record_entries) + "\n"
                          + dist_info + "/RECORD,,\n").encode()
                zf.writestr(dist_info + "/RECORD", record)
            return path

        # Round 17's second creation dialog: the base-package install
        # (numpy and pandas in the real flow), streamed the same way --
        # driven offline with a wheel in place of the contract so the
        # phase notes pin without the network. The real flow runs this
        # between the creation and the refresh, so the order here is
        # the order the GUI runs.
        base_wheel = _build_t3_wheel(
            os.path.join(temp_dir, "dfnb_t3_base-1.0-py3-none-any.whl"),
            "dfnb_t3_base", "t3-base-wheel")
        base_dialog = env_form._base_install_run_dialog(
            "t3_env", venv_dir, ["--no-index", base_wheel])
        _wait_env_dialog(base_dialog, "the base-package install", 180.0)
        result(base_dialog.exit_code == 0
               and base_dialog.transcript[0] ==
               "Installing the base packages the notebook needs "
               "(--no-index, {}): numpy arrays carry the volumes, "
               "pandas DataFrames carry the measured properties."
               .format(base_wheel)
               and "PyPI" in base_dialog.transcript[1]
               and base_dialog.transcript[-1] ==
               "The base packages were installed (exit code 0).",
               "the base install streamed its phases ({} lines, tail "
               "{!r})".format(len(base_dialog.transcript),
                              base_dialog.transcript[-3:]))
        result(os.path.isfile(os.path.join(
                   installed_envs.venv_site_packages(venv_dir),
                   "dfnb_t3_base", "__init__.py")),
               "the base wheel's package landed in the venv's "
               "site-packages")

        # The form's discovery picks the new environment up, and the USER
        # pick (a real setCurrentIndex, the signal path) switches the
        # notebook to it: the metadata, the combo, the status label and
        # the pip rule all follow (the kernel is not started by the pick
        # -- the first cell run starts it, as a subprocess on the venv's
        # own interpreter).
        env_form._refresh_env_list()
        refreshed_items = [env_combo.itemText(i)
                           for i in range(env_combo.count())]
        result(env_combo.count() == 5
               and refreshed_items == [installed_envs.EMBEDDED_LABEL,
                                       "t3_env", "",
                                       installed_envs.NEW_ENV_LABEL,
                                       installed_envs.DELETE_ENV_LABEL],
               "the refreshed combo lists the embedded entry, the "
               "environment, a separator and the two administrative "
               "entries: {}".format(refreshed_items))
        env_index = env_combo.findData("t3_env")
        result(env_index == 1, "the environment sits at index 1")
        env_combo.setCurrentIndex(env_index)
        result(env_form._selected_environment == "t3_env"
               and env_form.document.metadata["dfnb_environment"]
               == "t3_env",
               "the pick switched the selection and the notebook's "
               "metadata")
        result(env_form._env_label.text() == "env: t3_env",
               "the status strip names the selected environment")
        result(env_form._pip_button.isEnabled()
               and "run a pip command in the environment 't3_env'"
               in env_form._pip_button.toolTip(),
               "the pip button enabled on the custom, idle environment "
               "with its tooltip: {!r}".format(
                   env_form._pip_button.toolTip()))

        # The pip-button pin's wheel: the shared builder's second
        # product (the exact command the pip button runs, driven
        # through the same streaming dialog).
        wheel_path = _build_t3_wheel(
            os.path.join(temp_dir, "dfnb_t3_pkg-1.0-py3-none-any.whl"),
            "dfnb_t3_pkg", "t3-wheel")

        # The pip run through the same builder the button uses
        # (``_pip_run_dialog``), driven with the FULL command text the
        # round-17 button takes: the intro note naming the command as
        # typed opens the transcript, pip's own lines follow, the
        # exit-code note ends it.
        the_command = "pip install --no-index " + wheel_path
        pip_dialog = env_form._pip_run_dialog(
            "t3_env", venv_dir, the_command)
        _wait_env_dialog(pip_dialog, "the offline pip install", 180.0)
        result(pip_dialog.exit_code == 0
               and pip_dialog.transcript[0] ==
               "Running the environment's own pip: {}.".format(
                   the_command)
               and pip_dialog.transcript[-1] ==
               "The pip command finished (exit code 0).",
               "the offline install streamed its phases ({} lines, tail "
               "{!r})".format(len(pip_dialog.transcript),
                              pip_dialog.transcript[-3:]))
        result(os.path.isfile(
                   os.path.join(
                       installed_envs.venv_site_packages(venv_dir),
                       "dfnb_t3_pkg", "__init__.py")),
               "the wheel's package landed in the venv's site-packages")

        # The crown pin: a real cell runs through the form's kernel ON the
        # environment (round 16's subprocess model: the GUI and this form
        # stay on the embedded Python; the kernel spawns the environment
        # runner as a subprocess on the VENV's own python.exe, and the
        # cell executes there, importing the pip-installed package from
        # the venv's site-packages) -- and the started kernel holds the
        # in-use lock, so the pip button disables.

        def _wait_env_run(timeout_s=180.0):
            deadline = time.monotonic() + timeout_s
            while env_form._running:
                if time.monotonic() > deadline:
                    result(False, "the environment cell did not finish "
                                  "within {} s".format(timeout_s))
                app.processEvents()
                time.sleep(0.02)
            for _ in range(5):
                app.processEvents()
                time.sleep(0.02)

        env_form._cell_widgets[0]["editor"].setPlainText(
            'import dfnb_t3_pkg\n'
            'print("T3ENV", dfnb_t3_pkg.WHICH, dfnb_t3_pkg.WHERE)')
        result(env_form._run_cell_at(0) is True,
               "the environment cell started")
        _wait_env_run()
        env_outputs = env_form.document.get_cell(0)["outputs"]
        env_stream = "".join(
            output.get("text", "") for output in env_outputs
            if output.get("output_type") == "stream")
        result(env_stream.startswith("T3ENV t3-wheel ")
               and venv_dir in env_stream,
               "the cell imported the wheel's package from the venv "
               "(stream {!r})".format(env_stream))
        result(not [output for output in env_outputs
                    if output.get("output_type") == "error"],
               "the environment cell ran without errors")
        result(env_form.kernel.is_started
               and env_form.kernel.active_environment == venv_dir
               and installed_envs.is_environment_in_use(venv_dir) is True,
               "the started kernel runs on the environment and holds its "
               "in-use lock")
        # Round 18: the running kernel no longer DISABLES the pip
        # button (a cell run leaves the kernel started, so the old rule
        # locked the button forever after the first cell) -- the hold
        # surfaces as the tooltip's file-in-use risk note instead.
        result(env_form._pip_button.isEnabled()
               and "file-in-use" in env_form._pip_button.toolTip(),
               "the pip button STAYS ENABLED while the kernel runs on "
               "the environment, the risk in its tooltip: {!r}".format(
                   env_form._pip_button.toolTip()))
        result(env_combo.isEnabled(),
               "the combo stays enabled while only the kernel runs")

        # Round 18, the restart pin: a RESTARTED kernel is still a
        # running kernel (the in-use lock stays held by design, the
        # namespace wipe is in place), and the controls re-render at
        # restart_finished -- the button must be ENABLED there too.
        restart_results = []
        env_form.kernel.restart_finished.connect(
            lambda ok, message: restart_results.append((ok, message)))
        env_form.kernel.restart()
        restart_deadline = time.monotonic() + 60.0
        while not restart_results:
            if time.monotonic() > restart_deadline:
                result(False, "the kernel restart never finished within "
                              "60 s")
                break
            app.processEvents()
            time.sleep(0.02)
        result(bool(restart_results) and restart_results[0][0] is True,
               "the kernel restart completed (result {!r})"
               .format(restart_results))
        result(env_form.kernel.is_started
               and env_form.kernel.active_environment == venv_dir
               and installed_envs.is_environment_in_use(venv_dir) is True,
               "the restarted kernel still runs on the environment and "
               "holds its in-use lock")
        result(env_form._pip_button.isEnabled()
               and "file-in-use" in env_form._pip_button.toolTip(),
               "the pip button is enabled AFTER the restart finished "
               "with the risk note re-rendered: {!r}".format(
                   env_form._pip_button.toolTip()))

        # The dfnb relay across the environment boundary (round 16's
        # subprocess model): the cell's dfnb call executes in the venv's
        # child process, crosses to this app process as a runner request
        # line, rides the kernel's relay thread into the REAL bridge
        # service behind this form, and the answer returns as the cell's
        # stream output. ORS never imports in the child; the answer here
        # is the client's unwrapped list of live-object dicts (the
        # handler's {'objects': [...]} payload is the wire shape, the
        # client method returns the list itself -- possibly empty, the
        # shape is the pin, not the contents).
        env_form._cell_widgets[0]["editor"].setPlainText(
            'objects = dfnb.list_objects()\n'
            'print("T3RELAY", isinstance(objects, list),\n'
            '      all(isinstance(item, dict) and\n'
            '          {"guid", "title", "class_name"} <= set(item)\n'
            '          for item in objects))')
        result(env_form._run_cell_at(0) is True,
               "the relay cell started")
        _wait_env_run()
        relay_outputs = env_form.document.get_cell(0)["outputs"]
        relay_stream = "".join(
            output.get("text", "") for output in relay_outputs
            if output.get("output_type") == "stream")
        result("T3RELAY True True" in relay_stream
               and not [output for output in relay_outputs
                        if output.get("output_type") == "error"],
               "the environment cell's dfnb call crossed child -> relay "
               "-> bridge and back (stream {!r})".format(relay_stream))

        # Round 17, the shell escape through the REAL environment child:
        # the '!python' reroute lands on the venv's own interpreter (the
        # child's sys.executable), the no-window grandchild writes into
        # the captured pipe, and the forwarder streams it back as the
        # cell's output -- the same captured-pipe path every
        # '!command' cell takes (pip cells are refused, round 17: pip
        # runs from the button).
        env_form._cell_widgets[0]["editor"].setPlainText(
            '!python -c "import sys; print(sys.executable)"')
        result(env_form._run_cell_at(0) is True,
               "the shell-escape cell started")
        _wait_env_run()
        bang_outputs = env_form.document.get_cell(0)["outputs"]
        bang_stream = "".join(
            output.get("text", "") for output in bang_outputs
            if output.get("output_type") == "stream")
        bang_line = bang_stream.strip()
        result(not [output for output in bang_outputs
                    if output.get("output_type") == "error"]
               and bang_line
               and os.path.normcase(bang_line) == os.path.normcase(
                   installed_envs.venv_python(venv_dir)),
               "the '!python -c' cell streamed the venv's own "
               "interpreter path through the environment child (stream "
               "{!r})".format(bang_stream))

        # Round 17, the runtime DLL delivery on a real venv child: the
        # kernel's start copied the SCAN-DISCOVERED misparked set into
        # the venv's Scripts (ensure_runtime_dlls -- not name-keyed,
        # round 17's follow-up: the import tables of the base tree's
        # extension modules decide, and on this machine that set heals
        # sqlite3 AND tkinter alike) and the runner armed that
        # directory for the dependent load (the two-piece fix: a
        # base-tree pyd's altered search never reaches the exe dir on
        # its own, probe-proven), so the stdlib extension family
        # imports inside the environment kernel -- in a bare venv on
        # this distribution sqlite3 and tkinter are DLL-load
        # ImportErrors.
        runtime_code = (
            'import bz2, ctypes, hashlib, lzma, select\n'
            'import sqlite3, ssl, unicodedata, zlib\n'
            'print("T3RT", sqlite3.connect(":memory:")\n'
            '      .execute("select 41 + 1").fetchone()[0],\n'
            '      ssl.OPENSSL_VERSION.split()[1],\n'
            '      ctypes.sizeof(ctypes.c_void_p))')
        if os.path.isfile(os.path.join(
                os.path.dirname(installed_envs.venv_python(venv_dir)),
                'tcl86t.dll')):
            runtime_code += ('\nimport tkinter\n'
                             'print("T3TK", tkinter.TkVersion)')
        env_form._cell_widgets[0]["editor"].setPlainText(runtime_code)
        result(env_form._run_cell_at(0) is True,
               "the runtime-DLL cell started")
        _wait_env_run()
        rt_outputs = env_form.document.get_cell(0)["outputs"]
        rt_stream = "".join(
            output.get("text", "") for output in rt_outputs
            if output.get("output_type") == "stream")
        result("T3RT 42" in rt_stream
               and ("T3TK" not in runtime_code
                    or "T3TK 8" in rt_stream)
               and not [output for output in rt_outputs
                        if output.get("output_type") == "error"],
               "the environment child imported the stdlib extension "
               "family and answered a sqlite query (stream "
               "{!r})".format(rt_stream))

        # Round 17's pip rule, pinned where the user meets it: pip does
        # NOT run from cells. A '%pip3 list' cell fails the runner's
        # parse with the explained magic error (every % magic is
        # unsupported in an environment kernel, pip included), and an
        # '!pip3 list' cell completes cleanly whose ONLY output is the
        # note naming the pip button -- no package table ever streams,
        # because nothing spawned.
        env_form._cell_widgets[0]["editor"].setPlainText('%pip3 list')
        result(env_form._run_cell_at(0) is True,
               "the %pip3 cell started")
        _wait_env_run()
        magic_outputs = env_form.document.get_cell(0)["outputs"]
        magic_errors = [
            output for output in magic_outputs
            if output.get("output_type") == "error"]
        result(len(magic_errors) == 1
               and "pip button" in magic_errors[0].get("evalue", "")
               and "magics" in magic_errors[0].get("evalue", ""),
               "the '%pip3 list' cell ended in the explained magic "
               "error pointing at the pip button (errors {!r})"
               .format(magic_errors))

        env_form._cell_widgets[0]["editor"].setPlainText('!pip3 list')
        result(env_form._run_cell_at(0) is True,
               "the !pip3 cell started")
        _wait_env_run()
        pip3_outputs = env_form.document.get_cell(0)["outputs"]
        pip3_stream = "".join(
            output.get("text", "") for output in pip3_outputs
            if output.get("output_type") == "stream")
        result("pip button" in pip3_stream
               and "dfnb-t3-base" not in pip3_stream
               and not [output for output in pip3_outputs
                        if output.get("output_type") == "error"],
               "the '!pip3 list' cell wrote the button note and spawned "
               "nothing (no package table: stream {!r})"
               .format(pip3_stream))

        # The switch back to the embedded Python while the kernel runs on
        # the environment: the confirm auto-accepts under the suite seam
        # (a LOG record), the kernel shuts down (tearing the venv child
        # down with it; the in-use lock frees), the metadata drops the
        # key, and the pip button falls back to its embedded reason.
        switch_log = _RecordingLogHandler()
        form_logger.addHandler(switch_log)
        try:
            env_combo.setCurrentIndex(0)
        finally:
            form_logger.removeHandler(switch_log)
        result(len(switch_log.records) == 1
               and "auto-accepted under test" in switch_log.records[0],
               "the switch confirm auto-accepted under test with one log "
               "record ({} entries)".format(len(switch_log.records)))
        deadline = time.monotonic() + 60.0
        while env_form.kernel.is_started:
            if time.monotonic() > deadline:
                result(False, "the kernel never shut down for the switch")
            app.processEvents()
            time.sleep(0.02)
        # The teardown frees the in-use lock AFTER its bounded child-exit
        # wait, which trails the is_started flip (the flag drops at the
        # teardown's first line): wait for the lock with its own bound
        # instead of sampling it once. A sample right after the flip
        # races the teardown's tail -- the round-16 one-time staged
        # flake of this pin was exactly that window, and round 17's
        # polling watcher (a 50 ms quit latency) made it wide enough
        # to catch again.
        lock_deadline = time.monotonic() + 10.0
        while installed_envs.is_environment_in_use(venv_dir):
            if time.monotonic() > lock_deadline:
                break
            app.processEvents()
            time.sleep(0.02)
        result(env_form.kernel.is_started is False
               and installed_envs.is_environment_in_use(venv_dir)
               is False,
               "the switch shut the kernel down and freed the lock")
        result(env_form._selected_environment is None
               and "dfnb_environment" not in env_form.document.metadata,
               "the switch back to embedded dropped the metadata key")
        result(env_form._pip_button.isEnabled() is False
               and "not pip-installable" in env_form._pip_button.toolTip(),
               "the pip button disabled again on the embedded Python")

        # The notebook remembers its environment: selecting it again and
        # saving round-trips the metadata, and the form's real open tail
        # (_load_notebook_document) restores the selection.
        env_combo.setCurrentIndex(env_combo.findData("t3_env"))
        result(env_form._selected_environment == "t3_env",
               "re-selecting the environment switched back to it")
        env_notebook_path = os.path.join(temp_dir, "env_roundtrip.ipynb")
        env_form.file_path = env_notebook_path
        result(env_form.on_save_notebook() is True
               and os.path.isfile(env_notebook_path),
               "the environment notebook saved")
        reloaded_env = installed_core.NotebookDocument.from_file(
            env_notebook_path)
        result(reloaded_env.metadata.get("dfnb_environment") == "t3_env",
               "the saved notebook carries dfnb_environment='t3_env'")
        env_form._load_notebook_document(
            installed_core.NotebookDocument.from_file(env_notebook_path),
            env_notebook_path)
        result(env_form._selected_environment == "t3_env"
               and env_form.document.metadata.get("dfnb_environment")
               == "t3_env"
               and env_combo.currentData() == "t3_env",
               "the open path restored the saved environment")

        # A saved name that no longer exists falls back to the embedded
        # Python (one LOG record and a status note; the notebook still
        # opens).
        ghost_doc = installed_core.NotebookDocument.from_file(
            env_notebook_path)
        ghost_doc.metadata["dfnb_environment"] = "ghost_env"
        ghost_notebook_path = os.path.join(temp_dir, "env_ghost.ipynb")
        ghost_doc.save(ghost_notebook_path)
        ghost_log = _RecordingLogHandler()
        form_logger.addHandler(ghost_log)
        try:
            env_form._load_notebook_document(
                installed_core.NotebookDocument.from_file(
                    ghost_notebook_path),
                ghost_notebook_path)
        finally:
            form_logger.removeHandler(ghost_log)
        result(env_form._selected_environment is None
               and env_combo.currentData() is None,
               "the missing environment fell back to the embedded Python")
        result(len(ghost_log.records) == 1
               and "no longer exists" in ghost_log.records[0],
               "the fallback logged exactly one record ({} entries)"
               .format(len(ghost_log.records)))
        result("no longer exists" in env_form.status_label.text(),
               "the status names the fallback: {!r}".format(
                   env_form.status_label.text()))

        # The streaming dialog's close refusal while a child runs (Esc or
        # the window's X must not orphan the reader thread), observed
        # through the finished signal with the dialog never shown: a
        # 3-second child, reject() refused mid-run, reject() accepted
        # after the end.
        sleep_dialog = installed_form._EnvProcessDialog(
            "t3 refusal pin", env_form)
        finished_box = {}
        sleep_dialog.finished.connect(
            lambda *_: finished_box.update(finished=True))
        sleep_dialog.start([sys.executable, "-c",
                            "import time; time.sleep(3)"])
        sleep_dialog.reject()
        result("finished" not in finished_box,
               "reject() is refused while the child runs (finished never "
               "emitted)")
        _wait_env_dialog(sleep_dialog, "the sleep child", 30.0)
        result(sleep_dialog.exit_code == 0,
               "the sleep child finished cleanly")
        sleep_dialog.reject()
        result(finished_box.get("finished") is True,
               "reject() closes the dialog once the run ended")

        # The pip failure path: an unresolvable specifier offline fails
        # with pip's own diagnostic in the transcript (the exit code is
        # what the form's failure box quotes).
        fail_dialog = installed_form._EnvProcessDialog(
            "t3 pip failure pin", env_form)
        fail_dialog.start(installed_envs.pip_install_command(
            venv_dir, ["--no-index", "dfnb-nope-pkg"]))
        _wait_env_dialog(fail_dialog, "the failing pip install", 120.0)
        result(fail_dialog.exit_code != 0
               and any("No matching distribution" in line
                       for line in fail_dialog.transcript),
               "the failing install surfaced pip's diagnostic (exit {})"
               .format(fail_dialog.exit_code))

        # Round 17, the delete flow on the real t3_env: re-select it
        # first (so the deletion also exercises the silent switch back
        # to the embedded Python). Round 18 reshapes the flow's teeth:
        # the refusal remains only for a hold this window cannot stop
        # (pinned on the SECOND venv, the direct lock standing in for
        # another notebook window's OS hold), and the delete with THIS
        # window's kernel running on t3_env -- the case the old refusal
        # made impossible after running any cell -- must stop the
        # kernel, free the lock, remove the tree and switch back.
        env_combo.setCurrentIndex(env_combo.findData("t3_env"))
        result(env_form._selected_environment == "t3_env",
               "re-selecting the environment switched back to it for "
               "the delete pins")

        # Round 17 review coverage, the UNSELECTED branch of the delete:
        # a second real venv exists while t3_env stays selected, and
        # deleting the OTHER environment must not touch the notebook's
        # selection or metadata (the silent switch back belongs to the
        # selected-env branch alone).
        other_venv = installed_envs.env_dir(envs_root_dir, "t3_other")
        other_creation = subprocess.run(
            installed_envs.venv_creation_command(other_venv),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=300,
            creationflags=installed_envs.CREATE_NO_WINDOW)
        result(other_creation.returncode == 0
               and os.path.isfile(installed_envs.venv_python(other_venv)),
               "the second real venv was created for the unselected "
               "delete (exit {})".format(other_creation.returncode))
        env_form._refresh_env_list()
        result(env_combo.findData("t3_other") != -1,
               "the refreshed combo lists the second environment")

        # Round 18, the refusal's narrowed subject: a hold this window
        # cannot stop (the direct lock on t3_other, standing in for
        # another notebook window's kernel) refuses the delete; the
        # kernel of THIS window is NOT the holder here, so the new
        # stop-first seam must not fire and the selection must stay.
        marker = installed_envs.acquire_in_use(other_venv)
        result(os.path.isfile(marker),
               "the in-use marker sits inside the environment")
        env_form._delete_environment("t3_other")
        result(os.path.isdir(other_venv)
               and env_form._selected_environment == "t3_env"
               and "in use" in env_form.status_label.text(),
               "the in-use delete refused and said whose hold it is "
               "(status {!r})".format(env_form.status_label.text()))
        result(installed_envs.release_in_use(other_venv) is True,
               "the suite released its hold on the environment")
        env_form._delete_environment("t3_other")
        result(not os.path.isdir(other_venv)
               and env_form._selected_environment == "t3_env"
               and env_combo.findData("t3_env") != -1
               and env_combo.currentData() == "t3_env"
               and "was deleted" in env_form.status_label.text(),
               "deleting an UNSELECTED environment removed its tree and "
               "left the notebook on t3_env (status {!r})".format(
                   env_form.status_label.text()))

        # Round 18, the delete with THIS window's kernel running on the
        # environment: a cell lazy-starts the kernel on t3_env (the
        # common state after running cells, the old permanent refusal's
        # victim), then the delete stops the kernel first (the queued
        # shutdown tears the venv child down and frees the in-use
        # lock), removes the tree, and silently switches the notebook
        # back to the embedded Python.
        env_form._cell_widgets[0]["editor"].setPlainText(
            'print("T3DELRUN")')
        result(env_form._run_cell_at(0) is True,
               "the pre-delete cell started")
        _wait_env_run()
        result(env_form.kernel.is_started
               and env_form.kernel.active_environment == venv_dir
               and installed_envs.is_environment_in_use(venv_dir) is True,
               "the kernel runs on the environment marked for deletion")
        env_form._delete_environment("t3_env")
        result(not os.path.isdir(venv_dir),
               "the delete removed the environment's tree from disk "
               "while this window's kernel ran on it")
        result(env_form.kernel.is_started is False
               and installed_envs.is_environment_in_use(venv_dir) is False,
               "the delete stopped this window's kernel and freed the "
               "in-use lock")
        result(env_form._selected_environment is None
               and "dfnb_environment" not in env_form.document.metadata
               and env_combo.currentData() is None
               and installed_envs.list_environments(envs_root_dir) == [],
               "deleting the selected environment silently switched the "
               "notebook back to the embedded Python")
        result("was deleted" in env_form.status_label.text(),
               "the status confirms the deletion: {!r}".format(
                   env_form.status_label.text()))
        after_delete_items = [env_combo.itemText(i)
                              for i in range(env_combo.count())]
        result(after_delete_items == [installed_envs.EMBEDDED_LABEL,
                                      installed_envs.NEW_ENV_LABEL,
                                      installed_envs.DELETE_ENV_LABEL],
               "the refreshed combo carries the embedded entry and the "
               "two administrative entries only: {}".format(
                   after_delete_items))

        # Round 18 review, the spawn-failure path end to end: the venv's
        # interpreter gone between the selection and the click (an env
        # dir that carries pyvenv.cfg but no python.exe -- discovery
        # would never offer it, so the selection is set directly, the
        # stale state a hand-deleted interpreter leaves behind). The
        # run helper (_env_run_dialog) catches the popen's OSError and
        # returns None, so the pip button's ``dialog is None`` branch
        # owns the report: no exception escapes the Qt slot, the status
        # line names the failure, exactly ONE box fires (the log record
        # under the test seam, the open-failure one-box pattern), and
        # the pip button comes back enabled with the install-running
        # flag dropped. The spec dialog is stubbed the way the
        # save-failure pin stubs document.save: swap and restore.
        ghost_dir = installed_envs.env_dir(envs_root_dir, "t3_ghost")
        os.makedirs(ghost_dir, exist_ok=True)
        with open(os.path.join(ghost_dir, "pyvenv.cfg"), "w",
                  encoding="utf-8") as handle:
            handle.write("home = {}\n".format(
                os.path.dirname(sys.executable)))
        result(os.path.isfile(installed_envs.venv_python(ghost_dir))
               is False,
               "the ghost environment carries no interpreter to spawn")
        env_form._selected_environment = "t3_ghost"
        real_pip_command_text = installed_form.pip_command_text
        installed_form.pip_command_text = (
            lambda environment_name, parent=None: "pip list")
        spawn_log = _RecordingLogHandler()
        form_logger.addHandler(spawn_log)
        slot_raised = None
        try:
            records_before = len(spawn_log.records)
            try:
                env_form._on_pip_button()
            except Exception as exc:
                slot_raised = exc
        finally:
            installed_form.pip_command_text = real_pip_command_text
            form_logger.removeHandler(spawn_log)
        spawn_entries = spawn_log.records[records_before:]
        result(slot_raised is None,
               "the spawn failure did not escape the Qt slot ({!r})"
               .format(slot_raised))
        result("did not start" in env_form.status_label.text()
               and "t3_ghost" in env_form.status_label.text(),
               "the status line reports the pip spawn failure: {!r}"
               .format(env_form.status_label.text()))
        result(len(spawn_entries) == 1
               and spawn_entries[0].startswith("Dragonfly Notebook:")
               and "t3_ghost" in spawn_entries[0]
               and "did not start" in spawn_entries[0]
               and "WinError 2" in spawn_entries[0],
               "exactly ONE failure box fired (one 'Dragonfly Notebook' "
               "log entry) naming the environment and the OSError "
               "(Popen's WinError 2, the message carries no filename "
               "on Windows): {!r}".format(spawn_entries))
        result(env_form._env_install_running is False,
               "the spawn failure dropped the install-running flag")
        result(env_form._pip_button.isEnabled()
               and "run a pip command in the environment 't3_ghost'"
               in env_form._pip_button.toolTip(),
               "the pip button is enabled again after the spawn failure "
               "with its idle tooltip: {!r}".format(
                   env_form._pip_button.toolTip()))
        # leave the form as the section found it (the ghost tree goes
        # with the suite's temp-root teardown below)
        env_form._selected_environment = None
        env_form._update_env_controls()
    finally:
        if env_form is not None:
            try:
                env_form.kernel.stop()
            except Exception:
                pass
            try:
                env_form.client.close()
            except Exception:
                pass
        try:
            env_service.close()
        except Exception:
            pass
        # the temp environments root leaves with the suite (best-effort;
        # every child was pumped to completion above)
        shutil.rmtree(envs_root_dir, ignore_errors=True)

finally:
    deleted = 0
    for obj in reversed(made):
        try:
            obj.deleteObject()
            deleted += 1
        except Exception:
            pass  # the plugin wrapper is not an ORS managed object
    print(f"deleted {deleted} of {len(made)} ORS objects", flush=True)
    # The form's kernel worker thread and bridge client: torn down with the
    # suite (the window close path is never driven headless here).
    if form is not None:
        try:
            form.kernel.stop()
        except Exception:
            pass
        try:
            form.client.close()
        except Exception:
            pass
    if bridge_service is not None:
        try:
            bridge_service.close()
        except Exception:
            pass
    app.processEvents()
    for leftover in os.listdir(temp_dir):
        try:
            os.remove(os.path.join(temp_dir, leftover))
        except OSError:
            pass
    try:
        os.rmdir(temp_dir)
    except OSError:
        pass

# ---------------------------------------------------------------------------
print("\n=== (7) the kernel smoke, subprocess-guarded ===", flush=True)

# The engine pins run in their OWN process (the dfpy_ors launcher, the
# installed engine's kernel on its worker thread), with a hard timeout --
# and the smoke is a HARD GATE: exit 0 AND the KERNEL-SMOKE-OK marker
# (printed only after every pin passed) or the suite FAILS; a smoke
# failure, timeout or unstartable helper is never a skip. The engine
# behavior (lazy start, streams, the stdin refusal, inline figures, the
# pushed dfnb, the ORS-free namespace, GUI-timer freedom through a
# sleeping cell, the request_stop interrupt, restart) is pinned by the
# subprocess smoke itself; the in-process suites never run the kernel.
# The helper is pinned to the SAME data root this suite verified
# (DFNB_INSTALL_ROOT when staged, the probe order otherwise).
helper_path = os.path.join(_ROOT, "verification", "_kernel_smoke_helper.py")
_KSMOKE_TIMEOUT_S = 300
smoke_env = dict(os.environ)
smoke_env["DFNB_INSTALL_ROOT"] = user_root
try:
    proc = subprocess.run(
        [sys.executable, os.path.join("verification", "dfpy_ors.py"),
         os.path.join("verification", "_kernel_smoke_helper.py")],
        cwd=_ROOT, capture_output=True, text=True, timeout=_KSMOKE_TIMEOUT_S,
        env=smoke_env, creationflags=_NO_WINDOW)
except subprocess.TimeoutExpired as exc:
    partial = exc.stdout
    if isinstance(partial, bytes):
        partial = partial.decode("utf-8", "replace")
    result(False, "the kernel smoke timed out after {} s -- a smoke failure "
                  "is a FAILED suite, not a skip; output tail:\n{}"
           .format(_KSMOKE_TIMEOUT_S, (partial or "")[-2000:]))
except Exception as exc:  # an unstartable smoke fails the gate too
    result(False, "the kernel smoke could not start: {}".format(exc))
else:
    smoke_tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-2000:]
    smoke_ok = proc.returncode == 0 and "KERNEL-SMOKE-OK" in proc.stdout
    result(smoke_ok,
           "the installed kernel smoke passed (lazy start, streams, "
           "stdin refusal, inline figure, pushed dfnb, ORS-free "
           "namespace, GUI-timer freedom, request_stop interrupt, "
           "queued stop, restart)"
           if smoke_ok else
           "the kernel smoke FAILED (exit code {}{}) -- a smoke failure "
           "is a FAILED suite, not a skip; output tail:\n{}"
           .format(proc.returncode,
                   "" if "KERNEL-SMOKE-OK" in proc.stdout
                   else ", KERNEL-SMOKE-OK missing", smoke_tail))
    # Round 14, the ipconfig reroute pinned through the smoke child: the
    # kernel start inside that subprocess rerouted jupyter_client's
    # interface ladder off its `ipconfig` rung (the helper prints the
    # PASS line only when the swap the REAL start path left behind is
    # in place). This is pinned through the child because a
    # jupyter_client import inside THIS suite process breaks its
    # offscreen window activation (no activeWindow, no focusWidget, and
    # every keyboard pin dies) -- bisected this round: the early
    # placement failed the ✎ Edit focus pin deterministically, and the
    # late placement crashed the suite's own teardown; the smoke child
    # already imports jupyter_client, so the check lives there.
    result(smoke_ok and "rerouted localinterfaces away from `ipconfig`"
           in proc.stdout,
           "the kernel start rerouted localinterfaces away from "
           "`ipconfig` (the library's own socket-based loader -- the "
           "round-13 window-spam fix, proven on the real start path by "
           "the smoke child)"
           if smoke_ok and "rerouted localinterfaces away from `ipconfig`"
           in proc.stdout else
           "the smoke child did not report the localinterfaces ipconfig "
           "reroute (round 14) -- output tail:\n{}".format(smoke_tail))

print("INSTALL-PATH SMOKE PASSED")
