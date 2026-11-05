"""The notebook editor window (round 8): the standalone application's
main window.

Since round 8 this form lives in the STANDALONE notebook application
process (``notebook_app.py``), NOT inside Dragonfly: it is a plain
``QMainWindow`` -- the ``OrsAbstractWindow`` base, the implementation
argument, ``WorkingContext.registerOrsWidget`` and the hidden
matplotlib pre-init canvas are all gone, because nothing in this
process may touch ORS or Dragonfly's widget framework. The form talks
to Dragonfly exclusively through the bridge client (``dfnb_client``)
that the kernel shares; the object pickers list objects THROUGH the
bridge (``dfnb.list_objects``) instead of ORS combo boxes.

A floating window holding ONE notebook: a toolbar (new/open/save, add
code/markdown cells, move/delete, run all, restart kernel, clear
outputs), a tools toolbar (snippet insert buttons + the object picker
dialogs), the cells stacked inside a resizable QScrollArea (the
shipped-plugins rule that started life in v1 still holds: tall content
must scroll), and a status strip with the per-run BUSY bar.

Running a cell (round 8): ``_run_cell_at`` only STARTS the run -- the
kernel executes the cell on its worker ``QThread`` and the form's GUI
thread stays FREE (repainting, timers, user input). Outputs arrive as
queued kernel signals and grow the running cell's output browser live;
``run_finished`` re-renders the authoritative block. The round-7
progress dialog is REPLACED by a per-run busy bar in the status strip:
a ``QProgressBar`` with range ``(0, 0)`` (indeterminate, no numbers, no
percentage -- ``textVisible(False)`` on top of the range that makes the
percentage undefined anyway). The busy animation is Qt's own; the
form's ``QTimer`` drives the live elapsed footer -- timer events
deliver mid-run precisely because the GUI thread is free, the property
round 7 could not rely on and worked around with heartbeat
``repaint()``s. The running cell's Run button becomes a red **Stop**
button; clicking it sets ONLY ``kernel.stop_requested`` -- the
heartbeat tracer on the worker thread (``notebook_kernel``) arms the
actual interrupt at the cell's next Python line, never inside a
GUI-thread Qt slot. Every other run affordance disables for the run;
because runs are asynchronous now, every STRUCTURAL action (new, open,
add, move, delete, clear outputs, the pickers) is guarded while a cell
runs -- a rebuild mid-run would tear down the running cell's widgets,
which the synchronous rounds could not race.

Disconnected state: when Dragonfly (the bridge) goes away the form
shows a red banner and the status says so; a 2 s timer keeps trying the
intended pipe and then every ``dfnb_bridge_*`` pipe on the machine, so
reopening Dragonfly reconnects the app automatically. dfnb calls
during the gap raise the client's clear disconnect error, which surfaces
as a normal cell error output.

Keyboard: Ctrl+Enter runs the focused cell, Shift+Enter runs it and
advances (inserting a fresh cell at the end, Jupyter-style), Alt+Enter
runs it and inserts a new cell right below; Ctrl+S saves. Round 12 adds
the rendered-markdown exception: a RENDERED markdown cell is run-INERT
(its browser consumes the run chords outright -- only its ✎ Edit flip
makes it runnable again) and flat-chrome (no run button, no type label,
no borders -- it blends onto the page background). Round 13 completes
the picture: Shift+Enter there is the ONE chord that acts -- the browser
emits skipForward and the form advances to the next EDITABLE cell,
stepping over rendered markdowns.

The kernel (``notebook_kernel.NotebookKernel``) is created at form
construction but started LAZILY on the first cell run -- never at import
time, never at construction. Structural edits (add/delete/move/open/new)
rebuild the cell widgets from the document after syncing editor texts
back; a cell run never rebuilds.

Custom Python environments (round 16): the main toolbar row carries an
environment combo (Dragonfly's embedded Python first, then every venv
under ``%LOCALAPPDATA%\\DragonflyNotebook\\environments``, then
``+ New Environment``) and a pip button (round 17: one field taking the
FULL pip command, e.g. ``pip install yfinance`` or ``pip uninstall
numpy``; pip does not run from cells). The button is enabled while a
custom environment is selected and nothing of this window is running (a
cell run or an environment operation) -- round 18 dropped the
in-use-kernel condition that locked it permanently after the first cell
run (the kernel stays started, and a restart keeps its lock by
design); a running kernel's hold is a tooltip note instead (replacing
loaded DLLs can fail with a Windows file-in-use error). Deleting an
environment (round 18) stops THIS window's kernel first when it runs
on the deleted one; only another window's kernel still refuses.
Switching
environments while a kernel runs asks once, then shuts the kernel down:
the next run lazy-starts on the new environment, whose packages overlay
the embedded ones (see ``notebook_envs``). The choice is stored in the
notebook's metadata (``dfnb_environment``) and restored on open; a
missing environment falls back to the embedded Python.

Named mainform_dragonflynotebook (the shipped mainform_* form-module
pattern the plugin used): on Windows's case-insensitive filesystem a
form module named 'dragonflynotebook.py' would collide with the plugin
module 'DragonflyNotebook.py'. The module kept its name when it moved
into the standalone application process.
"""

import os

os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import logging  # noqa: E402
import math  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from PyQt6.QtCore import QEventLoop, QLocale, QPoint, Qt, QTimer, \
    pyqtSignal  # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor, QValidator  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,  # noqa: E402
                             QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMainWindow, QMenu, QMessageBox, QProgressBar,
                             QPushButton, QRadioButton, QScrollArea, QSpinBox,
                             QStackedWidget, QTextBrowser, QToolButton,
                             QVBoxLayout, QWidget)

from .dfnb_client import DfnbClient  # noqa: E402
from . import notebook_envs  # noqa: E402
from .dragonflynotebook_core import (SNIPPET_ORDER, SNIPPET_SPECS,  # noqa: E402
                                     NotebookDocument, generate_snippet,
                                     registry_load, registry_save,
                                     sanitize_identifier)
from .dragonflynotebook_core import __version__ as _PLUGIN_VERSION  # noqa: E402
from .notebook_cells import (NotebookCellEditor, PythonCellHighlighter,  # noqa: E402
                             _BrowserViewportResizeFilter,
                             _RenderedMarkdownKeyFilter,
                             available_image_width, code_font,
                             fit_document_images, render_outputs_html,
                             render_stream_chunk)
from .notebook_drop_policy import ipynb_drop_decision  # noqa: E402
from .notebook_kernel import NotebookKernel  # noqa: E402

_FILE_FILTER = 'Jupyter notebooks (*.ipynb);;All files (*)'

# The form's logger: the failure paths log here when the application is
# under test, exactly the way OrsTMessageBox decides log-vs-modal
# (orsMessageBox.py lines 141-144) -- stdlib logging, because OrsLibraries
# does not exist in this process.
_log = logging.getLogger('DragonflyNotebook')
# The seam's level, set by the module that owns the logger: an UNSET
# logger inherits the root's WARNING, which would drop the INFO failure
# entries entirely -- and under running_in_test the log entry IS the
# failure surface (there is no box to fall back to).
_log.setLevel(logging.INFO)

# Smoke hooks (the tier-3 spawn test): when DFNB_SMOKE is set the form
# prints one line per observable behavior the test pins -- the busy bar's
# no-percentage state (DFNB-BUSY-BAR, once per run start) and the live
# timer ticks that prove the GUI thread stays responsive mid-run
# (DFNB-TICKS). Connection/disconnection prints live in the app entry
# (notebook_app.py) so the banner pin and the connect pin are separate
# lines.
_SMOKE = bool(os.environ.get('DFNB_SMOKE'))

# The live elapsed tick cadence: the GUI thread is free while a cell runs,
# so a plain QTimer delivers mid-run -- the live footer ticks ~10x per
# second and every tick is observable evidence of GUI responsiveness.
_LIVE_TICK_MS = 100

# The reconnect cadence while the bridge is down (and the liveness probe
# cadence while it is up -- a near-free socket state read).
_RECONNECT_INTERVAL_MS = 2000

# The frame allowance on top of the laid-out document height: the
# browser's frame chrome (the native style's frame plus the
# QTextBrowser#cell_output stylesheet border) sits OUTSIDE
# document().size(), so the fixed height must carry it or the last
# pixel row clips into a scrollbar. Probed as the smallest allowance
# whose verticalScrollBar().maximum() stays 0 alike for a multi-line
# text output and an embedded 600x300 figure -- under the runtime's OWN
# platform, the native Windows one the app and the tier-3 suite run on
# (the offscreen platform styles the frame 1 px thinner and there the
# same sweep says 2; the vbar-max-0 arithmetic is the round-9 editor
# pins').
_OUTPUT_HEIGHT_FUDGE = 3

# The far ceiling on the full-height fit (round 21): full rendering has
# NO user-facing cap below it (a multi-megapixel figure or a ~880k-line
# text dump still lays out whole); the valve exists only because Qt
# itself refuses widgets taller than QWIDGETSIZE_MAX (16777215 px) --
# past that, setFixedHeight silently clamps while the vertical policy
# stays AlwaysOff and the bottom of the output becomes unreachable. The
# ceiling sits below Qt's limit with headroom, and the fit flips THAT
# browser's vertical policy to AsNeeded when it engages, so even the
# extreme output stays scrollable and reachable.
_MAX_FULL_HEIGHT = 15000000

# Round 22: the debounce window for the image-fit pass. A window drag
# emits a viewport resize storm; every resize ARMS this single-shot
# timer (the _BrowserViewportResizeFilter), so the whole drag coalesces
# into ONE full pass over every cell browser with the CURRENT viewport
# widths -- no per-pixel re-render. 140 ms is short enough that a
# settle feels immediate, long enough to swallow the storm.
_IMAGE_FIT_DEBOUNCE_MS = 140

# Round 22b: the cap on the scroll anchor's repeated ensures (the
# round-21 retry-budget pattern): the anchor re-ensures at each quiet
# pass until one confirms the geometry stable, at most this many times,
# so a pathological layout cannot pin the view forever.
_IMAGE_FIT_ANCHOR_MAX_ENSURES = 4

# The settle chain's retry budget (round 21): the deferred scroll settle
# after a structural rebuild re-ensures the current cell on zero-timer
# passes while the rebuilt layout is still moving (the stored-output
# browsers and the editors settle over several event-loop turns) and
# bounds the chain at this many retries -- no accumulation past it.
_SETTLE_RETRY_PASSES = 12

# The footer watermark (round 10, the h5 importer's convention): an
# understated gray line pinned to the very bottom of the form, carrying
# attribution and the support contact. Deliberately NOT emitted to the
# log, a message box or a frame: it lives only as this footer label.
# Round 15 leads with the plugin's CURRENT version (the ONE source in
# dragonflynotebook_core, the module this ORS-free form and the ORS glue
# both import), so every window names the exact build it runs.
_WATERMARK_TEXT = (
    'DragonflyNotebook {}. Developed at Aarhus University as part of the '
    'XtremeCT project. If any issues arise, email '
    'marius.didziokas@chem.au.dk with a short description of the problem '
    'and the Dragonfly log.'.format(_PLUGIN_VERSION))

# The whole form's look, hardcoded (Dragonfly 2025.1 is dark-only). Every
# selector is objectName- or property-scoped, with ONE deliberate
# exception: the bare QMenu block below. Child dialogs (the picker
# dialogs open from the cells) inherit ancestor stylesheets, so a bare
# type selector here would restyle them -- for menus that restyle is the
# intent: the toolbar's add-snippet menus are bare QMenu children, and
# in this all-dark application every menu going dark is correct
# (scoping it would leave some menus OS-pale). The picker dialogs and
# the failure boxes carry their own dark look instead: _DIALOG_STYLESHEET
# below, scoped to the dialogs' own object names (#snippet_dialog /
# #dfnb_message) and applied on the dialog/box itself, so the
# ancestor-inheritance path nothing downstream can switch off never has
# to cover them.
_FORM_STYLESHEET = """
QWidget#notebook_form, QWidget#notebook_central { background: #1E1E1E; }
QScrollArea#mainScrollArea { background: transparent; border: none; }
QWidget#cellsContainer { background: transparent; }
QWidget#toolbar QToolButton {
    background: transparent; border: 1px solid transparent;
    border-radius: 4px; padding: 3px 8px; color: #D4D4D4;
}
QWidget#toolbar QToolButton:hover {
    background: #2D2D30; border: 1px solid #3C3C3C;
}
QWidget#toolbar QToolButton:pressed { background: #333337; }
QWidget#toolbar QToolButton:disabled { color: #6A6A6A; }
QWidget#toolbar QToolButton#run_all_button { color: #7FB3E0; font-weight: 600; }
QWidget#toolbar QToolButton#run_all_button:hover {
    border: 1px solid #4F94CD; background: rgba(79, 148, 205, 0.12);
}
QWidget#toolbar QToolButton#run_all_button:disabled { color: #6A6A6A; }
/* Round 16: the environment combo in the main toolbar row (the pip
   button is a plain toolbar QToolButton, covered by the rules above).
   The combo shares the toolbar's dark field look; its popup follows
   the menu look. Scoped on QWidget#toolbar exactly like the tool
   buttons, so no parented dialog's combos are touched. */
QWidget#toolbar QComboBox {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 4px; padding: 2px 6px;
}
QWidget#toolbar QComboBox:focus { border-color: #4F94CD; }
QWidget#toolbar QComboBox:disabled { color: #6A6A6A; }
QWidget#toolbar QComboBox::drop-down { border: none; }
QWidget#toolbar QComboBox QAbstractItemView {
    background: #252526; color: #D4D4D4; border: 1px solid #3C3C3C;
    selection-background-color: #094771;
}
QToolButton#add_code_end_button, QToolButton#add_markdown_end_button {
    background: transparent; border: 1px solid transparent;
    border-radius: 4px; padding: 3px 8px; color: #9D9D9D;
}
QToolButton#add_code_end_button:hover,
QToolButton#add_markdown_end_button:hover {
    background: #2D2D30; border: 1px solid #3C3C3C;
}
QFrame#toolbar_separator { background: #3C3C3C; }
QMenu {
    background: #252526; color: #D4D4D4; border: 1px solid #3C3C3C;
}
QMenu::item { padding: 4px 24px 4px 12px; }
QMenu::item:selected { background: #094771; }
QMenu::separator { height: 1px; background: #3C3C3C; margin: 4px 8px; }
QProgressBar#busy_bar {
    background: #252526; border: 1px solid #3C3C3C; border-radius: 3px;
    max-height: 10px; min-height: 10px;
}
QProgressBar#busy_bar::chunk { background: #4F94CD; border-radius: 3px; }
QLabel#disconnected_banner {
    color: #F14C4C; background: rgba(241, 76, 76, 0.10);
    border: 1px solid #F14C4C; border-radius: 4px; padding: 4px 8px;
}
QFrame[dfnb_cell="true"] {
    background: #252526; border: 1px solid #3C3C3C; border-radius: 6px;
}
QFrame[dfnb_cell="true"][hovered="true"] { border-color: #4A4A4A; }
QFrame[dfnb_cell="true"][current="true"] { border-color: #4F94CD; }
QPushButton#run_button {
    color: #7FB3E0; background: rgba(79, 148, 205, 0.12);
    border: 1px solid #4F94CD; border-radius: 4px; padding: 2px 10px;
}
QPushButton#run_button:hover { background: rgba(79, 148, 205, 0.24); }
QPushButton#run_button:disabled { color: #6A6A6A; border-color: #3C3C3C; }
QPushButton#cancel_button {
    color: #F14C4C; background: transparent;
    border: 1px solid #F14C4C; border-radius: 4px; padding: 2px 10px;
}
QPushButton#cancel_button:hover { background: rgba(241, 76, 76, 0.15); }
QPushButton#cancel_button:disabled {
    color: #6A6A6A; border-color: #3C3C3C; background: transparent;
}
/* Round 11: the markdown cell's flip-back button, the run button's
   steel-blue family at a lower profile (a mode toggle, not an action). */
QPushButton#markdown_edit_button {
    color: #7FB3E0; background: transparent;
    border: 1px solid #3C3C3C; border-radius: 4px; padding: 2px 8px;
}
QPushButton#markdown_edit_button:hover {
    background: rgba(79, 148, 205, 0.15); border-color: #4F94CD;
}
QPushButton#markdown_edit_button:disabled {
    color: #6A6A6A; border-color: #3C3C3C; background: transparent;
}
/* Round 12: a RENDERED markdown cell goes FLAT -- no cell background, no
   border in ANY state. The base rules above stay verbatim; these win by
   the extra dfnb_rendered attribute selector alone (one per existing
   QFrame[dfnb_cell] combination, so hover/current can never leak a
   border back), and the rendered browser blends onto the #1E1E1E page
   (border: none also drops the base rule's border-top). */
QFrame[dfnb_cell="true"][dfnb_rendered="true"] {
    background: transparent; border: none;
}
QFrame[dfnb_cell="true"][dfnb_rendered="true"][hovered="true"] {
    background: transparent; border: none;
}
QFrame[dfnb_cell="true"][dfnb_rendered="true"][current="true"] {
    background: transparent; border: none;
}
QTextBrowser#cell_output[dfnb_rendered="true"] {
    background: transparent; border: none;
}
QLabel#cell_elapsed { color: #C8C8C8; }
QLabel#cell_type_label { color: #C8C8C8; }
QLabel#cell_prompt { color: rgb(230, 230, 230); }
QTextBrowser#cell_output {
    background: #1B1B1C; color: rgb(230, 230, 230);
    border-top: 1px solid #3C3C3C;
}
QLabel#status_label { color: #C8C8C8; }
QLabel#env_label { color: #C8C8C8; }
QFrame#status_strip { border-top: 1px solid #3C3C3C; background: #1E1E1E; }
QLabel#watermark_label { color: #808080; font-size: 8pt; }

/* --- Scrollbars, ported VERBATIM from Dragonfly's app stylesheet
   (resource/stylesheet/main.txt, the QScrollBar blocks) in round 9: the
   standalone application process has no app-global QSS to inherit, and
   the default OS scrollbar on the main panel was the one light element
   on the window. SCOPED on purpose (the round-4b house rule): child
   dialogs inherit ancestor stylesheets, so every scrollbar selector
   names its surface -- the main cells panel (QScrollArea#mainScrollArea,
   the one vertical scroll area) and the per-cell output browsers
   (QTextBrowser#cell_output, which keep their own HORIZONTAL scrollbar
   only -- the vertical policy is AlwaysOff, the browser renders at its
   full content height; the code editors have none at all). An unscoped
   scrollbar rule here would restyle every dialog's lists too. --- */
QScrollArea#mainScrollArea QScrollBar:horizontal,
QTextBrowser#cell_output QScrollBar:horizontal {
     border: 1px solid #222222;
     background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0.0 #121212, stop: 0.2 #282828, stop: 1 #484848);
     height: 9px;
     margin: 0px 16px 0 16px;
}
QScrollArea#mainScrollArea QScrollBar::handle:horizontal,
QTextBrowser#cell_output QScrollBar::handle:horizontal
{
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #606060, stop: 0.5 #606060, stop: 1 #606060);
      min-height: 20px;
      border-radius: 2px;
}
QScrollArea#mainScrollArea QScrollBar::add-line:horizontal,
QTextBrowser#cell_output QScrollBar::add-line:horizontal {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #404040, stop: 1 #404040);
      width: 14px;
      subcontrol-position: right;
      subcontrol-origin: margin;
}
QScrollArea#mainScrollArea QScrollBar::sub-line:horizontal,
QTextBrowser#cell_output QScrollBar::sub-line:horizontal {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #404040, stop: 1 #404040);
      width: 14px;
     subcontrol-position: left;
     subcontrol-origin: margin;
}
QScrollArea#mainScrollArea QScrollBar::right-arrow:horizontal,
QTextBrowser#cell_output QScrollBar::right-arrow:horizontal,
QScrollArea#mainScrollArea QScrollBar::left-arrow:horizontal,
QTextBrowser#cell_output QScrollBar::left-arrow:horizontal
{
      border: 1px solid black;
      width: 1px;
      height: 1px;
      background: white;
}
QScrollArea#mainScrollArea QScrollBar::add-page:horizontal,
QTextBrowser#cell_output QScrollBar::add-page:horizontal,
QScrollArea#mainScrollArea QScrollBar::sub-page:horizontal,
QTextBrowser#cell_output QScrollBar::sub-page:horizontal
{
      background: none;
}
QScrollArea#mainScrollArea QScrollBar:vertical
{
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0.0 #121212, stop: 0.2 #282828, stop: 1 #484848);
      width: 9px;
      margin: 16px 0 16px 0;
      border: 1px solid #222222;
}
QScrollArea#mainScrollArea QScrollBar::handle:vertical
{
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #606060, stop: 0.5 #606060, stop: 1 #606060);
      min-height: 20px;
      border-radius: 2px;
}
QScrollArea#mainScrollArea QScrollBar::add-line:vertical
{
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: bottom;
      subcontrol-origin: margin;
}
QScrollArea#mainScrollArea QScrollBar::sub-line:vertical
{
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: top;
      subcontrol-origin: margin;
}
QScrollArea#mainScrollArea QScrollBar::up-arrow:vertical,
QScrollArea#mainScrollArea QScrollBar::down-arrow:vertical
{
      border: 1px solid black;
      width: 1px;
      height: 1px;
      background: white;
}
QScrollArea#mainScrollArea QScrollBar::add-page:vertical,
QScrollArea#mainScrollArea QScrollBar::sub-page:vertical
{
      background: none;
}
"""

# The pickers' own look (round 10): the notebook's palette values, scoped
# to exactly the two object names this module names -- every selector here
# starts with QDialog#snippet_dialog or QMessageBox#dfnb_message, NEVER a
# bare type selector (child dialogs inherit ancestor stylesheets, so an
# unscoped rule in this string would restyle anything a parented dialog
# ever shows). Applied on the dialog/box itself (SnippetDialog.__init__ /
# _show_message), so the parenting that would otherwise bypass the form's
# ancestor inheritance cannot bypass this either. The scrollbar blocks are
# the round-9 ORS port (resource/stylesheet/main.txt) re-scoped to the
# dialog: they style the properties list's scrollbar and the combo popups.
_DIALOG_STYLESHEET = """
QDialog#snippet_dialog { background: #252526; color: #D4D4D4; }
QMessageBox#dfnb_message { background: #252526; color: #D4D4D4; }
QDialog#snippet_dialog QLabel { color: #D4D4D4; }
QMessageBox#dfnb_message QLabel { color: #D4D4D4; }
QDialog#snippet_dialog QLineEdit {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 3px; padding: 2px 6px;
    selection-background-color: #094771;
}
QDialog#snippet_dialog QLineEdit:focus { border-color: #4F94CD; }
QDialog#snippet_dialog QLineEdit:disabled { color: #6A6A6A; }
QDialog#snippet_dialog QComboBox {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 3px; padding: 2px 6px;
}
QDialog#snippet_dialog QComboBox:focus { border-color: #4F94CD; }
QDialog#snippet_dialog QComboBox:disabled { color: #6A6A6A; }
QDialog#snippet_dialog QComboBox::drop-down { border: none; }
QDialog#snippet_dialog QComboBox QAbstractItemView {
    background: #252526; color: #D4D4D4; border: 1px solid #3C3C3C;
    selection-background-color: #094771;
}
QDialog#snippet_dialog QSpinBox,
QDialog#snippet_dialog QDoubleSpinBox {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 3px; padding: 2px 6px;
    selection-background-color: #094771;
}
QDialog#snippet_dialog QSpinBox:focus,
QDialog#snippet_dialog QDoubleSpinBox:focus { border-color: #4F94CD; }
QDialog#snippet_dialog QSpinBox:disabled,
QDialog#snippet_dialog QDoubleSpinBox:disabled { color: #6A6A6A; }
QDialog#snippet_dialog QSpinBox::up-button,
QDialog#snippet_dialog QDoubleSpinBox::up-button {
    background: #2D2D30; border: 1px solid #3C3C3C;
}
QDialog#snippet_dialog QSpinBox::down-button,
QDialog#snippet_dialog QDoubleSpinBox::down-button {
    background: #2D2D30; border: 1px solid #3C3C3C;
}
QDialog#snippet_dialog QCheckBox,
QDialog#snippet_dialog QRadioButton { color: #D4D4D4; }
QDialog#snippet_dialog QCheckBox::indicator,
QDialog#snippet_dialog QRadioButton::indicator {
    width: 13px; height: 13px;
    border: 1px solid #3C3C3C; background: #1E1E1E;
}
QDialog#snippet_dialog QRadioButton::indicator { border-radius: 7px; }
QDialog#snippet_dialog QCheckBox::indicator:checked,
QDialog#snippet_dialog QRadioButton::indicator:checked {
    background: #4F94CD; border-color: #4F94CD;
}
QDialog#snippet_dialog QListWidget#properties_list {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 3px;
}
QDialog#snippet_dialog QListWidget#properties_list::item { color: #D4D4D4; padding: 2px 4px; }
QDialog#snippet_dialog QListWidget#properties_list::item:selected { background: #094771; }
QDialog#snippet_dialog QListWidget::indicator {
    width: 13px; height: 13px;
    border: 1px solid #3C3C3C; background: #1E1E1E;
}
QDialog#snippet_dialog QListWidget::indicator:checked {
    background: #4F94CD; border-color: #4F94CD;
}
QDialog#snippet_dialog QScrollBar:horizontal {
     border: 1px solid #222222;
     background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0.0 #121212, stop: 0.2 #282828, stop: 1 #484848);
     height: 9px;
     margin: 0px 16px 0 16px;
}
QDialog#snippet_dialog QScrollBar::handle:horizontal {
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #606060, stop: 0.5 #606060, stop: 1 #606060);
      min-height: 20px;
      border-radius: 2px;
}
QDialog#snippet_dialog QScrollBar::add-line:horizontal {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #404040, stop: 1 #404040);
      width: 14px;
      subcontrol-position: right;
      subcontrol-origin: margin;
}
QDialog#snippet_dialog QScrollBar::sub-line:horizontal {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #404040, stop: 1 #404040);
      width: 14px;
     subcontrol-position: left;
     subcontrol-origin: margin;
}
QDialog#snippet_dialog QScrollBar::right-arrow:horizontal,
QDialog#snippet_dialog QScrollBar::left-arrow:horizontal {
      border: 1px solid black;
      width: 1px;
      height: 1px;
      background: white;
}
QDialog#snippet_dialog QScrollBar::add-page:horizontal,
QDialog#snippet_dialog QScrollBar::sub-page:horizontal {
      background: none;
}
QDialog#snippet_dialog QScrollBar:vertical {
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0.0 #121212, stop: 0.2 #282828, stop: 1 #484848);
      width: 9px;
      margin: 16px 0 16px 0;
      border: 1px solid #222222;
}
QDialog#snippet_dialog QScrollBar::handle:vertical {
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #606060, stop: 0.5 #606060, stop: 1 #606060);
      min-height: 20px;
      border-radius: 2px;
}
QDialog#snippet_dialog QScrollBar::add-line:vertical {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: bottom;
      subcontrol-origin: margin;
}
QDialog#snippet_dialog QScrollBar::sub-line:vertical {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: top;
      subcontrol-origin: margin;
}
QDialog#snippet_dialog QScrollBar::up-arrow:vertical,
QDialog#snippet_dialog QScrollBar::down-arrow:vertical {
      border: 1px solid black;
      width: 1px;
      height: 1px;
      background: white;
}
QDialog#snippet_dialog QScrollBar::add-page:vertical,
QDialog#snippet_dialog QScrollBar::sub-page:vertical {
      background: none;
}
QDialog#snippet_dialog QDialogButtonBox QPushButton,
QMessageBox#dfnb_message QPushButton {
    background: #2D2D30; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 4px; padding: 4px 16px;
}
QDialog#snippet_dialog QDialogButtonBox QPushButton:hover,
QMessageBox#dfnb_message QPushButton:hover {
    background: #333337; border-color: #4A4A4A;
}
QDialog#snippet_dialog QDialogButtonBox QPushButton:pressed,
QMessageBox#dfnb_message QPushButton:pressed { background: #333337; }
QDialog#snippet_dialog QDialogButtonBox QPushButton:default,
QMessageBox#dfnb_message QPushButton:default {
    color: #7FB3E0; background: rgba(79, 148, 205, 0.12);
    border-color: #4F94CD;
}
QDialog#snippet_dialog QDialogButtonBox QPushButton:default:hover,
QMessageBox#dfnb_message QPushButton:default:hover {
    background: rgba(79, 148, 205, 0.24);
}
/* Round 16: the environment dialogs (the creation/pip streaming runner
   and the two small input dialogs) -- the same palette as the pickers,
   scoped to their own object name (#env_dialog, applied on the dialog
   itself), plus the streaming runner's monospace output field and busy
   bar (the status strip's busy-bar look) and a scrollbar block for the
   output field (the round-9 ORS port, re-scoped). */
QDialog#env_dialog { background: #252526; color: #D4D4D4; }
QDialog#env_dialog QLabel { color: #D4D4D4; }
QDialog#env_dialog QLineEdit {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 3px; padding: 2px 6px;
    selection-background-color: #094771;
}
QDialog#env_dialog QLineEdit:focus { border-color: #4F94CD; }
QDialog#env_dialog QLineEdit:disabled { color: #6A6A6A; }
QDialog#env_dialog QTextBrowser#env_output {
    background: #1B1B1C; color: rgb(230, 230, 230);
    border: 1px solid #3C3C3C; border-radius: 3px;
}
QDialog#env_dialog QProgressBar#env_progress {
    background: #252526; border: 1px solid #3C3C3C; border-radius: 3px;
    max-height: 10px; min-height: 10px;
}
QDialog#env_dialog QProgressBar#env_progress::chunk {
    background: #4F94CD; border-radius: 3px;
}
QDialog#env_dialog QDialogButtonBox QPushButton {
    background: #2D2D30; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 4px; padding: 4px 16px;
}
QDialog#env_dialog QDialogButtonBox QPushButton:hover {
    background: #333337; border-color: #4A4A4A;
}
QDialog#env_dialog QDialogButtonBox QPushButton:pressed {
    background: #333337;
}
QDialog#env_dialog QDialogButtonBox QPushButton:default {
    color: #7FB3E0; background: rgba(79, 148, 205, 0.12);
    border-color: #4F94CD;
}
QDialog#env_dialog QDialogButtonBox QPushButton:default:hover {
    background: rgba(79, 148, 205, 0.24);
}
/* Round 18: the delete picker's environment list, the same field look
   the snippet_dialog properties list uses (re-scoped to #env_dialog). */
QDialog#env_dialog QListWidget#env_delete_list {
    background: #1E1E1E; color: #D4D4D4;
    border: 1px solid #3C3C3C; border-radius: 3px;
}
QDialog#env_dialog QListWidget#env_delete_list::item {
    color: #D4D4D4; padding: 2px 4px;
}
QDialog#env_dialog QListWidget#env_delete_list::item:selected {
    background: #094771;
}
QDialog#env_dialog QScrollBar:vertical {
    background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0.0 #121212, stop: 0.2 #282828, stop: 1.0 #484848);
    width: 9px;
    margin: 16px 0 16px 0;
    border: 1px solid #222222;
}
QDialog#env_dialog QScrollBar::handle:vertical {
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #606060, stop: 0.5 #606060, stop: 1 #606060);
      min-height: 20px;
      border-radius: 2px;
}
QDialog#env_dialog QScrollBar::add-line:vertical {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: bottom;
      subcontrol-origin: margin;
}
QDialog#env_dialog QScrollBar::sub-line:vertical {
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: top;
      subcontrol-origin: margin;
}
QDialog#env_dialog QScrollBar::up-arrow:vertical,
QDialog#env_dialog QScrollBar::down-arrow:vertical {
      border: 1px solid black;
      width: 1px;
      height: 1px;
      background: white;
}
QDialog#env_dialog QScrollBar::add-page:vertical,
QDialog#env_dialog QScrollBar::sub-page:vertical {
      background: none;
}
"""


def _repolish(widget):
    """Re-evaluates the form stylesheet for one widget after a dynamic
    property or objectName flip: QSS selectors only re-match on polish."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def format_elapsed_live(elapsed_s):
    """The live elapsed tick format, ``0:02.4`` (minutes:seconds.tenths) --
    the format the round-7 progress dialog's footer used, kept verbatim
    now that the dialog is gone and the footer is driven by the form's
    timer."""
    minutes = int(elapsed_s // 60)
    return '{:d}:{:04.1f}'.format(minutes, elapsed_s - minutes * 60)


def format_elapsed(elapsed_s):
    """The pinned final format, ``2.31 s``."""
    return '{:.2f} s'.format(elapsed_s)


def _show_message(parent, text, icon=QMessageBox.Icon.Critical):
    """Exactly ONE message box per failure path, titled with the plugin's
    display name -- and, under the application's test convention
    (orsMessageBox.py lines 141-144: with ``running_in_test`` the message
    is logged, never shown modal), a LOG entry instead, so automated
    headless harnesses never block on a dialog. Every failure box in this
    module goes through here (the round-7 review repaired the paths that
    had missed the seam; round 8 makes the seam the only door)."""
    app = QApplication.instance()
    if app is None or app.property('running_in_test'):
        _log.info('Dragonfly Notebook: %s', text)
        return
    box = QMessageBox(icon, 'Dragonfly Notebook', text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setWindowTitle('Dragonfly Notebook')
    # the same seam as the pickers: name + stylesheet on the box itself
    # (a box parented to a picker inherits that picker's stylesheet --
    # the #dfnb_message selectors keep both looks consistent)
    box.setObjectName('dfnb_message')
    box.setStyleSheet(_DIALOG_STYLESHEET)
    box.exec()


class _CellFrame(QFrame):
    """One cell's container frame: flips the 'hovered' property on
    enter/leave (with an unpolish/polish) so the form stylesheet's hover
    border rule re-evaluates.

    Round 12: a press anywhere on the frame announces itself through
    ``frameClicked`` -- the form makes the clicked cell the current one,
    because a RENDERED markdown cell has no editor to click into and the
    frame is its only selection surface. ``super()`` runs first and no
    press handling is overridden (the frame carries no drag start -- the
    only drag paths in the app are the editors' drop handlers and the
    window's .ipynb drop; QFrame itself ignores presses, so the event
    propagates to the parent exactly as before)."""

    frameClicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setProperty('dfnb_cell', True)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.frameClicked.emit()

    def enterEvent(self, event):
        self.setProperty('hovered', True)
        _repolish(self)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setProperty('hovered', False)
        _repolish(self)
        super().leaveEvent(event)


# ---------------------------------------------------------------------------
# the picker dialogs (ported from the deleted Dragonfly-side
# picker_dialogs.py, round 8)
# ---------------------------------------------------------------------------

# which class each import-kind picker reads from (through the bridge)
_IMPORT_SOURCES = {
    'import_volume': 'Channel',
    'import_roi': 'ROI',
    'import_multiroi': 'MultiROI',
    'import_properties': 'MultiROI',
}

# assign-to defaults (the publish ones mirror generate_snippet's
# new_<noun> fallbacks)
_VARIABLE_DEFAULTS = {
    'import_volume': 'volume_np',
    'import_roi': 'mask_np',
    'import_multiroi': 'labels_np',
    'import_properties': 'props',
    'publish_volume': 'new_volume',
    'publish_roi': 'new_roi',
    'publish_multiroi': 'new_multiroi',
}

# the numpy array a publish kind reads (generate_snippet's positional arg)
_ARRAY_DEFAULTS = {
    'publish_volume': 'volume_np',
    'publish_roi': 'mask_np',
    'publish_multiroi': 'labels_np',
}

_TITLE_DEFAULTS = {
    'publish_volume': 'Result volume',
    'publish_roi': 'Processed ROI',
    'publish_multiroi': 'New segmentation',
}


class _LocaleSafeDoubleSpinBox(QDoubleSpinBox):
    """Accepts both '.' and ',' as the decimal separator, whatever the
    machine locale is: validation normalizes commas and parses with float,
    display formats C-locale '.6g'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # the both-separators recipe's other half: the C locale keeps the
        # base class's locale-dependent parsing/formatting out of the way
        self.setLocale(QLocale.c())

    def validate(self, text, pos):
        stripped = text.replace(',', '.').strip()
        if stripped in ('', '+', '-', '.', '+.', '-.'):
            return QValidator.State.Intermediate, text, pos
        try:
            value = float(stripped)
        except ValueError:
            return QValidator.State.Invalid, text, pos
        if self.minimum() <= value <= self.maximum():
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Intermediate, text, pos

    def valueFromText(self, text):
        try:
            return float(text.replace(',', '.').strip())
        except ValueError:
            return self.minimum()

    def textFromValue(self, value):
        return format(float(value), '.6g')


class SnippetDialog(QDialog):
    """The picker for one snippet kind; ``parameters()`` returns the
    ``generate_snippet`` kwargs.

    Ported from the deleted Dragonfly-side ``picker_dialogs.py`` (round
    8): the ORS ``OrsObjectClassComboBox`` pickers cannot exist in the
    notebook application process, so the object lists are read THROUGH
    the bridge -- ``DfnbClient.call('list_objects', ...)`` -- and fill
    plain combos; the measured-properties chooser refreshes through
    ``list_measured_properties`` whenever the picked MultiROI or the
    timestep changes. A bridge failure (Dragonfly disconnected) shows
    its ONE message box and leaves the combo empty.

    The dialog fields per kind:

      * import_volume / import_roi / import_multiroi: assign-to name, the
        object combo for the read-from class, the timestep;
      * import_properties: the above plus the measured-properties chooser
        (checkable items; checked ones become the imported columns, none
        checked blocks OK when the list is non-empty, an empty list is
        allowed and imports every slot);
      * publish kinds: the numpy array variable to publish, the assign-to
        name of the published object, its title, the optional reference
        volume combo, and for publish_volume the optional explicit spacing
        (locale-safe spin boxes, units stated in the labels);
      * export_properties: a MODE radio -- 'Write into an existing
        MultiROI' (the MultiROI combo + timestep) vs 'Publish a new
        MultiROI from an array' (the publish fields: array, assign-to,
        title, optional reference, explicit spacing shown when no
        reference is inherited) -- plus, in both modes, the properties
        source as a kernel variable name (a pandas DataFrame or a dict of
        columns).

    Headless testing: the dialogs are constructed and their widgets and
    ``parameters()`` are pinned directly -- ``exec()`` is NEVER called in
    the suite (it would block headless).
    """

    def __init__(self, kind, client, parent=None):
        super().__init__(parent)
        if kind not in SNIPPET_SPECS:
            raise ValueError('unknown snippet kind {!r}'.format(kind))
        self.kind = kind
        self._client = client
        spec = SNIPPET_SPECS[kind]
        self.setWindowTitle('dfnb.{} -- {}'.format(spec['function'],
                                                  spec['label']))
        # the dialog's own dark look (_DIALOG_STYLESHEET targets this
        # object name); applied HERE so no parent chain can leave the
        # picker on the OS palette
        self.setObjectName('snippet_dialog')
        self.setStyleSheet(_DIALOG_STYLESHEET)
        self.setMinimumWidth(380)
        self.setModal(True)

        is_import = kind in _IMPORT_SOURCES
        is_publish = kind.startswith('publish_')

        form = QFormLayout()

        if kind == 'export_properties':
            # the export picker has TWO modes (a radio): write into an
            # EXISTING MultiROI, or publish a NEW MultiROI from an array --
            # its fields are built by _build_export_fields, including the
            # assign-to name of the publish mode
            self._build_export_fields(form)
        else:
            self.variable_edit = QLineEdit(_VARIABLE_DEFAULTS[kind], self)
            self.variable_edit.setObjectName('variable_edit')
            form.addRow('Assign to', self.variable_edit)

            if is_publish:
                self.array_edit = QLineEdit(_ARRAY_DEFAULTS[kind], self)
                self.array_edit.setObjectName('array_edit')
                self.array_edit.setToolTip('The kernel variable holding the '
                                           'numpy array to publish')
                form.addRow('Array to publish', self.array_edit)

                self.title_edit = QLineEdit(_TITLE_DEFAULTS[kind], self)
                self.title_edit.setObjectName('title_edit')
                form.addRow('Published title', self.title_edit)

                self.reference_check = QCheckBox(
                    'Inherit geometry and spacing from a reference volume',
                    self)
                self.reference_check.setObjectName('reference_check')
                form.addRow(self.reference_check)
                self.reference_combo = self._make_object_combo(
                    'Channel', 'reference volume')
                self.reference_combo.setObjectName('reference_combo')
                self.reference_combo.setEnabled(False)
                self.reference_check.toggled.connect(
                    self.reference_combo.setEnabled)
                form.addRow('Reference volume', self.reference_combo)

                if kind == 'publish_volume':
                    self.spacing_check = QCheckBox('Set explicit spacing',
                                                   self)
                    self.spacing_check.setObjectName('spacing_check')
                    form.addRow(self.spacing_check)
                    spacing_row = QWidget(self)
                    spacing_layout = QHBoxLayout(spacing_row)
                    spacing_layout.setContentsMargins(0, 0, 0, 0)
                    self.spacing_spins = []
                    for axis in ('X', 'Y', 'Z'):
                        spin = _LocaleSafeDoubleSpinBox(self)
                        spin.setObjectName('spacing_{}_spin'.format(
                            axis.lower()))
                        spin.setRange(1e-06, 1000000.0)
                        spin.setDecimals(6)
                        spin.setSingleStep(0.1)
                        spin.setValue(1.0)
                        spin.setEnabled(False)
                        self.spacing_spins.append(spin)
                        spacing_layout.addWidget(QLabel(
                            '{} spacing (mm)'.format(axis), self))
                        spacing_layout.addWidget(spin)
                    self.spacing_check.toggled.connect(
                        self._set_spacing_enabled)
                    form.addRow(spacing_row)

            else:
                source_name = _IMPORT_SOURCES[kind]
                self.source_combo = self._make_object_combo(
                    source_name, source_name)
                self.source_combo.setObjectName('source_combo')
                form.addRow('{} to read from'.format(source_name),
                            self.source_combo)

                self.timestep_spin = QSpinBox(self)
                self.timestep_spin.setObjectName('timestep_spin')
                self.timestep_spin.setRange(0, 2147483647)
                form.addRow('Timestep', self.timestep_spin)

                if kind == 'import_properties':
                    self.properties_list = QListWidget(self)
                    self.properties_list.setObjectName('properties_list')
                    self.properties_list.setMinimumHeight(120)
                    self.properties_list.setToolTip(
                        'Checked properties become columns of the imported '
                        'table; none available imports every slot')
                    form.addRow('Measured properties', self.properties_list)
                    # refresh through the bridge whenever the picked
                    # MultiROI or the timestep changes
                    self.source_combo.currentIndexChanged.connect(
                        lambda _index: self._refresh_properties())
                    self.timestep_spin.valueChanged.connect(
                        lambda _value: self._refresh_properties())
                    self._refresh_properties()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel,
                                   self)
        buttons.setObjectName('dialog_buttons')
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # ---- the bridge-fed combos -------------------------------------------

    def _make_object_combo(self, class_name, what, on_failure=None):
        """A plain combo filled THROUGH the bridge with the live objects of
        one ORS class (``dfnb.list_objects``, representable-only -- exactly
        the objects the Dragonfly UI offers). The item text is the object's
        title; the item data is the STRING the generated snippet passes as
        the dfnb source/reference argument: the title when it is unique in
        the list (what the user sees is unambiguous), the GUID otherwise
        (duplicated titles would be ambiguous). A bridge failure shows its
        ONE message box -- through ``on_failure``, so the export picker
        (the only dialog with TWO bridge-fed combos) can route both fills
        through one once-guarded handler -- and leaves the combo empty."""
        combo = QComboBox(self)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(16)
        combo.setToolTip('Every {} currently available in the Dragonfly '
                         'scene'.format(what))
        failure = self._fill_object_combo(combo, class_name)
        if failure is not None:
            if on_failure is None:
                on_failure = self._show_fill_failure
            on_failure(class_name, failure)
        return combo

    def _show_fill_failure(self, class_name, exc):
        """The one-box report for a failed combo fill (the message text in
        ONE place; every single-combo dialog's failure path lands here)."""
        _show_message(self,
                      'the {} list could not be read from Dragonfly: '
                      '{}'.format(class_name, exc))

    def _fill_object_combo(self, combo, class_name):
        """Fills ``combo`` through the bridge and returns None on success
        or the exception on failure: the failure's MESSAGE BOX decision
        belongs to the caller (``_make_object_combo``), because the export
        picker fills two combos at construction and exactly-one-box-per-
        failure-path means its two fills report ONE box, not two."""
        combo.clear()
        try:
            payload = self._client.call('list_objects', {
                'class_name': class_name,
                'representable_only': True})
            entries = payload.get('objects') or []
        except Exception as exc:
            return exc
        title_counts = {}
        for entry in entries:
            title = str((entry or {}).get('title') or '').strip()
            if title:
                title_counts[title] = title_counts.get(title, 0) + 1
        for entry in entries:
            title = str((entry or {}).get('title') or '').strip()
            guid = str((entry or {}).get('guid') or '')
            if title and title_counts.get(title, 0) == 1:
                combo.addItem(title, title)
            elif title:
                combo.addItem('{} ({})'.format(title, guid[:8]), guid)
            else:
                combo.addItem('Untitled ({})'.format(guid[:8]), guid)
        return None

    def _refresh_properties(self):
        """Refills the chooser through the bridge for the picked MultiROI
        and timestep. A failure shows its ONE message box and leaves the
        chooser empty (OK then imports every slot -- which is none)."""
        self.properties_list.clear()
        reference = self.source_combo.currentData()
        if not reference:
            return
        try:
            payload = self._client.call('list_measured_properties', {
                'source': reference,
                'timestep': self.timestep_spin.value()})
        except Exception as exc:
            _show_message(self,
                          'the measured properties of the picked MultiROI '
                          'could not be read: {}'.format(exc))
            return
        for label in payload.get('properties') or []:
            item = QListWidgetItem(str(label))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.properties_list.addItem(item)

    # ---- behavior ---------------------------------------------------------

    def _set_spacing_enabled(self, checked):
        for spin in self.spacing_spins:
            spin.setEnabled(checked)

    def _build_export_fields(self, form):
        """The export picker's fields: a MODE radio (write into an EXISTING
        MultiROI vs publish a NEW MultiROI from an array) swapping two field
        pages, and the properties source -- a kernel variable name -- shared
        by both modes. The TWO bridge-fed combos (the MultiROI source and
        the Channel reference) share ONE failure box per open, through the
        once-guarded handler below."""
        # The export picker is the ONLY dialog constructing TWO bridge-fed
        # combos at build time: with the bridge unreachable BOTH fills
        # fail, and exactly-one-box-per-failure-path means one open shows
        # one box -- the first failure's message (the second is the same
        # dead bridge, subsumed).
        reported = []

        def _one_failure_box(class_name, exc):
            if not reported:
                reported.append(class_name)
                self._show_fill_failure(class_name, exc)

        self.mode_existing_radio = QRadioButton(
            'Write into an existing MultiROI', self)
        self.mode_existing_radio.setObjectName('mode_existing_radio')
        self.mode_publish_radio = QRadioButton(
            'Publish a new MultiROI from an array', self)
        self.mode_publish_radio.setObjectName('mode_publish_radio')
        form.addRow(self.mode_existing_radio)
        form.addRow(self.mode_publish_radio)
        self.mode_existing_radio.setChecked(True)

        self.properties_edit = QLineEdit('props_df', self)
        self.properties_edit.setObjectName('properties_edit')
        self.properties_edit.setToolTip('The kernel variable holding the '
                                        'properties to export: a pandas '
                                        'DataFrame (one column per property) '
                                        'or a dict {name: values}')
        form.addRow('Properties variable', self.properties_edit)

        self.mode_stack = QStackedWidget(self)
        self.mode_stack.setObjectName('mode_stack')

        existing_page = QWidget(self)
        existing_form = QFormLayout(existing_page)
        existing_form.setContentsMargins(0, 0, 0, 0)
        self.source_combo = self._make_object_combo(
            'MultiROI', 'MultiROI', on_failure=_one_failure_box)
        self.source_combo.setObjectName('source_combo')
        existing_form.addRow('MultiROI to write into', self.source_combo)
        self.timestep_spin = QSpinBox(existing_page)
        self.timestep_spin.setObjectName('timestep_spin')
        self.timestep_spin.setRange(0, 2147483647)
        existing_form.addRow('Timestep', self.timestep_spin)
        # Default unchecked = add mode, today's behavior. Wipe is
        # meaningless in publish mode (a fresh MultiROI has no existing
        # properties to replace), so the checkbox lives only on this page
        # and hides with it (the mode stack swaps whole pages).
        self.wipe_check = QCheckBox('Wipe existing properties (replace all)',
                                    existing_page)
        self.wipe_check.setObjectName('wipe_check')
        existing_form.addRow(self.wipe_check)
        self.mode_stack.addWidget(existing_page)

        publish_page = QWidget(self)
        publish_form = QFormLayout(publish_page)
        publish_form.setContentsMargins(0, 0, 0, 0)
        self.array_edit = QLineEdit('mask_np', publish_page)
        self.array_edit.setObjectName('array_edit')
        self.array_edit.setToolTip('The kernel variable holding the labels '
                                   'array to publish')
        publish_form.addRow('Array to publish', self.array_edit)
        self.variable_edit = QLineEdit('new_mroi', publish_page)
        self.variable_edit.setObjectName('variable_edit')
        publish_form.addRow('Assign to', self.variable_edit)
        self.title_edit = QLineEdit('Measured segmentation', publish_page)
        self.title_edit.setObjectName('title_edit')
        publish_form.addRow('Published title', self.title_edit)
        self.reference_check = QCheckBox(
            'Inherit geometry and spacing from a reference volume', self)
        self.reference_check.setObjectName('reference_check')
        publish_form.addRow(self.reference_check)
        self.reference_combo = self._make_object_combo(
            'Channel', 'reference volume', on_failure=_one_failure_box)
        self.reference_combo.setObjectName('reference_combo')
        self.reference_combo.setEnabled(False)
        self.reference_check.toggled.connect(self.reference_combo.setEnabled)
        publish_form.addRow('Reference volume', self.reference_combo)
        self.spacing_check = QCheckBox('Set explicit spacing', self)
        self.spacing_check.setObjectName('spacing_check')
        publish_form.addRow(self.spacing_check)
        self.spacing_row = QWidget(self)
        spacing_layout = QHBoxLayout(self.spacing_row)
        spacing_layout.setContentsMargins(0, 0, 0, 0)
        self.spacing_spins = []
        for axis in ('X', 'Y', 'Z'):
            spin = _LocaleSafeDoubleSpinBox(self)
            spin.setObjectName('spacing_{}_spin'.format(axis.lower()))
            spin.setRange(1e-06, 1000000.0)
            spin.setDecimals(6)
            spin.setSingleStep(0.1)
            spin.setValue(1.0)
            spin.setEnabled(False)
            self.spacing_spins.append(spin)
            spacing_layout.addWidget(QLabel(
                '{} spacing (mm)'.format(axis), self))
            spacing_layout.addWidget(spin)
        self.spacing_check.toggled.connect(self._set_spacing_enabled)
        publish_form.addRow(self.spacing_row)
        # the explicit spacing is offered only when NO reference volume is
        # inherited (the reference provides the geometry): checking the
        # reference unchecks and hides the spacing row
        self.reference_check.toggled.connect(self._update_spacing_visibility)
        self.mode_stack.addWidget(publish_page)

        form.addRow(self.mode_stack)
        self.mode_existing_radio.toggled.connect(self._set_export_mode)

    def _set_export_mode(self, existing_checked):
        """The mode radio swaps the two field pages."""
        self.mode_stack.setCurrentIndex(0 if existing_checked else 1)

    def _update_spacing_visibility(self):
        """The explicit spacing row is shown only when no reference volume
        is inherited (the reference provides the geometry)."""
        has_reference = self.reference_check.isChecked()
        self.spacing_check.setVisible(not has_reference)
        self.spacing_row.setVisible(not has_reference)
        if has_reference and self.spacing_check.isChecked():
            self.spacing_check.setChecked(False)

    def selected_properties(self):
        """The labels of the checked property items."""
        return [self.properties_list.item(index).text()
                for index in range(self.properties_list.count())
                if self.properties_list.item(index).checkState()
                == Qt.CheckState.Checked]

    def _on_accept(self):
        needs_source = self.kind in _IMPORT_SOURCES or (
            self.kind == 'export_properties'
            and self.mode_existing_radio.isChecked())
        if needs_source and self.source_combo.currentData() is None:
            source_name = _IMPORT_SOURCES.get(self.kind, 'MultiROI')
            _show_message(
                self,
                'No {} is available to pick. Create or show one in '
                'Dragonfly first.'.format(source_name),
                icon=QMessageBox.Icon.Warning)
            return
        if self.kind == 'import_properties' and \
                self.properties_list.count() > 0 and \
                not self.selected_properties():
            _show_message(
                self,
                'Check at least one measured property (or cancel).',
                icon=QMessageBox.Icon.Warning)
            return
        self.accept()

    # ---- the result -------------------------------------------------------

    def parameters(self):
        """The ``generate_snippet`` kwargs for this dialog's state."""
        kind = self.kind
        if kind == 'export_properties':
            props = sanitize_identifier(self.properties_edit.text(),
                                        fallback='props_df')
            if self.mode_existing_radio.isChecked():
                params = {'source': self.source_combo.currentData(),
                          'timestep': self.timestep_spin.value(),
                          'properties': props}
                # the key is OMITTED when unchecked: the generated line
                # stays the minimal pre-wipe shape
                if self.wipe_check.isChecked():
                    params['wipe'] = True
                return params
            params = {'array': sanitize_identifier(self.array_edit.text(),
                                                   fallback='mask_np'),
                      'variable': sanitize_identifier(
                          self.variable_edit.text(), fallback='new_mroi'),
                      'title': self.title_edit.text().strip()
                               or 'Measured segmentation',
                      'reference': self.reference_combo.currentData()
                                   if self.reference_check.isChecked()
                                   else None,
                      'properties': props}
            if self.spacing_check.isChecked():
                params['spacing'] = tuple(spin.value()
                                          for spin in self.spacing_spins)
            return params
        params = {'variable': sanitize_identifier(self.variable_edit.text(),
                                                  fallback='value')}
        if kind in _IMPORT_SOURCES:
            params['source'] = self.source_combo.currentData()
            params['timestep'] = self.timestep_spin.value()
            if kind == 'import_properties':
                checked = self.selected_properties()
                params['properties'] = checked if checked else None
            return params
        params['array'] = sanitize_identifier(self.array_edit.text(),
                                              fallback=_ARRAY_DEFAULTS[kind])
        params['title'] = self.title_edit.text().strip() or \
            _TITLE_DEFAULTS[kind]
        params['reference'] = (self.reference_combo.currentData()
                               if self.reference_check.isChecked() else None)
        if kind == 'publish_volume' and self.spacing_check.isChecked():
            params['spacing'] = tuple(spin.value()
                                      for spin in self.spacing_spins)
        return params


def snippet_parameters_dialog(kind, client, parent=None):
    """Runs the kind's picker modally; returns the ``generate_snippet``
    kwargs, or None when the user cancelled or the dialog failed (its ONE
    failure box already shown)."""
    try:
        dialog = SnippetDialog(kind, client, parent)
    except Exception as exc:
        _show_message(parent, 'the picker for {} could not be opened: {}'
                      .format(kind, exc))
        return None
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    try:
        return dialog.parameters()
    except Exception as exc:
        _show_message(parent, 'the parameters for {} could not be read: {}'
                      .format(kind, exc))
        return None


# ---------------------------------------------------------------------------
# the environment dialogs (round 16)
# ---------------------------------------------------------------------------

class _EnvProcessDialog(QDialog):
    """The streaming runner for the environment long runs (creating a
    venv, pip installing into one): a monospace output browser that
    grows line by line while the no-window child works (~40 s for a
    venv, network time for a pip command), an indeterminate busy bar
    (the status strip's busy-bar look), and a Cancel button that
    terminates the child. Cancel flips to Close when the run ends.

    Threading: :meth:`start` spawns the child (through
    ``notebook_envs.popen``: CREATE_NO_WINDOW, merged pipes) on the
    caller's thread; a daemon READER thread iterates the merged stdout
    and forwards each line and the exit code through queued signals
    (``line_arrived`` / ``run_done`` -- the reader never touches a
    widget). Headless testing: the suite calls :meth:`start` and pumps
    ``QApplication.processEvents`` until :attr:`exit_code` is set;
    ``exec()`` is never called there (the SnippetDialog convention).
    """

    line_arrived = pyqtSignal(str)
    run_done = pyqtSignal(int)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('env_dialog')
        self.setStyleSheet(_DIALOG_STYLESHEET)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self._proc = None
        self._reader = None
        self._exit_code = None
        self._cancelled = False
        self._lines = []

        self.output = QTextBrowser(self)
        self.output.setObjectName('env_output')
        self.output.setFont(code_font())
        self.output.setFixedHeight(220)
        self.progress = QProgressBar(self)
        self.progress.setObjectName('env_progress')
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()  # shown when a run starts
        self.buttons = QDialogButtonBox(self)
        self.cancel_button = self.buttons.addButton(
            'Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        self.close_button = self.buttons.addButton(
            'Close', QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_button.hide()  # shown when the run ends
        self.cancel_button.clicked.connect(self._on_cancel)
        self.close_button.clicked.connect(self.accept)
        self.line_arrived.connect(self._on_line_arrived)
        self.run_done.connect(self._on_run_done)

        layout = QVBoxLayout(self)
        layout.addWidget(self.output)
        layout.addWidget(self.progress)
        layout.addWidget(self.buttons)

    # ---- the run -----------------------------------------------------------

    def start(self, argv):
        """Spawns the child and the reader thread. One dialog runs ONE
        child (the form creates a fresh dialog per operation)."""
        self._proc = notebook_envs.popen(argv)
        self.progress.show()
        self._reader = threading.Thread(target=self._read_child, daemon=True)
        self._reader.start()

    def _read_child(self):
        proc = self._proc
        try:
            for line in proc.stdout:
                self.line_arrived.emit(line.rstrip('\r\n'))
            code = proc.wait()
        except Exception:
            code = -1
        self.run_done.emit(int(code))

    # ---- state for the form and the suite ----------------------------------

    @property
    def exit_code(self):
        """The child's exit code, or None while it runs."""
        return self._exit_code

    @property
    def transcript(self):
        """Every line the child printed (the failure boxes quote its
        tail; the suite pins the whole transcript)."""
        return list(self._lines)

    def note(self, text):
        """Appends one explanatory line to the output and the transcript
        (the form's phase announcements: the venv module prints NOTHING
        for most of its ~40 s creation run, so without notes the dialog
        sits silent and looks stuck). GUI-thread only -- the form calls
        it around the child's own lines, which arrive queued."""
        self._on_line_arrived(text)

    @property
    def was_cancelled(self):
        return self._cancelled

    def _on_line_arrived(self, line):
        self._lines.append(line)
        self.output.append(line)

    def _on_run_done(self, code):
        self._exit_code = int(code)
        self.progress.hide()
        self.cancel_button.hide()
        self.close_button.show()
        self.close_button.setDefault(True)
        self.close_button.setFocus()

    def _on_cancel(self):
        if self._exit_code is None and self._proc is not None:
            self._cancelled = True
            try:
                self._proc.terminate()
            except Exception:
                pass

    def reject(self):
        # Esc / the window's X while the child runs would close the
        # dialog with the reader still attached: refuse it (Cancel is
        # the way out of a running child; after it ends, reject closes
        # normally).
        if self._exit_code is None and self._proc is not None:
            return
        super().reject()


class NewEnvironmentDialog(QDialog):
    """The '+ New Environment' entry's dialog: one field, the name,
    validated live against ``notebook_envs.validate_name`` (the reason
    under the field when invalid, OK disabled until valid), and the
    honest note that creation takes about a minute. Headless testing:
    constructed and pinned directly, ``exec()`` never runs in the
    suite."""

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.setObjectName('env_dialog')
        self.setStyleSheet(_DIALOG_STYLESHEET)
        self.setWindowTitle('New Python environment')
        self.setModal(True)
        self.setMinimumWidth(420)
        self._root = root

        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName('name_edit')
        self.name_edit.setPlaceholderText('name (letters, digits, . _ -)')
        self.message_label = QLabel('', self)
        self.message_label.setObjectName('name_message')
        self.message_label.setWordWrap(True)
        self.note_label = QLabel(
            'The environment is created with Dragonfly\'s Python and its '
            'own pip. Creation takes about a minute; packages can be pip '
            'installed into it afterwards.', self)
        self.note_label.setObjectName('name_note')
        self.note_label.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.setObjectName('dialog_buttons')
        self.ok_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.name_edit.textChanged.connect(self._revalidate)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow('Name', self.name_edit)
        layout.addLayout(form)
        layout.addWidget(self.message_label)
        layout.addWidget(self.note_label)
        layout.addWidget(self.buttons)
        # A pristine empty field never emits textChanged, so the opening
        # state validates here: the reason sits under the field from the
        # first paint instead of a disabled OK with no explanation.
        self._revalidate()

    def _revalidate(self):
        try:
            notebook_envs.validate_name(self.name_edit.text(), self._root)
        except ValueError as exc:
            self.message_label.setText(str(exc))
            self.ok_button.setEnabled(False)
            return
        self.message_label.setText('')
        self.ok_button.setEnabled(True)

    def environment_name(self):
        """The validated name (empty when nothing valid was entered)."""
        try:
            return notebook_envs.validate_name(self.name_edit.text(),
                                               self._root)
        except ValueError:
            return ''


def new_environment_dialog(root, parent=None):
    """Runs the New Environment dialog modally; returns the validated
    name, or None when the user cancelled (the ONE failure box for a
    construction failure is already shown)."""
    try:
        dialog = NewEnvironmentDialog(root, parent)
    except Exception as exc:
        _show_message(parent,
                      'the New Environment dialog could not be opened: {}'
                      .format(exc))
        return None
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.environment_name() or None


class PipCommandDialog(QDialog):
    """The pip button's dialog: one field taking the FULL pip command
    (``pip install yfinance``, ``pip uninstall numpy``, ``pip list``; a
    leading ``python -m pip`` is tolerated and stripped, and the bare
    ``install yfinance`` spelling works too). OK stays disabled until
    the field holds a pip command. Headless testing: constructed and
    pinned directly, ``exec()`` never runs in the suite."""

    def __init__(self, environment_name, parent=None):
        super().__init__(parent)
        self.setObjectName('env_dialog')
        self.setStyleSheet(_DIALOG_STYLESHEET)
        self.setWindowTitle('pip in \'{}\''.format(environment_name))
        self.setModal(True)
        self.setMinimumWidth(420)

        self.command_edit = QLineEdit(self)
        self.command_edit.setObjectName('command_edit')
        self.command_edit.setPlaceholderText(
            'e.g. pip install yfinance, pip uninstall numpy, pip list')
        self.message_label = QLabel('', self)
        self.message_label.setObjectName('command_message')
        self.message_label.setWordWrap(True)
        self.note_label = QLabel(
            'Runs in the environment \'{}\' with its own pip; the output '
            'streams below the field while it runs. Pip commands do not '
            'run from cells. (An update runs as install --upgrade; an '
            'uninstall is confirmed automatically.)'.format(
                environment_name), self)
        self.note_label.setObjectName('command_note')
        self.note_label.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.setObjectName('dialog_buttons')
        self.ok_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.command_edit.textChanged.connect(self._revalidate)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow('Command', self.command_edit)
        layout.addLayout(form)
        layout.addWidget(self.message_label)
        layout.addWidget(self.note_label)
        layout.addWidget(self.buttons)
        # The same opening-state validation as the New Environment dialog:
        # the empty pristine field never emits textChanged.
        self._revalidate()

    def _revalidate(self):
        # the dialog's own gate is the SAME normalization the run uses
        # (notebook_envs.pip_command_words): a command that is only
        # launcher spellings never reaches OK
        try:
            notebook_envs.pip_command_words(self.command_edit.text())
        except ValueError:
            self.message_label.setText('Enter the pip command (for '
                                       'example: pip install yfinance).')
            self.ok_button.setEnabled(False)
            return
        self.message_label.setText('')
        self.ok_button.setEnabled(True)

    def command(self):
        """The command text as typed (``pip_command`` normalizes the
        launcher spellings and the aliases when it builds the argv)."""
        return self.command_edit.text().strip()


def pip_command_text(environment_name, parent=None):
    """Runs the pip dialog modally; returns the command text as typed,
    or None when the user cancelled (the ONE failure box for a
    construction failure is already shown)."""
    try:
        dialog = PipCommandDialog(environment_name, parent)
    except Exception as exc:
        _show_message(parent,
                      'the pip dialog could not be opened: {}'.format(exc))
        return None
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.command() or None


class DeleteEnvironmentDialog(QDialog):
    """The '- Delete Environment' picker (round 18): the environment
    list as one QListWidget on the plugin's dark dialog stylesheet --
    the plain ``QInputDialog.getItem`` it replaced never took the
    stylesheet and stood out against every other dialog of the flow.
    One row per existing environment, the first pre-selected, OK
    enabled while a row is selected (an empty list never opens: the
    delete flow refuses it before constructing this). Headless testing:
    constructed and pinned directly, ``exec()`` never runs in the
    suite."""

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.setObjectName('env_dialog')
        self.setStyleSheet(_DIALOG_STYLESHEET)
        self.setWindowTitle('Dragonfly Notebook')
        self.setModal(True)
        self.setMinimumWidth(380)

        self.prompt_label = QLabel('Environment to delete:', self)
        self.prompt_label.setObjectName('delete_prompt')
        self.env_list = QListWidget(self)
        self.env_list.setObjectName('env_delete_list')
        for name in names:
            QListWidgetItem(name, self.env_list)
        self.env_list.setCurrentRow(0 if names else -1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.setObjectName('dialog_buttons')
        self.ok_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.env_list.currentRowChanged.connect(self._revalidate)

        layout = QVBoxLayout(self)
        layout.addWidget(self.prompt_label)
        layout.addWidget(self.env_list)
        layout.addWidget(self.buttons)
        # The opening-state gate (the same pattern as the two input
        # dialogs above): the pre-selected row must reach OK from the
        # first paint.
        self._revalidate(self.env_list.currentRow())

    def _revalidate(self, row):
        """OK follows the selection: an environment must be picked (the
        current row changed signal carries the new row)."""
        self.ok_button.setEnabled(row >= 0)

    def selected_environment(self):
        """The picked name ('' when nothing is selected)."""
        item = self.env_list.currentItem()
        return item.text() if item is not None else ''


def delete_environment_dialog(names, parent=None):
    """Runs the delete picker modally; returns the picked environment
    name, or None when the user cancelled (the ONE failure box for a
    construction failure is already shown)."""
    try:
        dialog = DeleteEnvironmentDialog(names, parent)
    except Exception as exc:
        _show_message(parent,
                      'the delete-environment dialog could not be opened: '
                      '{}'.format(exc))
        return None
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_environment() or None


# ---------------------------------------------------------------------------
# the form
# ---------------------------------------------------------------------------

class DragonflyNotebookForm(QMainWindow):
    """The notebook editor window (one per application process; several
    applications may run against several Dragonfly instances -- each
    launcher click spawns one)."""

    def __init__(self, pipe_name=None, parent=None):
        super().__init__(parent)

        # The bridge client: created here, connected right after the UI is
        # built (the launcher starts the bridge BEFORE spawning this app,
        # so the first connect normally succeeds immediately). It is also
        # the object the kernel seed pushes as ``dfnb``.
        self.client = DfnbClient(pipe_name=pipe_name)
        self.client.connected_signal.connect(self._on_client_connected)
        self.client.disconnected_signal.connect(self._on_client_disconnected)

        self.document = NotebookDocument.new_notebook()
        self.file_path = None
        # The kernel engine: created here (cheap, starts NOTHING), started
        # lazily by the first run -- on its own worker thread, so this
        # window's GUI thread never runs a cell.
        self.kernel = NotebookKernel(dfnb=self.client, parent=self)
        self._wire_kernel_signals()

        self._cell_widgets = []          # per-index records, see _add_cell_widget
        # Round 22: ONE single-shot debounce timer and ONE shared resize
        # filter for every cell widget's viewport (output browsers AND
        # editors -- an editor's grow-with-content height change moves
        # the frames just the same, so it must delay the anchor's quiet
        # pass too). A viewport resize (a window drag, a panel reflow)
        # only ARMS the timer; when it fires, _fit_all_browser_images
        # re-runs the uniform image fit over every cell browser with the
        # CURRENT viewport widths (markdown browsers included), so
        # figures larger than the window are rescaled after every window
        # resize.
        self._browser_image_fit_timer = QTimer(self)
        self._browser_image_fit_timer.setSingleShot(True)
        self._browser_image_fit_timer.setInterval(_IMAGE_FIT_DEBOUNCE_MS)
        self._browser_image_fit_timer.timeout.connect(
            self._fit_all_browser_images)
        self._browser_resize_filter = _BrowserViewportResizeFilter(
            self._arm_browser_image_fit, parent=self)
        # Round 22b: the scroll-anchor state machine, see
        # _fit_all_browser_images. 0 = idle; 1 = armed by a structural
        # rebuild (the ensure waits for the first image-changing pass);
        # 2 = active (each quiet pass repairs or confirms; the anchor
        # retires after two consecutive stable quiet passes). The
        # counter bounds the repetitions.
        self._image_fit_anchor_state = 0
        self._image_fit_anchor_ensures = 0
        # Round 22b: whether the current frame was in view at the last
        # KNOWN scroll position. The scrollbar's user-action hooks
        # (actionTriggered/sliderMoved) refresh
        # it on every real scroll (the user's, or the round-21 settle's);
        # content drift never fires that signal, so a quiet pass that
        # finds the frame out of view with this still True knows the
        # layout moved under a still viewport (repairable) -- while
        # False means the user scrolled away (never yanked); see
        # _fit_all_browser_images and _update_image_fit_anchor_in_view.
        self._image_fit_anchor_in_view = False
        # Round 22b: consecutive stable quiet passes before the anchor
        # retires (one is not enough -- probed storm lulls exceed the
        # debounce, so a single stable pass can still be mid-storm).
        self._image_fit_anchor_stable_streak = 0
        # Round 22b: whether ANY arm since the last pass saw the current
        # frame in view. The pass-time visibility is too late -- a
        # resize's first image wave can push the frame out during the
        # 140 ms before the debounced pass, and gating the promotion on
        # the pass-time position would then skip the repair entirely.
        # The arm-time position is where the user had the cell when the
        # geometry event arrived.
        self._image_fit_anchor_saw_visible = False
        # Round 22b: nesting counter around the form's OWN scroll
        # repositioning (the round-21 settle chain, focus, the anchor's
        # ensure). A scrollbar move made under this flag is not a user
        # decision -- the settle can pass through positions where the
        # current frame is out of view on the way to it -- so it must
        # never revoke the anchor's recorded visibility.
        self._anchor_internal_scroll = 0
        self._current_cell_index = -1    # the editor that last had focus
        self._running = False            # a cell (or Run All) is executing
        self._rebuilding = False         # suppresses spurious dirty flags
        self._running_cell_index = -1    # the cell currently running
        self._run_started_at = 0.0        # time.monotonic() at the run start
        self._run_advance = False        # Shift+Enter pending for the run
        self._run_insert_after = False   # Alt+Enter pending for the run
        # The Run All chain: the runs are ASYNCHRONOUS now, so the chain
        # advances from _on_run_finished (one QTimer.singleShot(0) hop per
        # cell -- no recursion inside a signal handler).
        self._run_all_active = False
        self._run_all_total = 0
        self._run_all_ran = 0
        self._smoke_ticks = 0            # DFNB_SMOKE: live-tick counter

        # Round 16: the environment state. The combo (built in
        # _build_ui's toolbar row) lists the embedded Python first,
        # then every environment under the root, then '+ New
        # Environment'; ``_selected_environment`` is the environment
        # NAME (None = embedded), stored per notebook in the document's
        # metadata. ``_env_install_running`` is True while a venv
        # creation or pip child of THIS window streams (the controls
        # rule refuses a second one).
        self._envs_root = notebook_envs.envs_root()
        self._selected_environment = None
        self._env_install_running = False

        # Round 11, the markdown cell modes: the ids of the markdown cells
        # currently in EDIT mode (source editor visible, rendered browser
        # hidden). Keyed by the CELL's id -- not the widget index -- so the
        # teardown/rebuild of every structural edit (add/delete/move) carries
        # the modes through with zero bookkeeping: the ids travel with the
        # cells. Cleared on a document load (a fresh document must not
        # inherit stale ids); _add_cell_below puts a freshly added markdown
        # cell's id in (it starts EDIT), _run_cell_at drops it out (running
        # renders, and the mode survives a rebuild as RENDERED).
        self._markdown_edit_ids = set()

        # Round 9: the window is a drop site for .ipynb files -- dropping a
        # notebook from Windows Explorer onto THIS window opens it here
        # (the per-cell editors ignore the same drags so they reach the
        # window; the enter/move/drop handlers below accept only the
        # shared drop policy's 'consume' case).
        self.setAcceptDrops(True)

        self._build_ui()
        # Round 16: fill the environment combo (embedded + discovered
        # environments + '+ New Environment'); the selection starts at
        # the embedded Python (the default for a fresh notebook).
        self._refresh_env_list()
        self._rebuild_cells()
        self._update_title()
        self._set_status('New notebook -- kernel starts on the first run.')

        # First connection attempt: the exact pipe the launcher passed
        # (never a discovery pass at startup -- a sibling Dragonfly
        # instance's bridge must not capture this app). False only means
        # the reconnect timer keeps trying.
        self.client.connect(pipe_name)
        self._disconnected_banner.setVisible(not self.client.is_connected())
        self._reconnect_timer.start()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.setObjectName('notebook_form')
        self.resize(940, 720)
        self.setMinimumSize(680, 460)

        central = QWidget(self)
        central.setObjectName('notebook_central')
        outerLayout = QVBoxLayout(central)
        outerLayout.setContentsMargins(8, 8, 8, 8)
        outerLayout.setSpacing(4)

        toolbar = self._build_toolbar_row()
        outerLayout.addWidget(toolbar)
        tools_row = self._build_tools_row()
        outerLayout.addWidget(tools_row)

        # The disconnected banner: hidden while the bridge is up, shown the
        # moment it drops (Dragonfly closed or restarted) and hidden again
        # on reconnect.
        self._disconnected_banner = QLabel(
            'Dragonfly is disconnected -- dfnb calls fail until the bridge '
            'is back. The notebook reconnects automatically once Dragonfly '
            'is up again.', self)
        self._disconnected_banner.setObjectName('disconnected_banner')
        self._disconnected_banner.setWordWrap(True)
        self._disconnected_banner.hide()
        outerLayout.addWidget(self._disconnected_banner)

        self.scrollArea = QScrollArea(self)
        self.scrollArea.setObjectName('mainScrollArea')
        self.scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        self.scrollArea.setWidgetResizable(True)
        self.cellsContainer = QWidget()
        self.cellsContainer.setObjectName('cellsContainer')
        self.cellsLayout = QVBoxLayout(self.cellsContainer)
        self.cellsLayout.setContentsMargins(8, 8, 8, 8)
        self.cellsLayout.setSpacing(8)
        self.cellsLayout.addStretch(1)
        # The end-of-list add-cell affordance: built ONCE here, after the
        # trailing stretch, and kept alive by _rebuild_cells (which only
        # tears the cell frames down).
        self._end_row_buttons = []
        end_row = QWidget()
        end_row_layout = QHBoxLayout(end_row)
        end_row_layout.setContentsMargins(0, 0, 0, 0)
        end_row_layout.setSpacing(2)
        for name, text, tooltip, handler in (
                ('add_code_end_button', '+ Code',
                 'Add a code cell below the current one', self.on_add_code_cell),
                ('add_markdown_end_button', '+ Markdown',
                 'Add a markdown cell below the current one',
                 self.on_add_markdown_cell)):
            button = QToolButton(end_row)
            button.setObjectName(name)
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(handler)
            end_row_layout.addWidget(button)
            self._end_row_buttons.append(button)
        end_row_layout.addStretch(1)
        self.cellsLayout.addWidget(end_row)
        self.scrollArea.setWidget(self.cellsContainer)
        outerLayout.addWidget(self.scrollArea, 1)
        # Round 22b: the anchor's user-scroll boundary. actionTriggered
        # and sliderMoved fire ONLY for user-driven scrollbar input
        # (wheel, drag, keys) -- programmatic setValue never does, so
        # Qt's own clamp (a shrunken maximum pulling the value down
        # after the image rescale) and the form's own repositioning
        # cannot masquerade as the user scrolling away; see
        # _update_image_fit_anchor_in_view.
        self.scrollArea.verticalScrollBar().actionTriggered.connect(
            self._update_image_fit_anchor_in_view)
        self.scrollArea.verticalScrollBar().sliderMoved.connect(
            self._update_image_fit_anchor_in_view)

        self.status_label = QLabel(self)
        self.status_label.setObjectName('status_label')
        self.status_label.setMinimumHeight(20)
        # The per-run BUSY bar (round 8, replacing the round-7 progress
        # dialog): range (0, 0) makes it an indeterminate busy indicator --
        # NO numbers, no percentage (textVisible(False) hides the label
        # outright; with min==max the percentage is undefined anyway).
        # Qt animates the busy chunk; the form's own QTimer (below) drives
        # the live elapsed footer -- timer events deliver mid-run because
        # the GUI thread is FREE now, the property the round-7 heartbeat
        # repaints had to work around.
        self.busy_bar = QProgressBar(self)
        self.busy_bar.setObjectName('busy_bar')
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedWidth(140)
        self.busy_bar.hide()
        # Round 16: the environment name sits in the status strip next
        # to the busy bar (the kernel status text stays what it was).
        self._env_label = QLabel(self)
        self._env_label.setObjectName('env_label')
        self._update_env_label()
        status_strip = QFrame(self)
        status_strip.setObjectName('status_strip')
        strip_layout = QHBoxLayout(status_strip)
        strip_layout.setContentsMargins(0, 4, 0, 0)
        strip_layout.addWidget(self.status_label)
        strip_layout.addStretch(1)
        strip_layout.addWidget(self._env_label)
        strip_layout.addWidget(self.busy_bar)
        outerLayout.addWidget(status_strip)
        # The watermark is the LAST widget of the outer layout: below the
        # status strip, always visible (the h5 importer's understated-footer
        # convention -- no print/log/box emission).
        self.watermark_label = QLabel(_WATERMARK_TEXT, self)
        self.watermark_label.setObjectName('watermark_label')
        self.watermark_label.setWordWrap(True)
        outerLayout.addWidget(self.watermark_label)

        self.setCentralWidget(central)

        save_shortcut = QShortcut(QKeySequence('Ctrl+S'), self)
        save_shortcut.activated.connect(self.on_save_notebook)

        # The live tick (the round-8 replacement for the round-7
        # heartbeat->listener seam): started with every run, stopped with
        # every run end. It only ever touches the running cell's elapsed
        # label -- plain widget updates on a FREE GUI thread.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(_LIVE_TICK_MS)
        self._live_timer.timeout.connect(self._on_live_tick)

        # The reconnect timer: runs forever (2 s). While connected it is a
        # near-free liveness probe (the socket state read detects a peer
        # that closed); while disconnected it retries the intended pipe
        # first and then every dfnb_bridge_* pipe on the machine, so
        # reopening Dragonfly reconnects the app automatically.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(_RECONNECT_INTERVAL_MS)
        self._reconnect_timer.timeout.connect(self._on_reconnect_tick)

        self.setStyleSheet(_FORM_STYLESHEET)

    def _build_toolbar_row(self):
        toolbar = QWidget(self)
        toolbar.setObjectName('toolbar')
        toolbarLayout = QHBoxLayout(toolbar)
        toolbarLayout.setContentsMargins(0, 0, 0, 0)
        toolbarLayout.setSpacing(2)
        self._toolbar_buttons = {}
        button_specs = (
            ('new', 'New', 'Start a new notebook (unsaved changes are '
             'asked about)', self.on_new_notebook),
            ('open', 'Open', 'Open a .ipynb', self.on_open_notebook),
            ('save', 'Save', 'Save the notebook (Ctrl+S)', self.on_save_notebook),
            None,
            ('add_code', '+ Code', 'Add a code cell below the current one',
             self.on_add_code_cell),
            ('add_markdown', '+ Markdown',
             'Add a markdown cell below the current one', self.on_add_markdown_cell),
            None,
            ('move_up', 'Up', 'Move the current cell up', self.on_move_cell_up),
            ('move_down', 'Down', 'Move the current cell down', self.on_move_cell_down),
            ('delete', 'Delete', 'Delete the current cell', self.on_delete_cell),
            None,
            ('run_all', 'Run All', 'Run every code cell in order', self.on_run_all),
            ('restart', 'Restart kernel',
             'Restart the kernel: the namespace is wiped and re-seeded',
             self.on_restart_kernel),
            ('clear_outputs', 'Clear outputs',
             "Clear every code cell's stored outputs", self.on_clear_outputs),
        )
        for spec in button_specs:
            if spec is None:
                toolbarLayout.addSpacing(6)
                separator = QFrame(toolbar)
                separator.setObjectName('toolbar_separator')
                separator.setFrameShape(QFrame.Shape.NoFrame)
                separator.setFixedWidth(1)
                toolbarLayout.addWidget(separator)
                toolbarLayout.addSpacing(6)
                continue
            name, text, tooltip, handler = spec
            button = QToolButton(toolbar)
            button.setObjectName(name + '_button')
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(26)
            button.clicked.connect(handler)
            toolbarLayout.addWidget(button)
            self._toolbar_buttons[name] = button
        # Round 16: the environment picker and the pip button,
        # after a separator at the row's end. Deliberately NOT in
        # _toolbar_buttons (that dict is the run-disable sweep, and these
        # two follow _update_env_controls' own finer rule instead).
        toolbarLayout.addSpacing(6)
        separator = QFrame(toolbar)
        separator.setObjectName('toolbar_separator')
        separator.setFrameShape(QFrame.Shape.NoFrame)
        separator.setFixedWidth(1)
        toolbarLayout.addWidget(separator)
        toolbarLayout.addSpacing(6)
        self._env_combo = QComboBox(toolbar)
        self._env_combo.setObjectName('env_combo')
        self._env_combo.setToolTip(
            'The Python environment the kernel runs on: Dragonfly\'s '
            'embedded Python, or one of your custom environments')
        self._env_combo.currentIndexChanged.connect(self._on_env_selected)
        toolbarLayout.addWidget(self._env_combo)
        self._pip_button = QToolButton(toolbar)
        self._pip_button.setObjectName('pip_button')
        self._pip_button.setText('pip')
        self._pip_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pip_button.setFixedHeight(26)
        self._pip_button.clicked.connect(self._on_pip_button)
        toolbarLayout.addWidget(self._pip_button)
        toolbarLayout.addStretch(1)
        return toolbar

    def _build_tools_row(self):
        """The tools toolbar (round 8, replacing the Functions dock): the
        snippet insert buttons (one menu with every dfnb kind -- a click
        inserts the generated ``dfnb.<function>(...)`` line at the focused
        cell's cursor) and the object-picker buttons (one dialog per kind,
        its object lists read THROUGH the bridge)."""
        tools_row = QWidget(self)
        tools_row.setObjectName('toolbar')
        toolsLayout = QHBoxLayout(tools_row)
        toolsLayout.setContentsMargins(0, 0, 0, 0)
        toolsLayout.setSpacing(2)

        def add_menu_button(text, tooltip, kinds, handler):
            button = QToolButton(tools_row)
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(26)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(button)
            for kind in kinds:
                action = menu.addAction(SNIPPET_SPECS[kind]['label'])
                action.triggered.connect(
                    lambda _checked=False, k=kind: handler(k))
            button.setMenu(menu)
            toolsLayout.addWidget(button)
            return button

        add_menu_button(
            'Insert snippet',
            'Insert a dfnb snippet at the current cell (with placeholder '
            'arguments to edit)',
            SNIPPET_ORDER, self._on_snippet_action)
        toolsLayout.addSpacing(6)
        separator = QFrame(tools_row)
        separator.setObjectName('toolbar_separator')
        separator.setFrameShape(QFrame.Shape.NoFrame)
        separator.setFixedWidth(1)
        toolsLayout.addWidget(separator)
        toolsLayout.addSpacing(6)
        add_menu_button(
            'Import data…',
            'Pick an object in Dragonfly and insert the dfnb import line',
            ('import_volume', 'import_roi', 'import_multiroi'),
            self._on_picker_action)
        add_menu_button(
            'Publish result…',
            'Publish a kernel array back into Dragonfly',
            ('publish_volume', 'publish_roi', 'publish_multiroi'),
            self._on_picker_action)
        add_menu_button(
            'Measured properties…',
            'Move measured properties between a MultiROI and the kernel',
            ('import_properties', 'export_properties'),
            self._on_picker_action)
        toolsLayout.addStretch(1)
        return tools_row

    def _wire_kernel_signals(self):
        kernel = self.kernel
        kernel.kernel_started.connect(self._on_kernel_started)
        kernel.kernel_stopped.connect(self._on_kernel_stopped)
        kernel.stream_arrived.connect(self._on_stream_arrived)
        kernel.display_arrived.connect(self._on_display_arrived)
        kernel.result_arrived.connect(self._on_result_arrived)
        kernel.error_arrived.connect(self._on_error_arrived)
        kernel.run_finished.connect(self._on_run_finished)
        kernel.run_failed.connect(self._on_run_failed)
        kernel.restart_finished.connect(self._on_restart_finished)

    # ------------------------------------------------------------- cells

    def _rebuild_cells(self, focus_index=None):
        """Tears the cell widgets down and rebuilds them from the document.

        Called for every STRUCTURAL change (open/new/add/delete/move/clear);
        a cell run never rebuilds (every structural action is guarded while
        a cell runs -- runs are asynchronous now, so the guard is what
        keeps the running cell's widgets alive). Editor texts are synced
        into the document first by the callers
        (``_sync_editors_to_document``)."""
        self._rebuilding = True
        try:
            self._current_cell_index = -1
            # The last two items are the trailing stretch and the
            # end-of-list add-cell row (built once in _build_ui).
            while self.cellsLayout.count() > 2:
                item = self.cellsLayout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._cell_widgets = []
            for index in range(self.document.cell_count()):
                self._add_cell_widget(index)
        finally:
            self._rebuilding = False
            # Round 22b: every structural rebuild ARMS the image pass's
            # scroll anchor (state 1). The rebuilt documents lay out on
            # SHOW -- after this flag is back to False -- so the
            # documentSizeChanged seam cannot observe the rebuild; and
            # the round-21 contract says the post-rebuild viewport
            # belongs to the current cell regardless. State 1 is not
            # yet ready to ensure: the anchor engages only once a pass
            # actually CHANGES an image (a text-only rebuild has
            # nothing for the image pass to repair -- the round-21
            # settle owns it, and consuming the flag early would let
            # the ensure fire in a mid-storm lull BEFORE the figures
            # are ever rescaled, probed). State 1 also downgrades to 0
            # at a quiet pass with no images changed (the rebuild is
            # over, the settle's own contract held).
            self._image_fit_anchor_state = 1
        self._refresh_cell_accent()
        if focus_index is None:
            focus_index = 0 if self._cell_widgets else -1
        if 0 <= focus_index < len(self._cell_widgets):
            self._focus_cell(focus_index)
            # Round 21: the synchronous ensure above runs while the
            # rebuilt layout is still unsettled -- the editors take their
            # real height one zero-timer later (resizeToContent) and the
            # browsers on their first documentSizeChanged -- so it can
            # land near the TOP once the layout settles taller. One
            # deferred pass re-ensures the focused frame after that
            # settle; the index is looked up at call time, so a stale
            # call from a superseded rebuild no-ops when the index is
            # gone (a rebuild resets it to -1 first).
            QTimer.singleShot(0, self._settle_scroll_on_current_cell)

    def _settle_scroll_on_current_cell(self, retries=_SETTLE_RETRY_PASSES):
        """The deferred scroll settle after a structural rebuild (round
        21): the viewport must show the CURRENT cell once the rebuilt
        layout has settled, not fall back to the top. Reads
        ``_current_cell_index`` at call time and no-ops when it is out
        of range, so a deferred call left over from a rebuild that was
        itself replaced cannot scroll to a wrong frame.

        Self-chaining and bounded, gated on LAYOUT STABILITY: a rebuild
        with stored outputs keeps growing for several event-loop turns
        (each editor's resizeToContent lands on its own zero-timer, and
        every stored-output browser re-fits on its documentSizeChanged
        storms), and a single pass -- or a pass chained on visibility
        alone -- can land MID-CASCADE: the frame reads visible, then
        the growth above it pushes it out of the viewport (repro: 25
        code cells with 40-line stored outputs, current 15/23/24, then
        a delete or a move -- scroll 12426 -> 12048 with the frame
        ~1200 px below a 561 px viewport, stable). So each pass
        re-ensures the frame and chains ONE more zero-timer pass while
        a retry remains AND (the geometry moved during the pass -- the
        scroll value, the scroll maximum or the frame's mapped top-left
        -- OR the frame is out of view: its mapped top-left outside the
        viewport rect, a frame taller than the viewport counting as
        visible once its top is inside). Steady state stops after the
        first still pass, and the retry budget bounds the chain (no
        accumulation). Opening a notebook still lands at the page top:
        there the current cell is cell 0, whose top is inside the
        viewport at scroll 0, the geometry does not move, so the chain
        stops after one no-op pass."""
        index = self._current_cell_index
        if not (0 <= index < len(self._cell_widgets)):
            return
        frame = self._cell_widgets[index]['frame']
        viewport = self.scrollArea.viewport()
        bar = self.scrollArea.verticalScrollBar()
        top_before = frame.mapTo(viewport, QPoint(0, 0))
        scroll_before = bar.value()
        maximum_before = bar.maximum()
        self._anchor_internal_scroll += 1
        try:
            self.scrollArea.ensureWidgetVisible(frame, 20, 20)
        finally:
            self._anchor_internal_scroll -= 1
        top_after = frame.mapTo(viewport, QPoint(0, 0))
        moved = (top_after != top_before
                 or bar.value() != scroll_before
                 or bar.maximum() != maximum_before)
        visible = viewport.rect().contains(top_after)
        if retries > 0 and (moved or not visible):
            QTimer.singleShot(
                0, lambda _r=retries - 1:
                    self._settle_scroll_on_current_cell(_r))

    def _add_cell_widget(self, index):
        cell = self.document.get_cell(index)
        is_code = cell['cell_type'] == 'code'

        frame = _CellFrame()
        frame.setObjectName('cellFrame_{}'.format(index))

        bar = QFrame()
        bar.setObjectName('cell_bar')
        bar.setFixedWidth(3)

        header = QHBoxLayout()
        header.setSpacing(6)
        run_button = QPushButton('Run')
        run_button.setObjectName('run_button')
        run_button.setToolTip('Run this cell (Ctrl+Enter)')
        count_value = cell.get('execution_count')
        prompt_label = QLabel('In [{}]:'.format(count_value)
                              if count_value is not None else 'In [ ]:')
        prompt_label.setObjectName('cell_prompt')
        prompt_label.setFont(code_font())
        type_label = QLabel(cell['cell_type'].capitalize())
        type_label.setObjectName('cell_type_label')
        header.addWidget(run_button)
        header.addWidget(prompt_label)
        if not is_code:
            prompt_label.hide()
        header.addWidget(type_label)
        header.addStretch(1)
        # Round 11: the markdown cell's flip-back affordance sits past the
        # stretch, in the top-right corner. Markdown cells only -- raw cells
        # keep today's plain build (they run on the kernel, never render).
        edit_button = None
        if cell['cell_type'] == 'markdown':
            # Round 23: parented at construction -- the mode flip below
            # calls setVisible BEFORE the layouts attach to the frame, and
            # a setVisible(True) on a parentless widget realizes it as a
            # stray top-level window (one per rendered markdown cell per
            # load).
            edit_button = QPushButton('✎ Edit', frame)
            edit_button.setObjectName('markdown_edit_button')
            edit_button.setToolTip('Back to editing')
            edit_button.clicked.connect(
                lambda _checked=False, idx=index:
                self._on_markdown_edit_button_clicked(idx))
            header.addWidget(edit_button)

        # Round 23: both cell widgets are parented to the frame at
        # construction -- the markdown branch below flips their visibility
        # BEFORE body.addWidget parents them, and a setVisible(True) on a
        # parentless widget realizes it as a stray top-level window (one
        # per rendered markdown cell flashed on every notebook load).
        editor = NotebookCellEditor(frame)
        editor.document().setDefaultFont(code_font())
        editor.setPlainText(cell['source'])
        # A dfnb snippet drag onto this editor announces itself; the form
        # runs the picker (through the bridge client it owns) and inserts
        # the generated line at THIS editor's cursor.
        editor.snippetDropped.connect(
            lambda kind, ed=editor: self._on_snippet_dropped(ed, kind))
        # Round 22b: the editor's viewport resizes (its grow-with-content
        # height changes) arm the image-fit debounce too -- an editor
        # height change moves the cell frames just the same, so it must
        # delay the anchor's quiet pass the same way a browser's does.
        editor.viewport().installEventFilter(self._browser_resize_filter)
        highlighter = None
        if is_code:
            highlighter = PythonCellHighlighter(editor.document())
            highlighter.relex(editor.toPlainText())
            editor.textChanged.connect(
                lambda ed=editor, hl=highlighter:
                self._on_editor_text_changed(ed, hl))
        editor.document().documentLayout().documentSizeChanged.connect(
            lambda _size, ed=editor: self._on_editor_size_changed(ed))

        browser = QTextBrowser(frame)
        browser.setObjectName('cell_output')
        browser.setOpenExternalLinks(False)
        browser.setFont(code_font())
        # The browser renders at its FULL content height (the fit below),
        # the way a document does -- so it never scrolls vertically: the
        # notebook panel is the one vertical scroll area, and a nested
        # one here would fight it. Round 22: horizontal scrolling is gone
        # too (both policies AlwaysOff) -- a figure larger than the
        # viewport is SCALED DOWN to fit instead of scrolling (the
        # round-22 image fit below and at every content path); a browser
        # never scrolls in either direction. The cell's SOURCE EDITOR is
        # untouched by all of this: code editing wraps its lines (round
        # 9's WidgetWidth + AlwaysOff pair) and stays exactly as it is --
        # this round only changes how a cell's RENDERINGS behave.
        browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Round 22: the viewport resize filter -- one form-owned
        # instance shared by every cell browser -- arms the image-fit
        # debounce whenever this viewport changes size, so a window drag
        # re-scales the figures once per debounce window.
        browser.viewport().installEventFilter(self._browser_resize_filter)
        # The output browser grows with its content: the editor's
        # documentSizeChanged seam, mirrored. Connected BEFORE
        # any setHtml/setMarkdown below, so every content change rides it
        # -- the build-time stored outputs, the markdown preview, and the
        # later live chunks (_append_live_html) and authoritative
        # re-renders (_render_outputs) alike. Round 22: the same seam
        # also arms the image-fit debounce, so content that arrives
        # BEFORE the widget is laid out (or resized) is still caught by
        # the coalesced pass on the first real layout.
        browser.document().documentLayout().documentSizeChanged.connect(
            lambda _size, b=browser: self._on_browser_size_changed(b))
        if is_code:
            try:
                stored_html = render_outputs_html(cell.get('outputs') or [])
            except Exception as exc:
                # A foreign notebook's stored payload can break the
                # renderer (nbformat tolerates payload shapes this code
                # may never have seen). The cell must stay usable and the
                # failure must stay visible: one log record, then a red
                # note in the output browser -- built through the
                # renderer's own escaping, from a stream dict that can
                # never hit the failing path itself.
                _log.exception(
                    'Dragonfly Notebook: the stored outputs of code cell '
                    '%d could not be rendered; showing a failure note '
                    'instead.', index)
                stored_html = render_outputs_html(
                    [{'output_type': 'stream', 'name': 'stderr',
                      'text': 'the stored outputs could not be displayed: '
                              '{}: {}'.format(type(exc).__name__, exc)}])
            if stored_html:
                browser.setHtml(stored_html)
                # Round 22: the stored outputs' figures are scaled to the
                # available width at build time too -- a true no-op here
                # (the browser is not visible yet; the round-22-review
                # guard in available_image_width skips a pre-show bake
                # against the bogus ~98 px viewport) and the post-show
                # pass re-fits at the real width.
                self._fit_browser_images_to_width(browser)
            else:
                browser.hide()
        else:
            browser.setMarkdown(cell['source'])
            # Round 22: a markdown cell's embedded images (local paths or
            # data URLs) get the same uniform width fit as the outputs --
            # a no-op here (the browser is not visible yet, the guard
            # skips a pre-show bake), the post-show pass re-fits at the
            # real width.
            self._fit_browser_images_to_width(browser)
            # Round 11: the markdown cell's mode comes from the id-keyed
            # set -- a freshly added cell (its id joined the set before
            # this build) opens EDIT, a loaded one opens RENDERED (Jupyter
            # opens files rendered). The RAW shape above is untouched:
            # browser preview visible, editor visible, no edit button.
            if cell['cell_type'] == 'markdown':
                # Round 12: the rendered view is run-INERT -- the browser
                # consumes the run/advance/insert Enter chords outright
                # (the key filter below), so a stray Ctrl/Shift/Alt+Enter
                # while it holds the keyboard does nothing.
                key_filter = _RenderedMarkdownKeyFilter(browser)
                # Round 13: Shift+Enter on the rendered view advances to
                # the next editable cell (rendered markdowns are skipped)
                # instead of stalling.
                key_filter.skipForward.connect(lambda idx=index:
                                               self._on_rendered_skip_forward(idx))
                browser.installEventFilter(key_filter)
                in_edit_mode = cell['id'] in self._markdown_edit_ids
                editor.setVisible(in_edit_mode)
                browser.setVisible(not in_edit_mode)
                edit_button.setVisible(not in_edit_mode)

        # The elapsed-time footer: hidden until the cell's first run, then
        # live ('0:02.4') while it runs and pinned ('2.31 s') after.
        elapsed_label = QLabel()
        elapsed_label.setObjectName('cell_elapsed')
        elapsed_label.setFont(code_font())
        elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
        elapsed_label.hide()

        body = QVBoxLayout()
        body.setContentsMargins(4, 4, 4, 4)
        body.addLayout(header)
        body.addWidget(editor)
        body.addWidget(browser)
        body.addWidget(elapsed_label)
        # The left selection bar hugs the rounded left edge: first widget
        # of a 0-margin, 0-spacing HBox. Its color is flipped by
        # _refresh_cell_accent.
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(bar)
        outer.addLayout(body, 1)
        frame.setLayout(outer)

        # before the trailing stretch + end-of-list row
        self.cellsLayout.insertWidget(self.cellsLayout.count() - 2, frame)
        # The grow-with-content pass rides the event loop (the editor takes
        # its real height one zero-timer after the rebuild). Scheduled only
        # AFTER the frame is anchored into the form's layout: the round-20
        # containment path can abort this builder mid-way (a renderer that
        # raises), and a callback scheduled before the anchoring would then
        # fire on widgets whose owning frame was already collected -- an
        # unhandled exception inside a Qt timer callback, which PyQt6
        # answers with a fatal abort.
        QTimer.singleShot(0, lambda ed=editor: ed.resizeToContent())

        # ONE dispatcher per run button: while THIS cell runs the button is
        # the red Stop affordance (the click sets the stop flag), otherwise
        # it runs the cell.
        run_button.clicked.connect(lambda _checked=False, idx=index:
                                   self._on_run_button_clicked(idx))
        editor.runRequested.connect(lambda idx=index: self._run_cell_at(idx))
        editor.runAdvanceRequested.connect(
            lambda idx=index: self._run_cell_at(idx, advance=True))
        editor.runInsertRequested.connect(
            lambda idx=index: self._run_cell_at(idx, insert_after=True))
        editor.focusGained.connect(lambda idx=index:
                                   self._set_current_cell(idx))
        # Round 12: a press on the frame itself selects the cell (a
        # rendered markdown cell has no editor to click into).
        frame.frameClicked.connect(lambda idx=index:
                                   self._on_frame_clicked(idx))

        self._cell_widgets.insert(index, {
            'frame': frame, 'editor': editor, 'browser': browser,
            'run_button': run_button,
            'prompt_label': prompt_label,
            'type_label': type_label, 'highlighter': highlighter,
            'cell_bar': bar, 'elapsed_label': elapsed_label,
            'edit_button': edit_button})
        if cell['cell_type'] == 'markdown':
            # Round 12: the chrome half of the mode (run button, type
            # label, accent bar, the dfnb_rendered property the flat QSS
            # matches) comes from the same helper the two runtime flip
            # sites use -- the build branch above settled only the
            # editor/browser/edit-button visibility.
            self._set_markdown_chrome(
                self._cell_widgets[index],
                rendered=cell['id'] not in self._markdown_edit_ids)

    def _set_markdown_chrome(self, record, rendered):
        """The chrome half of the markdown mode flip (round 12) -- the ONE
        authority for it: a RENDERED cell hides its Run button, its type
        label and its accent bar, and carries the ``dfnb_rendered``
        property on frame AND browser so the stylesheet's flat-chrome
        rules blend both onto the page background (no border, no cell
        surface); EDIT mode restores the full chrome. The ✎ Edit button
        is NOT touched here -- its visibility is the round-11 mode code's
        own concern. Markdown cells only, never raw or code (they never
        get a mode flip). Property flips need an unpolish/polish for the
        QSS property selectors to re-match (the hover/current idiom)."""
        record['run_button'].setVisible(not rendered)
        record['type_label'].setVisible(not rendered)
        record['cell_bar'].setVisible(not rendered)
        for widget in (record['frame'], record['browser']):
            widget.setProperty('dfnb_rendered', rendered)
            _repolish(widget)

    def _sync_editors_to_document(self):
        """Copies every editor's current text into the document (structural
        changes, save, close). Marks the document dirty only when a text
        actually differs."""
        for index, record in enumerate(self._cell_widgets):
            if index >= self.document.cell_count():
                break
            source = record['editor'].toPlainText()
            if source != self.document.get_cell(index)['source']:
                self.document.set_cell_source(index, source)

    def _set_current_cell(self, index):
        self._current_cell_index = index
        self._refresh_cell_accent()

    def _refresh_cell_accent(self):
        """Restyles the current-cell accent across every cell: the left bar
        paints the steel-blue accent on the current cell, the frame border
        follows the 'current' property (the form stylesheet's rule).
        Property flips need an unpolish/polish for the QSS property
        selectors to re-match."""
        for index, record in enumerate(self._cell_widgets):
            is_current = index == self._current_cell_index
            frame = record['frame']
            if frame.property('current') != is_current:
                frame.setProperty('current', is_current)
                _repolish(frame)
            bar = record['cell_bar']
            bar_style = ('background: {}; border-top-left-radius: 6px; '
                         'border-bottom-left-radius: 6px;'
                         .format('#4F94CD' if is_current else 'transparent'))
            if bar.styleSheet() != bar_style:
                bar.setStyleSheet(bar_style)

    def _focus_cell(self, index):
        record = self._cell_widgets[index]
        self._set_current_cell(index)
        cell = self.document.get_cell(index)
        if cell['cell_type'] == 'markdown' and \
                cell['id'] not in self._markdown_edit_ids:
            # Round 12: a RENDERED markdown cell has no editor to take the
            # keyboard -- its browser owns the focus deterministically
            # (setFocus on a hidden widget is a deferred no-op, which is
            # exactly how focus used to stray to a neighbouring editor).
            record['browser'].setFocus()
        else:
            record['editor'].setFocus()
        self._anchor_internal_scroll += 1
        try:
            self.scrollArea.ensureWidgetVisible(record['frame'], 20, 20)
        finally:
            self._anchor_internal_scroll -= 1

    def _on_frame_clicked(self, index):
        """A press on a cell's frame made it the current cell (round 12).
        A RENDERED markdown cell additionally takes the keyboard focus on
        its browser -- a click on rendered text must never leave focus on
        a previous editor's stray caret. EDIT-mode and code cells only get
        the current-cell flip: their own widgets keep the existing focus
        path (clicking the editor focuses it; a focus steal here would
        yank the caret out of it)."""
        if not (0 <= index < len(self._cell_widgets)):
            return
        self._set_current_cell(index)
        cell = self.document.get_cell(index)
        if cell['cell_type'] == 'markdown' and \
                cell['id'] not in self._markdown_edit_ids:
            self._cell_widgets[index]['browser'].setFocus()

    def _on_editor_text_changed(self, editor, highlighter):
        if highlighter.is_rehighlighting():
            # Round 9: this textChanged was emitted by the highlighter's
            # own deferred rehighlight() (PyQt6 6.7.1 emits textChanged
            # from a bare rehighlight(), probed even for an empty
            # document). The text did not change: re-lexing the same text
            # would reschedule the relex forever (the emission would
            # schedule another), and flagging dirty here marked a freshly
            # launched notebook dirty before the user typed anything --
            # which made the round-9 drop onto a fresh notebook ask
            # 'save changes?' spuriously.
            return
        highlighter.relex(editor.toPlainText())
        editor.resizeToContent()
        if not self._rebuilding:
            self.document.is_dirty = True

    def _on_editor_size_changed(self, editor):
        editor.resizeToContent()

    def _on_browser_size_changed(self, browser):
        """The output browser's documentSizeChanged seam (round 9), and
        since round 22 also the arm point for the image-fit debounce:
        every content change (build-time render, live chunk, authoritative
        re-render, markdown preview) re-fits the height immediately and
        coalesces an image-fit pass behind the debounce timer. The
        scroll anchor itself is armed by the structural rebuilds (see
        _rebuild_cells) and by the passes that change a visible cell's
        images (see _fit_all_browser_images)."""
        self._fit_browser_to_content(browser)
        self._arm_browser_image_fit()

    def _arm_browser_image_fit(self):
        """Starts (restarts) the ONE single-shot debounce behind the
        image-fit pass: viewport resizes and content changes both land
        here, and only the QUIET window after the last arm runs the
        pass -- a window drag coalesces into one re-scale. Each arm
        also records whether the CURRENT frame was in view at that
        moment (the resize filter fires before the layout reacts, so
        this is where the user had the cell), consumed by the pass's
        promotion decision; see _fit_all_browser_images."""
        if self._current_frame_in_view():
            self._image_fit_anchor_saw_visible = True
        self._browser_image_fit_timer.start()

    def _current_frame_in_view(self):
        """Whether the CURRENT cell's frame intersects the panel
        viewport (top-left inside; a frame taller than the viewport
        counts as visible once its top is inside). Shared by the arm
        hook, the user-action hook and the debounced pass."""
        index = self._current_cell_index
        if not 0 <= index < len(self._cell_widgets):
            return False
        viewport = self.scrollArea.viewport()
        frame = self._cell_widgets[index]['frame']
        return viewport.rect().contains(frame.mapTo(viewport, QPoint(0, 0)))

    def _fit_browser_images_to_width(self, browser):
        """Scales the too-wide images of ONE cell browser to its current
        available width (round 22). The guard skips a not-usefully-laid-
        out browser (a viewport of 0 or 1 px, or -- round 22 review --
        a not-VISIBLE browser, whose fresh viewport reports a bogus
        ~98 px: a pre-show build-time call) -- the first real layout
        fires the documentSizeChanged seam, which arms the debounce,
        and the coalesced pass fits then. Returns True when any image
        format actually changed (the debounced pass uses that to decide
        whether its scroll anchor is needed)."""
        available = available_image_width(browser)
        if available is None:
            return False
        return bool(fit_document_images(browser.document(), available))

    def _fit_all_browser_images(self):
        """The debounced pass: re-runs the uniform image fit over EVERY
        cell browser (markdown cells included) with the CURRENT viewport
        widths. Idempotent by construction (unchanged images are
        skipped), so a pass over a settled notebook costs one walk per
        document and no layout churn.

        Round 22b, the scroll anchor: a window resize shrinks the
        figures ABOVE the current cell too, and the resulting height
        changes move the current frame inside the panel without moving
        the scroll value -- the round-21 contract (structural actions
        keep the viewport on the current cell) would silently break for
        the most common real case (stored matplotlib outputs). The
        anchor is a THREE-STATE machine (``_image_fit_anchor_state``):
        0 idle, 1 armed-by-rebuild, 2 active.

          * a pass that CHANGED an image promotes state 1 to 2 (the
            rebuild's figures were rescaled -- the viewport must be
            handed back to the current cell) and promotes 0 to 2 only
            when an arm since the last pass saw the frame in view (the
            user-resize case -- recorded at ARM time, because a resize's
            first wave can push the frame out during the 140 ms before
            the pass runs); either promotion records the current frame
            as in-view, because a rebuild's own settle is contractually
            landing on this cell and a resize promotion just saw it in
            view;
          * a QUIET pass (nothing changed) with state 2 runs
            ``_ensure_current_cell_visible`` (the round-21 margins).
            The frame is repaired when it is in view, or when it is out
            of view but the last KNOWN scroll position had it in view
            (content drift moved it out; the scrollbar never fired, so
            nobody scrolled). When it is out of view AND the recorded
            visibility is False, a real scroll put it there -- the
            user's decision -- and the anchor retires WITHOUT
            ensuring: a window drag must never yank the view.

        Why the ensure REPEATS instead of firing once (probed, 22b
        traces): in a freshly rebuilt figure-heavy layout the browsers'
        height fits AND the editors' grow-with-content passes keep
        landing over the following event-loop turns -- a data-URL
        figure decodes over several turns, and the storm's waves come
        in BURSTS separated by pauses LONGER than the 140 ms debounce.
        A single fixed ensure lands mid-storm: either immediately (a
        zero-timer, the frame drifts out afterwards) or in a lull where
        the frame is momentarily visible (the probed failing runs: a
        no-op ensure at scroll 3106 while the image pass and the next
        wave then moved the frame out, with the anchor already
        consumed). Even ONE stable quiet pass is not enough -- the
        lulls are exactly what fires it early -- so the anchor retires
        only after TWO consecutive STABLE quiet passes (each 140 ms
        past the last geometry change), re-arming itself between them;
        ``_IMAGE_FIT_ANCHOR_MAX_ENSURES`` caps the repairs (the
        round-21 retry-budget pattern) so a pathological layout cannot
        pin the view forever. Every repetition is the same single
        ``ensureWidgetVisible`` the round-21 settle uses; nothing new
        is scrolled when the layout is already right.

        The no-yank rule rests on ``_update_image_fit_anchor_in_view``:
        the scrollbar's actionTriggered/sliderMoved fire ONLY for
        user-driven input -- programmatic setValue (Qt's own clamp after
        a shrunken maximum, the form's own repositioning under
        ``_anchor_internal_scroll``) never does, and content drift fires
        nothing -- the exact boundary between 'the layout moved under a
        still viewport' (repairable) and 'the user scrolled away' (a
        user decision, never yanked)."""
        index = self._current_cell_index
        changed = False
        for record in self._cell_widgets:
            browser = record['browser']
            available = available_image_width(browser)
            if available is None:
                continue
            # The per-pass memo (round 22 review): a browser whose
            # document revision AND available width are unchanged since
            # its last fit needs no walk at all -- repeat passes and
            # anchor re-arms cost near zero on a text-only (or settled)
            # notebook, which is what keeps a 2000-cell load from paying
            # a full-document walk per pass. The width half carries a
            # 2 px threshold: sub-3-px viewport jitter (a scrollbar
            # margin rounding, a font-metric edge) must not re-fit.
            # BOUNDARY CASE, honestly unproven: a sustained oscillation
            # would need the total content height to sit within one
            # scrollbar-toggle's height delta so the panel scrollbar
            # keeps appearing and disappearing and the viewport width
            # keeps flipping past the threshold -- the 140 ms debounce
            # and this memo both damp it, but no pin demonstrates the
            # toggle cannot self-sustain.
            revision = browser.document().revision()
            memo = record.get('_image_fit_memo')
            if memo is not None and memo[0] == revision \
                    and abs(available - memo[1]) <= 2:
                continue
            if self._fit_browser_images_to_width(browser):
                changed = True
            # Store the POST-fit revision (probed: mergeCharFormat does
            # not bump it, so a settled image document stays memoized)
            # and the width the fit was APPLIED at -- a threshold-skipped
            # pass must not widen the memo's window a pixel at a time.
            record['_image_fit_memo'] = (browser.document().revision(),
                                         available)
        saw_visible = self._image_fit_anchor_saw_visible
        self._image_fit_anchor_saw_visible = False
        if changed:
            # Geometry is moving again: prior stability proves nothing.
            self._image_fit_anchor_stable_streak = 0
            if self._image_fit_anchor_state == 1 or (
                    saw_visible
                    and self._image_fit_anchor_state == 0):
                self._image_fit_anchor_state = 2
                self._image_fit_anchor_ensures = 0
                self._image_fit_anchor_stable_streak = 0
                # A rebuild's own settle is contractually landing on
                # this cell and a resize promotion just saw it in view:
                # either way the last known scroll position had it in
                # view. Only a later user action (a real scroll) may
                # revoke that.
                self._image_fit_anchor_in_view = True
        elif self._image_fit_anchor_state == 2:
            if not (0 <= index < len(self._cell_widgets)):
                self._image_fit_anchor_state = 0
            else:
                # The quiet pass: the layout held still for a full
                # debounce window -- repair (or confirm) the viewport.
                bar = self.scrollArea.verticalScrollBar()
                frame = self._cell_widgets[index]['frame']
                viewport = self.scrollArea.viewport()
                in_view = viewport.rect().contains(
                    frame.mapTo(viewport, QPoint(0, 0)))
                if not in_view and not self._image_fit_anchor_in_view:
                    # A real scroll (the user's decision) took the
                    # current cell away: retire, never yank it back.
                    self._image_fit_anchor_state = 0
                else:
                    top_before = frame.mapTo(viewport, QPoint(0, 0)).y()
                    scroll_before = bar.value()
                    self._ensure_current_cell_visible()
                    self._image_fit_anchor_ensures += 1
                    top_after = frame.mapTo(viewport, QPoint(0, 0)).y()
                    still_in_view = viewport.rect().contains(
                        frame.mapTo(viewport, QPoint(0, 0)))
                    stable = (still_in_view
                              and top_after == top_before
                              and bar.value() == scroll_before)
                    if stable:
                        self._image_fit_anchor_stable_streak += 1
                    else:
                        self._image_fit_anchor_stable_streak = 0
                    if self._image_fit_anchor_stable_streak < 2 and \
                            self._image_fit_anchor_ensures \
                            < _IMAGE_FIT_ANCHOR_MAX_ENSURES:
                        # Not confirmed stable yet (or the ensure is
                        # still repairing): one more quiet pass.
                        self._arm_browser_image_fit()
                    else:
                        self._image_fit_anchor_state = 0
        elif self._image_fit_anchor_state == 1:
            # A text-only rebuild: nothing to repair, disarm.
            self._image_fit_anchor_state = 0

    def _update_image_fit_anchor_in_view(self, *_args):
        """The scrollbar's user-action hook (round 22b): while the
        anchor is active, every USER scroll action (wheel, drag, keys
        -- actionTriggered/sliderMoved; programmatic setValue never
        fires these) refreshes whether the current frame was in view at
        that scroll position. Layout drift never fires them (moving
        content does not scroll), Qt's own clamp after a shrunken
        maximum is a programmatic setValue (not the user's decision --
        the rescale itself caused it, and the repair below is what
        answers it), and the form's OWN repositioning is flagged anyway
        through ``_anchor_internal_scroll``. So an unflagged action
        here is exactly 'the user scrolled away' (never yanked), as
        opposed to 'the layout moved under a still viewport'
        (repairable).

        The re-read is DEFERRED one event-loop turn (round 22 review):
        actionTriggered fires BEFORE the scrollbar value moves (probed:
        a wheel step's handler reads the pre-step value while the value
        lands right after), so reading the frame position inline would
        record the PRE-step visibility -- a single wheel step that
        carries the current cell off-screen would leave the stale True
        and the next quiet pass would yank the view back. At the
        deferred fire the action's move has landed, and the re-checks
        (still active, still not an internal repositioning) keep a
        queued zero-timer from acting on a world that moved on."""
        if self._image_fit_anchor_state != 2:
            return
        if self._anchor_internal_scroll:
            return
        QTimer.singleShot(0, self._apply_image_fit_anchor_in_view)

    def _apply_image_fit_anchor_in_view(self):
        """The deferred half of the user-action hook (round 22 review,
        see _update_image_fit_anchor_in_view): runs one event-loop turn
        after the user's scroll action, when the action's value change
        has landed. A user action that took the current cell away
        revokes the recorded visibility AND the arm-time saw-visible
        gate (a pending debounced pass must not re-promote against the
        user's decision) and retires the anchor immediately, so no
        scheduled quiet pass can yank the view back."""
        if self._image_fit_anchor_state != 2:
            return
        if self._anchor_internal_scroll:
            return
        if self._current_frame_in_view():
            self._image_fit_anchor_in_view = True
        else:
            self._image_fit_anchor_in_view = False
            self._image_fit_anchor_saw_visible = False
            self._image_fit_anchor_stable_streak = 0
            self._image_fit_anchor_state = 0

    def _ensure_current_cell_visible(self):
        """The scroll ensure behind the debounced image pass (round
        22b): re-lands the viewport on the current cell's frame with
        the same margins the round-21 settle uses. Runs at each quiet
        pass while the anchor is active, until one confirms the
        geometry stable (or the repetition cap engages -- see
        _fit_all_browser_images); reads the index at call time and
        no-ops when it is gone."""
        index = self._current_cell_index
        if 0 <= index < len(self._cell_widgets):
            self._anchor_internal_scroll += 1
            try:
                self.scrollArea.ensureWidgetVisible(
                    self._cell_widgets[index]['frame'], 20, 20)
            finally:
                self._anchor_internal_scroll -= 1

    def _fit_browser_to_content(self, browser):
        """Grows an output browser to its FULL content height.

        Round 9 -- the editor's resizeToContent treatment, mirrored for
        the per-cell output browser: QTextBrowser's sizeHint is a
        CONSTANT 256x192 no matter what the document holds, so a
        matplotlib figure clipped to a ~50 px band (only the x-axis
        strip visible) and a multi-line text output clipped mid-glyph
        with a scrollbar. document().size() IS the laid-out content
        height, images included (data-URL images enter the layout
        synchronously -- probed: 4 text lines -> 60.0 px, a 600x300
        figure -> 308.0 px). The fixed height is that document size plus
        the frame allowance. Round 21 drops the round-9 cap: the browser
        renders at its full content height, the way a document does --
        the notebook panel is the one vertical scroll area and a nested
        one inside a cell would fight it, so no output scrolls vertically
        however tall it is (the browser's vertical scrollbar policy is
        AlwaysOff; since round 22 horizontal is AlwaysOff too -- the
        round-22 image fit scales over-wide figures into the viewport
        instead). Full rendering has no
        user-facing cap below ~880k text lines (a height of
        16777215 px, Qt's QWIDGETSIZE_MAX): the ONE exception is Qt's
        own widget limit -- past it setFixedHeight
        silently clamps while the vertical policy stays AlwaysOff and
        the bottom of the output becomes unreachable. The far valve
        ``_MAX_FULL_HEIGHT`` (15000000 px, well below Qt's limit) never
        engages on a realistic output: when the fitted height would
        exceed it, the height is pinned at the ceiling AND that
        browser's vertical policy flips to AsNeeded, so even the
        extreme output stays scrollable and reachable; in every other
        case the fit leaves the policy at AlwaysOff (the normal
        contract: no nested vertical scrolling). Round 22 drops the
        horizontal-scrollbar allowance (the round-9 arithmetic that
        added one scrollbar-height when the laid-out document was wider
        than the viewport): with the horizontal policy AlwaysOff that
        bar can never come up, so the allowance is dead arithmetic --
        the height is content plus the frame fudge only (a document
        genuinely wider than the viewport can no longer scroll to its
        right edge; the over-wide figures that made that case real are
        scaled into the viewport by the round-22 image fit before it
        matters, and tables still clip rather than scroll -- the price
        of an unscrollable cell). The fit is idempotent: an unchanged
        fixed height does not re-enter the layout (probed: two
        consecutive
        fits, one height, zero signal churn after settling). The
        documentSizeChanged seam in _add_cell_widget (connected before
        any setHtml/setMarkdown) routes every content change here -- the
        build-time stored outputs, the live stream chunks, the
        authoritative re-render and the markdown preview. A document
        does not lay out until the widget is shown, and showing
        re-lays-out and fires the signal, so the fit lands on the
        post-show truth with no explicit fit at the show sites."""
        height = int(math.ceil(browser.document().size().height())) \
            + _OUTPUT_HEIGHT_FUDGE
        if height > _MAX_FULL_HEIGHT:
            # The far valve: Qt itself would clamp the fixed height at
            # QWIDGETSIZE_MAX while the vertical policy stays AlwaysOff,
            # making the bottom unreachable -- pin the height at the
            # ceiling and let THIS browser scroll vertically instead.
            height = _MAX_FULL_HEIGHT
            browser.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            browser.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setFixedHeight(height)

    # ---------------------------------------------------------- snippets

    def insert_snippet_into_current_cell(self, snippet):
        """A tools-toolbar insert (or a picker OK) lands here: the
        generated ``dfnb.<function>(...)`` line goes at the cursor of the
        focused cell. Returns False -- with a status note -- when no cell
        is focused."""
        index = self._current_cell_index
        if not (0 <= index < len(self._cell_widgets)):
            self._set_status('Focus a cell first, then insert the snippet.')
            return False
        self._cell_widgets[index]['editor'].insert_at_cursor(snippet)
        if not self._rebuilding:
            self.document.is_dirty = True
        return True

    def _on_snippet_action(self, kind):
        """The 'Insert snippet' menu: the kind's generated line with
        placeholder arguments, inserted at the focused cell's cursor."""
        snippet = generate_snippet(kind)
        if self.insert_snippet_into_current_cell(snippet):
            self._set_status('Snippet inserted: {}.'
                             .format(SNIPPET_SPECS[kind]['label']))

    def _on_picker_action(self, kind):
        """A picker menu entry: the kind's dialog (its object lists read
        THROUGH the bridge), and on OK the generated line at the cursor."""
        if self._running:
            self._set_status('A cell is running -- wait for it.')
            return
        params = snippet_parameters_dialog(kind, self.client, self)
        if params is None:
            return  # cancelled, or its ONE failure box is already shown
        snippet = generate_snippet(kind, **params)
        if self.insert_snippet_into_current_cell(snippet):
            self._set_status('Snippet inserted: {}.'
                             .format(SNIPPET_SPECS[kind]['label']))

    def _on_snippet_dropped(self, editor, kind):
        """A dfnb snippet drag landed on ONE cell's editor (round 8: the
        functions dock that originated most snippet drags is gone, but the
        ``application/x-dfnb-snippet`` mime contract stays for OS-level
        drags). The kind's picker runs THROUGH the bridge client this form
        owns and the generated line lands at THIS editor's cursor -- the
        drop target, not the focused cell (the round-4 semantics)."""
        if self._running:
            self._set_status('A cell is running -- wait for it.')
            return
        params = snippet_parameters_dialog(kind, self.client, self)
        if params is None:
            return  # cancelled, or its ONE failure box is already shown
        editor.insert_at_cursor(generate_snippet(kind, **params))
        if not self._rebuilding:
            self.document.is_dirty = True
        self._set_status('Snippet inserted: {}.'
                         .format(SNIPPET_SPECS[kind]['label']))

    # ------------------------------------------------------ environments

    def _refresh_env_list(self):
        """Rebuilds the environment combo: the embedded Python entry,
        every discovered environment (sorted), a separator, and the two
        administrative entries ('+ New Environment', '- Delete
        Environment'). The fill is programmatic with signals blocked
        (the selection handler is for USER picks only); the current
        selection is restored afterwards, so a refresh cannot silently
        switch the notebook's environment."""
        combo = self._env_combo
        names = notebook_envs.list_environments(self._envs_root)
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(notebook_envs.EMBEDDED_LABEL, None)
            for name in names:
                combo.addItem(name, name)
            if names:
                combo.insertSeparator(combo.count())
            combo.addItem(notebook_envs.NEW_ENV_LABEL,
                           notebook_envs.NEW_ENV_SENTINEL)
            combo.addItem(notebook_envs.DELETE_ENV_LABEL,
                          notebook_envs.DELETE_ENV_SENTINEL)
            index = 0
            if self._selected_environment is not None:
                found = combo.findData(self._selected_environment)
                if found >= 0:
                    index = found
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)
        self._update_env_controls()

    def _revert_env_combo(self):
        """Puts the combo back on the current selection (after a
        cancelled creation or a refused switch), silently."""
        combo = self._env_combo
        target = 0
        if self._selected_environment is not None:
            found = combo.findData(self._selected_environment)
            if found >= 0:
                target = found
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(target)
        finally:
            combo.blockSignals(False)
        self._update_env_controls()

    def _on_env_selected(self, index):
        """The combo's handler for USER picks: '+ New Environment' opens
        the creation flow and '- Delete Environment' the delete flow
        (a cancel reverts the combo); any environment (or the embedded
        Python) switches this notebook to it, with one confirmation when
        a kernel has to be shut down for the switch. Refused while a cell
        runs (the combo is disabled then, but the guard keeps the handler
        honest for programmatic changes)."""
        if self._running:
            self._set_status('A cell is running; wait for it.')
            self._revert_env_combo()
            return
        data = self._env_combo.itemData(index)
        if data == notebook_envs.NEW_ENV_SENTINEL:
            app = QApplication.instance()
            if app is not None and app.property('running_in_test'):
                _log.info('Dragonfly Notebook: the new-environment dialog '
                          'is suppressed under test (a programmatic combo '
                          'pick reached the handler).')
                self._revert_env_combo()
                return
            self._create_environment_dialog()
            return  # the creation flow ends by selecting or reverting
        if data == notebook_envs.DELETE_ENV_SENTINEL:
            app = QApplication.instance()
            if app is not None and app.property('running_in_test'):
                _log.info('Dragonfly Notebook: the delete-environment '
                          'dialog is suppressed under test (a '
                          'programmatic combo pick reached the handler).')
                self._revert_env_combo()
                return
            self._delete_environment_dialog()
            return  # the delete flow ends by refreshing or reverting
        name = data  # None = the embedded Python
        if name == self._selected_environment:
            return
        if not self._confirm_environment_switch(name):
            self._revert_env_combo()
            return
        self._apply_environment(name, mark_dirty=True)

    def _confirm_environment_switch(self, name):
        """One box before a switch that must shut a started kernel down
        (the in-process shell cannot re-point its imports); under the
        suite seam (running_in_test) the answer is a logged yes so the
        switch pins run headless."""
        if not self.kernel.is_started:
            return True
        target = (notebook_envs.EMBEDDED_LABEL if name is None
                  else "the environment '{}'".format(name))
        message = ('Switching to {} restarts the kernel: the namespace is '
                   'wiped (the notebook and its cells are not touched). '
                   'Continue?'.format(target))
        app = QApplication.instance()
        if app is None or app.property('running_in_test'):
            _log.info('Dragonfly Notebook: environment-switch confirm '
                      'auto-accepted under test: %s', message)
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle('Dragonfly Notebook')
        box.setObjectName('dfnb_message')
        box.setStyleSheet(_DIALOG_STYLESHEET)
        box.setText(message)
        switch_button = box.addButton('Switch and restart kernel',
                                      QMessageBox.ButtonRole.YesRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(switch_button)
        box.exec()
        return box.clickedButton() is switch_button

    def _apply_environment(self, name, mark_dirty=True):
        """The ONE environment-switch authority: the selection, the
        document's metadata (the notebook remembers its environment
        across saves), the kernel's next activation, the combo, the
        status label and the pip button rule all follow. ``name`` is
        the environment name, or None for the embedded Python. A
        started kernel on a DIFFERENT environment is shut down (the
        confirm happened in the caller for user switches; a document
        load or a new notebook does it silently); the next run
        lazy-starts on the new environment."""
        self._selected_environment = name
        if mark_dirty:
            self.document.is_dirty = True
        if name is None:
            self.document.metadata.pop('dfnb_environment', None)
        else:
            self.document.metadata['dfnb_environment'] = name
        venv = (notebook_envs.env_dir(self._envs_root, name)
                if name is not None else None)
        if self.kernel.is_started and \
                self.kernel.active_environment != venv:
            self.kernel.shutdown()
        self.kernel.set_environment(venv)
        combo = self._env_combo
        combo.blockSignals(True)
        try:
            target = 0 if name is None else combo.findData(name)
            if target < 0:
                target = 0
            combo.setCurrentIndex(target)
        finally:
            combo.blockSignals(False)
        self._update_env_label()
        self._update_env_controls()

    def _update_env_label(self):
        """The status strip's environment name."""
        self._env_label.setText('env: {}'.format(
            notebook_envs.EMBEDDED_LABEL
            if self._selected_environment is None
            else self._selected_environment))

    def _update_env_controls(self):
        """The environment controls' rule, in one place. The combo
        disables while a cell runs (a switch mid-run would race the
        worker) or while a venv/pip child of this window streams. The
        pip button enables while a CUSTOM environment is selected and
        nothing of this window is running -- round 18 dropped the
        in-use-kernel condition from the disable set: a cell run
        lazy-starts the kernel and it STAYS started, so the old rule
        locked the button permanently after the first cell and a kernel
        restart (which keeps the lock by design, the namespace wipe is
        in place) could never clear it. The kernel hold surfaces as a
        tooltip note instead: a running kernel keeps its DLLs loaded,
        so a pip command that replaces them can fail with a Windows
        file-in-use error (the streaming dialog shows pip's own
        diagnostic when it does; restarting the kernel first is the
        clean path). The tooltip always says which rule currently
        holds."""
        name = self._selected_environment
        busy = self._running or self._env_install_running
        self._env_combo.setEnabled(not busy)
        reasons = []
        if name is None:
            reasons.append('select a custom environment first (the '
                            'embedded Python is not pip-installable)')
        if self._running:
            reasons.append('a cell is running')
        if self._env_install_running:
            reasons.append('an environment operation is already running')
        self._pip_button.setEnabled(not reasons)
        if reasons:
            self._pip_button.setToolTip('pip: {}'.format(
                '; '.join(reasons)))
        else:
            tooltip = ("run a pip command in the environment '{}' "
                       '(with its own pip)'.format(name))
            if notebook_envs.is_environment_in_use(
                    notebook_envs.env_dir(self._envs_root, name)):
                tooltip += ('. Note: a kernel is running on this '
                            'environment and holds its DLLs loaded; a '
                            'pip command that replaces them can fail '
                            'with a Windows file-in-use error (restart '
                            'the kernel first if that happens)')
            self._pip_button.setToolTip(tooltip)

    def _on_kernel_stopped(self):
        """The environment-switch seam landed (or the kernel was shut
        down): the in-use lock is free, so the pip rule re-checks."""
        self._update_env_controls()

    def _stop_kernel_and_wait(self, timeout_s=30.0):
        """Stops THIS window's kernel and waits (bounded) for the worker's
        teardown to land -- round 18's delete-with-running-kernel seam,
        the same shutdown the environment switch uses. ``shutdown()``
        only QUEUES the teardown on the worker thread (the child quits
        inside a bounded wait, the in-use lock frees inside the
        teardown, then ``kernel_stopped`` announces it), so the delete
        flow pumps the GUI thread's own event delivery in a bounded
        ``QEventLoop`` until the announcement arrives. Returns True
        when the kernel is stopped when the wait ends; a timeout leaves
        the caller to the refusal path (the lock probe decides)."""
        if not self.kernel.is_started:
            return True
        loop = QEventLoop()
        stopped = QTimer()
        stopped.setSingleShot(True)
        stopped.timeout.connect(loop.quit)
        self.kernel.kernel_stopped.connect(loop.quit)
        try:
            stopped.start(int(timeout_s * 1000))
            self.kernel.shutdown()
            loop.exec()
        finally:
            stopped.stop()
            try:
                self.kernel.kernel_stopped.disconnect(loop.quit)
            except TypeError:
                pass  # already disconnected (the loop timed out first)
        return not self.kernel.is_started

    def _env_run_dialog(self, title, intro_notes, argv, result_note):
        """One streaming environment run's dialog, with the phase text
        the run itself does not print (``python -m venv`` is silent for
        most of its ~40 s run, so the notes are what tells the user that
        things are happening; that phase text is the round-16
        addition). The intro notes
        land BEFORE the child starts, the exit-code note when the run
        ends. The caller owns the modal ``exec()``; the headless tier-3
        gate drives the returned dialog directly through ``start`` + the
        event pump, never ``exec()``. Returns None when the child could
        not be spawned at all (the OSError :func:`notebook_envs.popen`
        raises, e.g. the venv's interpreter deleted between the
        selection and the click): the caller's ``dialog is None``
        branch owns the status line and the one failure box, so the
        exception's text rides there on ``_env_spawn_error`` instead of
        being lost with the discarded dialog. One catch here covers the
        creation, the pip run and every future environment run, because
        they all flow through this helper."""
        dialog = _EnvProcessDialog(title, self)
        for text in intro_notes:
            dialog.note(text)
        dialog.run_done.connect(
            lambda code: dialog.note(result_note(code)))
        self._env_spawn_error = None
        try:
            dialog.start(argv)
        except OSError as exc:
            self._env_spawn_error = str(exc)
            return None
        return dialog

    def _creation_run_dialog(self, name, venv):
        """The creation run's dialog: title, the two phase notes (what
        the silent venv module is about to do, and for roughly how long),
        the venv argv, and the exit-code note that ends the
        transcript."""
        return self._env_run_dialog(
            "Creating environment '{}'...".format(name),
            ["Creating the virtual environment '{}' "
             '(python -m venv).'.format(name),
             'This step prints nothing on its own: the interpreter is '
             'copied and pip is bootstrapped into the new environment '
             'first (about a minute on this machine). The exit code at '
             'the end is the success signal.'],
            notebook_envs.venv_creation_command(venv),
            lambda code: (
                'The environment was created (exit code {}).'.format(code)
                if code == 0 else
                'The creation stopped with exit code {}.'.format(code)))

    def _pip_run_dialog(self, name, venv, command):
        """The pip run's dialog: title, the one-line intro naming the
        command as typed, the venv's own pip argv
        (:func:`notebook_envs.pip_command` normalizes the launcher
        spellings and the aliases), and the exit-code note."""
        return self._env_run_dialog(
            "pip in '{}'...".format(name),
            ["Running the environment's own pip: {}.".format(command)],
            notebook_envs.pip_command(venv, command),
            lambda code: (
                'The pip command finished (exit code {}).'.format(code)
                if code == 0 else
                'The pip command stopped with exit code {}.'.format(code)))

    def _base_install_run_dialog(self, name, venv, packages=None):
        """The base-package install run's dialog (the second half of a
        creation, round 17: a fresh environment gets numpy and pandas,
        the minimum the notebook's own data paths need). ``packages``
        defaults to the notebook contract; the tier-3 gate overrides it
        with an offline wheel so the pin needs no network."""
        if packages is None:
            packages = list(notebook_envs.BASE_PACKAGES)
        return self._env_run_dialog(
            "Installing the base packages into '{}'...".format(name),
            ['Installing the base packages the notebook needs ({}): '
             'numpy arrays carry the volumes, pandas DataFrames carry '
             'the measured properties.'.format(', '.join(packages)),
             'This downloads them from PyPI the first time and can take '
             'a few minutes; later environments reuse pip\'s download '
             'cache. The exit code at the end is the success signal.'],
            notebook_envs.pip_install_command(venv, packages),
            lambda code: (
                'The base packages were installed (exit code {}).'
                .format(code)
                if code == 0 else
                'The base package install stopped with exit code {}.'
                .format(code)))

    def _create_environment_dialog(self):
        """The '+ New Environment' flow: the name dialog, then the
        streaming creation. A cancel (or a construction failure) reverts
        the combo; a successful creation selects the new environment."""
        name = new_environment_dialog(self._envs_root, self)
        if not name:
            self._revert_env_combo()
            return
        self._create_environment(name)

    def _create_environment(self, name):
        """Creates one environment: the ~40 s no-window venv creation
        streamed into a modal dialog, then (round 17) the base-package
        install (numpy, pandas) streamed the same way, then the
        selection follows. A failed or cancelled CREATION removes the
        partial venv and keeps the previous selection; a failed base
        install keeps the (perfectly valid) environment and says how to
        retry. Both runs stream with ``_env_install_running`` set, so
        the pip button and the combo are locked from the first dialog
        to the last."""
        try:
            notebook_envs.validate_name(name, self._envs_root)
        except ValueError as exc:
            _show_message(self, str(exc))
            self._revert_env_combo()
            return
        venv = notebook_envs.env_dir(self._envs_root, name)
        self._env_install_running = True
        self._update_env_controls()
        dialog = None
        base_dialog = None
        try:
            dialog = self._creation_run_dialog(name, venv)
            if dialog is not None:
                dialog.exec()
                if dialog.exit_code == 0:
                    # round 17: the runtime DLL the child's sqlite3 needs
                    # (instant, idempotent), then the base packages
                    notebook_envs.ensure_runtime_dlls(venv)
                    base_dialog = self._base_install_run_dialog(name, venv)
                    if base_dialog is not None:
                        base_dialog.exec()
        finally:
            # a spawn failure must not wedge the controls for the rest
            # of the window's life: the flag drops whatever happened (a
            # spawn failure makes the run helper return None, and the
            # branch below reports it as the failed run it promised)
            self._env_install_running = False
        if dialog is None:
            shutil.rmtree(venv, ignore_errors=True)
            self._set_status("The environment '{}' was not created: the "
                             'process could not start.'.format(name))
            _show_message(
                self, "The environment '{}' could not be created: the "
                'process could not start ({}).'.format(
                    name, self._env_spawn_error))
            self._refresh_env_list()
            return
        if dialog.exit_code != 0:
            # a half-created venv is not an environment: remove it so
            # the name is free again (and discovery never offers it)
            shutil.rmtree(venv, ignore_errors=True)
            detail = ('cancelled' if dialog.was_cancelled
                      else 'the creation failed (exit {})'.format(
                          dialog.exit_code))
            self._set_status("The environment '{}' was not created: {}."
                             .format(name, detail))
            if not dialog.was_cancelled:
                _show_message(
                    self, "The environment '{}' could not be created:\n{}"
                    .format(name,
                            '\n'.join(dialog.transcript[-12:])
                            or 'no output'))
        elif base_dialog is None or base_dialog.exit_code != 0:
            # the venv exists and runs; only its base packages are
            # missing. Keep it, keep the name, say how to retry.
            if base_dialog is None:
                detail = 'the install process could not start'
            elif base_dialog.was_cancelled:
                detail = 'the install was cancelled'
            else:
                detail = 'exit code {}'.format(base_dialog.exit_code)
            self._set_status(
                "Environment '{}' was created, but its base packages "
                '({}) did not finish installing ({}). Retry with the pip '
                'button.'.format(
                    name, ', '.join(notebook_envs.BASE_PACKAGES), detail))
        else:
            self._set_status("Environment '{}' created with the base "
                             'packages installed. The kernel runs on it; '
                             'the pip button installs into it.'.format(name))
        self._refresh_env_list()
        if dialog.exit_code == 0:
            self._apply_environment(name, mark_dirty=True)

    def _delete_environment_dialog(self):
        """The '- Delete Environment' flow: pick one of the existing
        environments (round 18: the styled ``DeleteEnvironmentDialog``
        list, not the plain ``QInputDialog`` that ignored the plugin's
        dialog stylesheet), confirm the removal (the disk loss is
        permanent), delete it. An empty list or a cancel just reverts
        the combo (the entry is a door, not a state)."""
        names = notebook_envs.list_environments(self._envs_root)
        if not names:
            self._set_status('There are no custom environments to delete.')
            self._revert_env_combo()
            return
        name = delete_environment_dialog(names, self)
        if not name:
            self._revert_env_combo()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle('Dragonfly Notebook')
        box.setObjectName('dfnb_message')
        box.setStyleSheet(_DIALOG_STYLESHEET)
        box.setText("Delete the environment '{}'? Its interpreter and "
                    'every installed package are removed from disk. '
                    'This cannot be undone.'.format(name))
        delete_button = box.addButton('Delete environment',
                                      QMessageBox.ButtonRole.YesRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(delete_button)
        box.exec()
        if box.clickedButton() is not delete_button:
            self._revert_env_combo()
            return
        self._delete_environment(name)

    def _delete_environment(self, name):
        """Deletes one environment from disk (the confirm already
        happened in the dialog flow; the tier-3 gate calls this directly
        with its own throwaway environment). Round 18: a kernel of THIS
        WINDOW running on the deleted environment is STOPPED first (the
        same seam the environment switch uses: the queued shutdown tears
        the environment child down inside its bounded wait and frees the
        in-use lock, and the delete flow waits for the announcement) --
        the old unconditional refusal made deletion impossible after
        running any cell, the common case. The refusal remains only for
        holds this window cannot safely stop: another notebook window's
        kernel (the OS lock) or an environment operation of this window
        still in flight -- the message says which. Deleting the SELECTED
        environment silently switches this notebook back to the embedded
        Python (its kernel was just stopped, so the switch never has to
        shut one down)."""
        if self._running:
            self._set_status('A cell is running; wait for it (or press '
                             'its Stop button) before deleting an '
                             'environment.')
            self._revert_env_combo()
            return
        if self._env_install_running:
            self._set_status('An environment operation is already '
                             'running; wait for it before deleting an '
                             'environment.')
            self._revert_env_combo()
            return
        venv = notebook_envs.env_dir(self._envs_root, name)
        if self.kernel.is_started and \
                self.kernel.active_environment == venv:
            self._stop_kernel_and_wait()
        if notebook_envs.is_environment_in_use(venv):
            self._set_status(
                "The environment '{}' is in use by another notebook "
                "window's kernel. Switch that window to the embedded "
                'Python, or close it, and delete again.'.format(name))
            self._revert_env_combo()
            return
        try:
            shutil.rmtree(venv)
        except OSError as exc:
            self._set_status("The environment '{}' could not be deleted: "
                             '{}'.format(name, exc))
            self._revert_env_combo()
            return
        if self._selected_environment == name:
            self._apply_environment(None, mark_dirty=True)
        self._set_status("The environment '{}' was deleted.".format(name))
        self._refresh_env_list()

    def _on_pip_button(self):
        """The pip button (round 17: pip runs ONLY here, never from
        cells): the full-command dialog, then the venv's own pip
        streamed into a modal dialog. The button's rule already
        guaranteed a custom, idle environment when it was clickable
        (round 18: a running kernel no longer blocks the click -- the
        tooltip's note names the file-in-use risk instead)."""
        name = self._selected_environment
        if name is None or self._running or self._env_install_running:
            self._update_env_controls()
            return
        command = pip_command_text(name, self)
        if not command:
            return
        venv = notebook_envs.env_dir(self._envs_root, name)
        self._env_install_running = True
        self._update_env_controls()
        dialog = None
        try:
            dialog = self._pip_run_dialog(name, venv, command)
            if dialog is not None:
                dialog.exec()
        except ValueError as exc:
            # a command that is only launcher spellings (the dialog's
            # validation normally refuses it): a status note, not a
            # crash out of the slot -- the flag drops BEFORE the
            # re-render so the button rule reads the real state
            self._env_install_running = False
            self._set_status('The pip command could not start: '
                             '{}.'.format(exc))
            self._update_env_controls()
            return
        finally:
            # the same spawn-failure guarantee as the creation run (a
            # spawn failure makes the run helper return None, and the
            # branch below reports it)
            self._env_install_running = False
        if dialog is None:
            self._set_status("The pip command in '{}' did not start."
                             .format(name))
            _show_message(
                self, "The pip command in '{}' did not start: the "
                'process could not start ({}).'.format(
                    name, self._env_spawn_error))
            self._update_env_controls()
            return
        if dialog.exit_code == 0:
            self._set_status("Ran {} in '{}'.".format(command, name))
        else:
            detail = ('cancelled' if dialog.was_cancelled
                      else 'pip failed (exit {})'.format(dialog.exit_code))
            self._set_status("The pip command in '{}' did not finish: "
                             '{}.'.format(name, detail))
            if not dialog.was_cancelled:
                _show_message(
                    self, "The pip command in '{}' failed:\n{}"
                    .format(name,
                            '\n'.join(dialog.transcript[-12:])
                            or 'no output'))
        self._update_env_controls()

    # --------------------------------------------------------------- run

    def _guard_idle(self):
        """False (with a status note) while a cell runs. Round 8 made cell
        runs ASYNCHRONOUS, so every structural action now needs this guard
        -- a rebuild mid-run would tear down the running cell's widgets,
        which the synchronous rounds could not race (the GUI thread was
        busy inside the run)."""
        if self._running:
            self._set_status('A cell is running -- wait for it (or press '
                             'its Stop button).')
            return False
        return True

    def _run_cell_at(self, index, advance=False, insert_after=False,
                     run_total=None):
        """STARTS one cell on the kernel's worker thread and returns
        immediately (round 8: asynchronous -- the GUI thread is free while
        the cell runs). Outputs arrive through the kernel signals and grow
        the running cell's output browser live; ``_on_run_finished`` does
        the bookkeeping (record, re-render, elapsed pin, navigation, Run
        All chaining). Returns True when a code run was started, False for
        a markdown cell (previewed inline instead -- and run-INERT once
        rendered, round 12), a completely empty code cell (a full no-op,
        round 24: nothing starts, nothing is synced, nothing navigates)
        or a refused run.
        ``run_total`` (Run All only) labels the status 'cell N of M'."""
        if self._running:
            self._set_status('A cell is already running -- wait for it.')
            return False
        if not (0 <= index < self.document.cell_count()):
            return False
        record = self._cell_widgets[index]
        cell = self.document.get_cell(index)
        if cell['cell_type'] == 'markdown' and \
                cell['id'] not in self._markdown_edit_ids:
            # Round 12: a RENDERED markdown cell is run-INERT -- only the
            # ✎ Edit flip (which re-enters the EDIT set) makes it runnable
            # again; a repeat run is a full no-op (no re-render, no source
            # touch, no dirty flag). Run All already skips markdown.
            return False
        code = record['editor'].toPlainText()
        if cell['cell_type'] == 'code' and not code.strip():
            # Round 24: a COMPLETELY empty code cell (whitespace-only
            # included) is a COMPLETE NO-OP run: no kernel start, no
            # source sync (the document must not turn dirty because of
            # it), no execution count, no outputs, no status change, no
            # navigation -- an advance or insert-after on an empty cell
            # neither appends nor inserts, and no rebuild moves the
            # scroll. Inside a Run All chain the chain must not stall:
            # hop to the next non-empty code cell (or finish the chain)
            # WITHOUT counting the skipped cell as run.
            if run_total and self._run_all_active:
                self._advance_run_all(index)
            return False
        self.document.set_cell_source(index, code)

        if cell['cell_type'] == 'markdown':
            record['browser'].setMarkdown(code)
            # Round 22, review: NO direct fit here -- at this instant the
            # panel scrollbar may still be up (the replaced content's
            # height has not shrunk yet), so the viewport is a few px
            # narrow and a direct fit would scale the images wide of the
            # settled width, then the debounced pass would widen them
            # again ~140 ms later (a visible pop). Arm the debounced pass
            # instead: it fits ONCE, at the settled viewport width, after
            # the layout (and the panel scrollbar) has settled.
            self._arm_browser_image_fit()
            record['browser'].show()
            # Round 11: running a markdown cell swaps the editor for the
            # rendered view -- the id leaves the EDIT set, so the mode
            # survives a rebuild as RENDERED, and the edit button waits in
            # the header to flip back. The raw-cell branch below is
            # untouched (raw runs on the kernel).
            record['editor'].hide()
            record['edit_button'].show()
            self._markdown_edit_ids.discard(cell['id'])
            # Round 12: the flat chrome follows the mode, and the rendered
            # view owns its own focus deterministically -- Qt never
            # relocates it onto a neighbouring editor.
            self._set_markdown_chrome(record, rendered=True)
            record['browser'].setFocus()
            if advance or insert_after:
                self._navigate_after_run(index, insert_after)
            return False

        starting = not self.kernel.is_started
        self._running = True
        self._running_cell_index = index
        self._run_started_at = time.monotonic()
        self._run_advance = advance
        self._run_insert_after = insert_after
        record['elapsed_label'].setText(format_elapsed_live(0.0))
        self._set_running_ui(True)
        self.busy_bar.show()
        self._live_timer.start()
        self._smoke_ticks = 0
        if _SMOKE:
            print('DFNB-BUSY-BAR textVisible={} range={}-{}'.format(
                self.busy_bar.isTextVisible(), self.busy_bar.minimum(),
                self.busy_bar.maximum()), flush=True)
        run_label = ('Running cell {} of {}...'.format(index + 1, run_total)
                     if run_total
                     else 'Running cell {}...'.format(index + 1))
        self._set_status('Starting the kernel...' if starting
                         else run_label)
        self.kernel.run_cell(code)
        return True

    def _on_run_button_clicked(self, index):
        """The per-cell button's ONE dispatcher: while THIS cell runs the
        button is the red Stop affordance -- the click sets ONLY
        ``kernel.stop_requested`` (the heartbeat tracer on the worker
        thread arms the interrupt at the cell's next Python line; arming
        it from a GUI-thread Qt slot would raise the interrupt inside Qt
        code, which is fatal). Otherwise the button runs the cell."""
        if self._running and index == self._running_cell_index:
            self.kernel.request_stop()
            self._set_status('Stopping the running cell...')
            return
        self._run_cell_at(index)

    def _on_markdown_edit_button_clicked(self, index):
        """The markdown cell's '✎ Edit' button (round 11): back from the
        rendered view into the source editor -- the id re-enters the EDIT
        set, the editor shows, the rendered browser and the button itself
        hide. Round 12: the flip also restores the full cell chrome (the
        one authority is ``_set_markdown_chrome``) and hands the keyboard
        to the editor -- the cell is fully runnable again, and the focus
        never lingers on the inert browser. The flip only touches
        visibility otherwise (a mid-run toggle would be harmless), but the
        button still follows the run-disable sweep like every other
        run-adjacent affordance."""
        if not (0 <= index < len(self._cell_widgets)):
            return
        record = self._cell_widgets[index]
        self._markdown_edit_ids.add(self.document.get_cell(index)['id'])
        record['editor'].show()
        record['browser'].hide()
        record['edit_button'].hide()
        self._set_markdown_chrome(record, rendered=False)
        record['editor'].setFocus()

    def _flip_run_button(self, record, stop_mode):
        """One run button between its two looks: 'Run' (steel-blue,
        objectName run_button) and 'Stop' (red, objectName cancel_button --
        the stylesheet's cancel style doubles as the Stop look). The
        objectName flip needs an unpolish/polish for the QSS selectors to
        re-match."""
        button = record['run_button']
        new_name = 'cancel_button' if stop_mode else 'run_button'
        new_text = 'Stop' if stop_mode else 'Run'
        new_tooltip = ('Stop this cell (interrupt it at the next Python '
                       'line)' if stop_mode
                       else 'Run this cell (Ctrl+Enter)')
        if button.objectName() != new_name:
            button.setObjectName(new_name)
            _repolish(button)
        if button.text() != new_text:
            button.setText(new_text)
        if button.toolTip() != new_tooltip:
            button.setToolTip(new_tooltip)

    def _set_running_ui(self, running):
        """Flips every run-adjacent affordance for a run start/end: the
        toolbar, the end-of-list row, every cell's Run button -- the
        RUNNING cell's becomes the red Stop button and the rest disable.
        The GUI thread is free mid-run, so ordinary event-loop repaints
        make the flips visible (the round-7 repaint() workaround is gone
        with the dialog)."""
        for name, button in self._toolbar_buttons.items():
            button.setEnabled(not running)
        for button in self._end_row_buttons:
            button.setEnabled(not running)
        for index, record in enumerate(self._cell_widgets):
            is_running_cell = running and index == self._running_cell_index
            self._flip_run_button(record, stop_mode=is_running_cell)
            record['run_button'].setEnabled(is_running_cell or not running)
            # Round 11: the markdown flip-back button joins the same sweep
            # (raw/code cells carry no edit button at all).
            edit_button = record.get('edit_button')
            if edit_button is not None:
                edit_button.setEnabled(not running)
        # Round 16: the environment controls re-evaluate (the pip
        # button's in-use probe must run again now the run is over;
        # the combo locks while a cell runs).
        self._update_env_controls()

    def _on_live_tick(self):
        """One live-timer tick while a cell runs: updates the running
        cell's elapsed footer ('0:02.4'). Timer events deliver mid-run
        because the GUI thread is free -- this tick IS the GUI
        responsiveness evidence (the DFNB_SMOKE print makes it observable
        to the tier-3 spawn test)."""
        if not self._running:
            return
        index = self._running_cell_index
        if not (0 <= index < len(self._cell_widgets)):
            return
        elapsed = time.monotonic() - self._run_started_at
        label = self._cell_widgets[index]['elapsed_label']
        text = format_elapsed_live(elapsed)
        if label.text() != text:
            label.setText(text)
        label.show()
        if _SMOKE:
            self._smoke_ticks += 1
            print('DFNB-TICKS {}'.format(self._smoke_ticks), flush=True)

    def _running_record(self):
        index = self._running_cell_index
        if 0 <= index < len(self._cell_widgets):
            return self._cell_widgets[index]
        return None

    def _append_live_html(self, chunk):
        """Grows the RUNNING cell's output browser by one live chunk."""
        record = self._running_record()
        if record is None:
            return
        browser = record['browser']
        cursor = browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(chunk)
        browser.setTextCursor(cursor)
        browser.show()

    def _on_stream_arrived(self, name, text):
        self._append_live_html(render_stream_chunk(name, text))

    def _on_display_arrived(self, data, metadata):
        self._append_live_html(render_outputs_html(
            [{'output_type': 'display_data', 'data': data,
              'metadata': metadata}]))
        # Round 22: a display payload can carry a figure -- the fit runs
        # HERE (once per payload), never per stream chunk: streaming
        # text arrives one line at a time and re-walking the document
        # per line would be O(document) work per streamed line.
        record = self._running_record()
        if record is not None:
            self._fit_browser_images_to_width(record['browser'])

    def _on_result_arrived(self, data, metadata):
        self._append_live_html(render_outputs_html(
            [{'output_type': 'execute_result', 'data': data,
              'metadata': metadata, 'execution_count': None}]))
        # Round 22: same rule as the display payload above.
        record = self._running_record()
        if record is not None:
            self._fit_browser_images_to_width(record['browser'])

    def _on_error_arrived(self, ename, evalue, traceback_lines):
        self._append_live_html(render_outputs_html(
            [{'output_type': 'error', 'ename': ename, 'evalue': evalue,
              'traceback': traceback_lines}]))

    def _on_kernel_started(self):
        if self._running:
            self._set_status('Running cell {}...'.format(
                self._running_cell_index + 1))
        else:
            self._set_status('Kernel started -- ' + self._kernel_status_text())

    def _on_run_finished(self, count, outputs):
        """The run's end, on the GUI thread (queued from the worker):
        records the outputs in the document, re-renders the authoritative
        block (whatever the live view missed is corrected here), pins the
        elapsed footer, applies the pending navigation and advances the
        Run All chain."""
        if not self._running:
            return  # a late finish after an engine failure was handled
        index = self._running_cell_index
        self._running = False
        self._running_cell_index = -1
        self._live_timer.stop()
        self.busy_bar.hide()
        self._set_running_ui(False)
        if not (0 <= index < self.document.cell_count()):
            return
        self.document.record_execution(index, count, outputs)
        self._render_outputs(index, outputs)
        interrupted = any(o.get('output_type') == 'error'
                          and o.get('ename') == 'KeyboardInterrupt'
                          for o in outputs)
        elapsed = time.monotonic() - self._run_started_at
        record = self._cell_widgets[index]
        record['elapsed_label'].setText(
            'interrupted after {:.1f} s'.format(elapsed) if interrupted
            else format_elapsed(elapsed))
        record['elapsed_label'].show()
        if interrupted:
            self._set_status(
                'Cell [{}] interrupted after {:.1f} s -- if a cell seems '
                'stuck (a C extension that never returns), use Restart '
                'kernel.'.format(index + 1, elapsed))
        else:
            self._set_status('Cell [{}] done -- {}'.format(
                count, self._kernel_status_text()))
        advance, insert_after = self._run_advance, self._run_insert_after
        self._run_advance = False
        self._run_insert_after = False
        if advance or insert_after:
            self._navigate_after_run(index, insert_after)
        self._continue_run_all(index, interrupted)

    def _on_run_failed(self, message):
        """An ENGINE failure (kernel start failure or an engine problem),
        not a user-code error -- user errors arrive as the cell's own
        error output through ``run_finished``. ONE message for the whole
        Run All chain: the chain stops here instead of failing cell after
        cell (the round-6b rule, kept)."""
        was_running = self._running
        self._running = False
        self._running_cell_index = -1
        self._run_all_active = False
        self._run_advance = False
        self._run_insert_after = False
        self._live_timer.stop()
        self.busy_bar.hide()
        if was_running:
            self._set_running_ui(False)
        self._set_status('The cell did not run: {}'.format(message))
        _show_message(self, 'The cell did not run:\n{}'.format(message))

    def _on_restart_finished(self, ok, message):
        if ok:
            self._set_status('Kernel restarted (namespace wiped) -- '
                             + self._kernel_status_text())
        else:
            self._set_status('The kernel did not restart: {}'.format(message))
            _show_message(self, 'The kernel did not restart:\n{}'
                          .format(message))
        # Round 18: the controls re-evaluate at the restart's end too --
        # the restarted kernel is still a running kernel (the in-use
        # lock stays held by design), and the pip tooltip's note must
        # reflect the state the restart actually left.
        self._update_env_controls()

    def _continue_run_all(self, index, interrupted):
        """The Run All chain, advanced from _on_run_finished: the next
        non-empty code cell starts on a zero-timer hop (no recursion
        inside a signal handler); an interrupted cell stops the chain (an
        interrupt is a stop, not the rest of the notebook). Round 24: the
        hop skips completely empty code cells -- only cells that actually
        ran increment the ran count."""
        if not self._run_all_active:
            return
        self._run_all_ran += 1
        if interrupted:
            self._run_all_active = False
            self._set_status('Run All stopped at cell {}.'.format(index + 1))
            return
        self._advance_run_all(index)

    def _advance_run_all(self, index):
        """The Run All chain's ONE hop (round 24, shared by the
        run-finished advance and the empty-cell skip inside
        ``_run_cell_at``): the next NON-EMPTY code cell after ``index``
        starts on a zero-timer hop (no recursion inside a signal
        handler); when none follows, the chain ends with the summary
        status -- the count names only the cells that actually ran."""
        next_index = next(
            (i for i in range(index + 1, self.document.cell_count())
             if self.document.get_cell(i)['cell_type'] == 'code'
             and self.document.get_cell(i)['source'].strip()), None)
        if next_index is None:
            self._run_all_active = False
            self._set_status('Run All finished ({} code cells) -- {}'.format(
                self._run_all_ran, self._kernel_status_text()))
            return
        QTimer.singleShot(0, lambda idx=next_index:
                          self._run_cell_at(idx, run_total=self._run_all_total))

    def _next_editable_index(self, index):
        """The first cell after ``index`` that still takes the keyboard
        (round 13): rendered markdown cells are inert, so the advance skips
        them. Code, raw and EDIT-mode markdown cells are valid stops.
        ``None`` when no editable cell follows ``index``."""
        for one in range(index + 1, self.document.cell_count()):
            cell = self.document.get_cell(one)
            if cell['cell_type'] == 'markdown' and \
                    cell['id'] not in self._markdown_edit_ids:
                continue
            return one
        return None

    def _on_rendered_skip_forward(self, index):
        """Shift+Enter on a rendered markdown browser (round 13): the same
        advance the editor chord would have made -- next editable cell, or a
        fresh appended code cell when none follows. Nothing runs. A RUNNING
        kernel refuses the skip: the advance's append branch would rebuild
        the cells mid-run, tearing down the running cell's widgets while
        the worker still streams into them -- the same hazard _guard_idle
        documents for every other structural action."""
        if self._running:
            return
        self._navigate_after_run(index, insert_after=False)

    def _navigate_after_run(self, index, insert_after):
        """Shift+Enter: advance to the next cell (a fresh one is appended at
        the end, Jupyter-style). Alt+Enter: insert a new code cell right
        below and focus it. Round 13: the advance skips RENDERED markdown
        cells -- it lands on the next editable one (code, raw or EDIT-mode
        markdown), appending a fresh code cell when none follows."""
        if insert_after:
            self._sync_editors_to_document()
            self.document.add_cell('code', index=index + 1)
            self._rebuild_cells(focus_index=index + 1)
            self._set_status('Code cell inserted below.')
            return
        next_index = self._next_editable_index(index)
        if next_index is None:
            self._sync_editors_to_document()
            next_index = self.document.cell_count()
            self.document.add_cell('code')
            self._rebuild_cells(focus_index=next_index)
            self._set_status('Code cell appended.')
        else:
            self._focus_cell(next_index)

    def _render_outputs(self, index, outputs):
        record = self._cell_widgets[index]
        stored_html = render_outputs_html(outputs)
        browser = record['browser']
        browser.setHtml(stored_html)
        if stored_html:
            browser.show()
        else:
            browser.hide()
        # Round 22: the authoritative render's figures are scaled to the
        # available width (the run-finished path, and the restore path
        # the suite uses -- an empty render just finds no images).
        self._fit_browser_images_to_width(browser)
        self._update_cell_header(index)

    def _update_cell_header(self, index):
        cell = self.document.get_cell(index)
        record = self._cell_widgets[index]
        count_value = cell.get('execution_count')
        record['prompt_label'].setText(
            'In [{}]:'.format(count_value) if count_value is not None
            else 'In [ ]:')

    def on_run_all(self):
        if self._running:
            self._set_status('A cell is already running -- wait for it.')
            return
        self._sync_editors_to_document()
        # Round 24: the totals and the chain's first pick count only
        # NON-EMPTY code cells -- a completely empty code cell (whitespace
        # only included) is skipped by the run, so it is not a cell to run.
        def _runnable(index):
            cell = self.document.get_cell(index)
            return cell['cell_type'] == 'code' and cell['source'].strip()
        total = sum(1 for index in range(self.document.cell_count())
                    if _runnable(index))
        if not total:
            self._set_status('No non-empty code cells to run.')
            return
        first = next(i for i in range(self.document.cell_count())
                     if _runnable(i))
        self._run_all_active = True
        self._run_all_total = total
        self._run_all_ran = 0
        self._run_cell_at(first, run_total=total)

    def on_restart_kernel(self):
        if self._running:
            self._set_status('A cell is still running -- wait for it.')
            return
        self.kernel.restart()
        self._set_status('Restarting the kernel...')

    def on_clear_outputs(self):
        if not self._guard_idle():
            return
        self.document.clear_outputs()
        for index, record in enumerate(self._cell_widgets):
            if index < self.document.cell_count() and \
                    self.document.get_cell(index)['cell_type'] == 'code':
                record['browser'].hide()
        self._set_status('Outputs cleared.')

    def _kernel_status_text(self):
        return 'kernel: running' if self.kernel.is_started \
            else 'kernel: not started'

    # ------------------------------------------------------- connection

    def _on_client_connected(self):
        self._disconnected_banner.hide()
        self._set_status('Connected to Dragonfly -- ' +
                         self._kernel_status_text())
        if _SMOKE:
            print('DFNB-CONNECTED', flush=True)

    def _on_client_disconnected(self):
        self._disconnected_banner.show()
        self._set_status('Dragonfly disconnected -- dfnb calls fail until '
                         'the bridge is back; reconnecting automatically.')
        if _SMOKE:
            print('DFNB-DISCONNECTED', flush=True)

    def _on_reconnect_tick(self):
        """The 2 s connection timer: while connected a near-free liveness
        probe (the socket state read detects a peer that closed); while
        disconnected the intended pipe first, then every dfnb_bridge_* pipe
        on the machine -- reopening Dragonfly reconnects the app (a
        restarted Dragonfly has a NEW pid and a new pipe name, which is
        why discovery, not just the old name). Success/failure surfaces
        through the client's connected/disconnected signals."""
        if self.client.is_connected():
            self.client.poll_reconnect()
            return
        if self.client.connect(self.client.pipe_name):
            return
        self.client.poll_reconnect()

    # ------------------------------------------------- .ipynb drop target

    def dragEnterEvent(self, event):
        """The window accepts a drag ONLY when it is the shared drop
        policy's 'consume' case (ONLY local .ipynb file URLs); everything
        else is ignored and the platform's own drop behavior stands."""
        if ipynb_drop_decision(event.mimeData()) == 'consume':
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        """Keeps the notebook drag alive over the window -- the enter/
        move/drop triple must agree on the same mime rule or the drop
        never arrives."""
        if ipynb_drop_decision(event.mimeData()) == 'consume':
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        """A dropped .ipynb opens in THIS window specifically (the same
        chain as the Open dialog, minus the dialog); any other drag is
        ignored, never opened."""
        if ipynb_drop_decision(event.mimeData()) != 'consume':
            event.ignore()
            return
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        event.acceptProposedAction()
        self._open_notebook_at(urls[0].toLocalFile())

    # ------------------------------------------------------- file actions

    def on_new_notebook(self):
        if not self._guard_idle():
            return
        if not self._confirm_discarding_changes('start a new notebook'):
            return
        self.document = NotebookDocument.new_notebook()
        self._markdown_edit_ids.clear()  # the one swap path that skips
        # _load_notebook_document's clear: a fresh document must not
        # inherit stale ids (inert while ids stay uuid4-unique, but the
        # invariant is the contract, not the coincidence)
        self.file_path = None
        # Round 16: a fresh notebook runs on the embedded Python (the
        # default selection; the environment is notebook metadata).
        self._apply_environment(None, mark_dirty=False)
        self._rebuild_cells()
        self._update_title()
        self._set_status('New notebook -- ' + self._kernel_status_text())

    def _open_document_or_report(self, path):
        """Reads a .ipynb into a NotebookDocument: ONE failure message (an
        error box, or a log entry under ``running_in_test``) and None for
        both open-failure families -- a corrupt notebook (ValueError,
        UnicodeDecodeError included) and a missing/locked/unreadable file
        (OSError). The except MUST cover OSError: an exception escaping
        this Qt slot is fatal to the whole application (PyQt6 aborts on
        unhandled slot exceptions), which would show zero message boxes on
        the failure path."""
        try:
            return NotebookDocument.from_file(path)
        except (ValueError, OSError) as exc:
            _show_message(self, 'The notebook could not be opened:\n{}'
                          .format(exc))
            return None

    def _open_notebook_at(self, path):
        """The drag-and-drop / argv open: the ``on_open_notebook`` chain
        minus the file dialog (the drop or the launcher's argv[2] already
        names the file). Returns None when the notebook was opened, else a
        short reason -- the callers that cannot show a dialog surface it
        (the application entry's smoke markers, the drop's status line).
        The guards run in the same order as the dialog entry: an idle
        check (a rebuild mid-run tears down the running cell's widgets),
        then the discard confirmation, then the load (whose ONE failure
        message -- or log entry under ``running_in_test`` -- is shown by
        ``_open_document_or_report``)."""
        if not self._guard_idle():
            return 'a cell is running'
        if not self._confirm_discarding_changes('open another notebook'):
            return 'cancelled'
        document = self._open_document_or_report(path)
        if document is None:
            return 'the notebook could not be read (the failure entry has ' \
                   'the reason)'
        self._load_notebook_document(document, path)
        return None

    def _load_notebook_document(self, document, path):
        """The shared tail of every open path (the dialog's and the
        drop/argv one): swaps the opened document in, remembers the
        folder, rebuilds the cells, updates the title and reports.

        The tail is guarded: an exception escaping this Qt slot is fatal
        to the whole application (PyQt6 aborts on unhandled slot
        exceptions), so a failure AFTER the document was read -- a stored
        payload the rebuild cannot survive, a registry or environment
        fault -- logs one record, reports through the ONE-failure-message
        seam and falls back to a fresh empty notebook instead of taking
        the window down."""
        self.document = document
        self.file_path = document.file_path
        try:
            registry_save({'last_used_folder': os.path.dirname(path)})
            # Round 11: a loaded document starts every markdown cell RENDERED --
            # the EDIT set belongs to the previous document, clear it (hygiene
            # against id collisions across notebooks).
            self._markdown_edit_ids.clear()
            # Round 16: the notebook remembers its environment; restore the
            # selection. An environment that no longer exists falls back to
            # the embedded Python with a LOG entry (never a modal box in the
            # middle of an open; the user sees the fallback in the strip).
            saved = document.metadata.get('dfnb_environment')
            name = None
            fallback_note = None
            if isinstance(saved, str) and saved:
                if saved in notebook_envs.list_environments(self._envs_root):
                    name = saved
                else:
                    _log.info("Dragonfly Notebook: the environment '%s' saved "
                              'in %s no longer exists; running on the '
                              'embedded Python.', saved, os.path.basename(path))
                    fallback_note = ("The saved environment '{}' no longer "
                                     'exists; running on the embedded '
                                     'Python.'.format(saved))
            self._apply_environment(name, mark_dirty=False)
            self._rebuild_cells()
            self._update_title()
            self._set_status('Opened {} ({} cells) -- {}'.format(
                os.path.basename(path), document.cell_count(),
                self._kernel_status_text()))
            if fallback_note is not None:
                # the fallback is the LAST word on the strip: the opened-file
                # status right above would otherwise bury it in the same line
                self._set_status(fallback_note)
        except Exception as exc:
            _log.exception(
                'Dragonfly Notebook: the open of %s failed after the '
                'document was read; falling back to an empty notebook.',
                os.path.basename(path))
            _show_message(self, 'The notebook could not be opened:\n{}: {}'
                          .format(type(exc).__name__, exc))
            try:
                self.document = NotebookDocument.new_notebook()
                self.file_path = None
                self._markdown_edit_ids.clear()
                self._apply_environment(None, mark_dirty=False)
                # title and status first: they stay truthful even if the
                # rebuild below is the part that fails
                self._update_title()
                self._set_status('The notebook could not be opened -- '
                                 + self._kernel_status_text())
                self._rebuild_cells()
            except Exception:
                # even a catastrophic fallback must never raise into the
                # slot: one log record, the process stays alive
                _log.exception(
                    'Dragonfly Notebook: the empty-notebook fallback after '
                    'a failed open failed too.')

    def on_open_notebook(self):
        if not self._guard_idle():
            return
        if not self._confirm_discarding_changes('open another notebook'):
            return
        folder = registry_load().get('last_used_folder') or os.path.expanduser('~')
        path, _filter = QFileDialog.getOpenFileName(
            self, 'Open notebook', folder, _FILE_FILTER)
        if not path:
            return
        document = self._open_document_or_report(path)
        if document is None:
            return  # the ONE failure message is already shown
        self._load_notebook_document(document, path)

    def on_save_notebook(self):
        """Saves (Save As through a dialog when the notebook has no path
        yet). Returns True when the notebook ended up saved."""
        if self._running:
            self._set_status('A cell is still running -- wait for it.')
            return False
        self._sync_editors_to_document()
        if self.file_path is None:
            folder = registry_load().get('last_used_folder') \
                or os.path.expanduser('~')
            path, _filter = QFileDialog.getSaveFileName(
                self, 'Save notebook', os.path.join(folder, 'notebook.ipynb'),
                _FILE_FILTER)
            if not path:
                return False  # the dialog was cancelled -- not saved
            if not path.lower().endswith('.ipynb'):
                path += '.ipynb'
            self.file_path = path
        try:
            self.document.save(self.file_path)
        except OSError as exc:
            self._set_status('The notebook could not be saved: {}'.format(exc))
            # ONE failure message, logged instead of shown under test
            # (a headless gate hitting a real save failure must not hang
            # on a dialog).
            _show_message(self, 'The notebook could not be saved:\n{}'
                          .format(exc))
            return False
        registry_save({'last_used_folder': os.path.dirname(self.file_path)})
        self._update_title()
        self._set_status('Saved {}'.format(self.file_path))
        return True

    def _confirm_discarding_changes(self, action):
        """Save / Discard / Cancel before a destructive action (new, open,
        close). True only when the action may proceed."""
        self._sync_editors_to_document()
        if not self.document.is_dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle('Dragonfly Notebook')
        # the same seam as _show_message: name + stylesheet on the box
        # itself; the #dfnb_message button rules style all three buttons
        # and the :default rule accents the Save default
        box.setObjectName('dfnb_message')
        box.setStyleSheet(_DIALOG_STYLESHEET)
        box.setText('The notebook has unsaved changes. Save them before you '
                    '{}?'.format(action))
        save_button = box.addButton(QMessageBox.StandardButton.Save)
        box.addButton(QMessageBox.StandardButton.Discard)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            return self.on_save_notebook()
        return clicked is not cancel_button

    # ------------------------------------------------------ edit actions

    def on_add_code_cell(self):
        self._add_cell_below('code')

    def on_add_markdown_cell(self):
        self._add_cell_below('markdown')

    def _add_cell_below(self, cell_type):
        if not self._guard_idle():
            return  # a rebuild mid-run tears down the running cell's widgets
        index = (self._current_cell_index
                 if 0 <= self._current_cell_index < self.document.cell_count()
                 else self.document.cell_count() - 1)
        self._sync_editors_to_document()
        new_cell = self.document.add_cell(cell_type, index=index + 1)
        if cell_type == 'markdown':
            # Round 11: a freshly added markdown cell starts in EDIT mode
            # (you write first, run to render) -- the id must be in the set
            # BEFORE _rebuild_cells builds the widget.
            self._markdown_edit_ids.add(new_cell['id'])
        self._rebuild_cells(focus_index=index + 1)
        self._set_status('{} cell added.'.format(cell_type.capitalize()))

    def on_move_cell_up(self):
        self._move_current_cell(-1)

    def on_move_cell_down(self):
        self._move_current_cell(1)

    def _move_current_cell(self, delta):
        if not self._guard_idle():
            return
        index = self._current_cell_index
        if not (0 <= index < self.document.cell_count()):
            self._set_status('Select a cell first (click into one).')
            return
        self._sync_editors_to_document()
        if not self.document.move_cell(index, delta):
            self._set_status('The cell is already at the {}.'
                             .format('top' if delta < 0 else 'bottom'))
            return
        self._rebuild_cells(focus_index=index + delta)
        self._set_status('Cell moved {}.'
                         .format('up' if delta < 0 else 'down'))

    def on_delete_cell(self):
        if not self._guard_idle():
            return
        index = self._current_cell_index
        if not (0 <= index < self.document.cell_count()):
            self._set_status('Select a cell first (click into one).')
            return
        if self.document.cell_count() == 1:
            self._set_status('Cannot delete the last cell -- clear it '
                             'instead.')
            return
        self._sync_editors_to_document()
        self.document.remove_cell(index)
        self._rebuild_cells(focus_index=min(index,
                                            self.document.cell_count() - 1))
        self._set_status('Cell deleted.')

    # ---------------------------------------------------------- window

    def _set_status(self, text):
        self.status_label.setText(text)

    def _update_title(self):
        name = os.path.basename(self.file_path) if self.file_path \
            else 'untitled'
        self.setWindowTitle('Dragonfly Notebook -- {}'.format(name))

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            self._set_status('A cell is still running -- wait for it to '
                             'finish before closing.')
            return
        self._sync_editors_to_document()
        if self.document.is_dirty:
            if not self._confirm_discarding_changes('close'):
                event.ignore()
                return
        # The kernel is per notebook window: when the window closes the
        # kernel's worker thread is torn down with it.
        try:
            self.kernel.stop()
        except Exception:
            pass
        # The bridge client is dropped for good; a NEW notebook window
        # (a new application launch) connects afresh.
        try:
            self.client.close()
        except Exception:
            pass
        super().closeEvent(event)
