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
import time  # noqa: E402

from PyQt6.QtCore import QLocale, Qt, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor, QValidator  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,  # noqa: E402
                             QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QMainWindow, QMenu, QMessageBox, QProgressBar,
                             QPushButton, QRadioButton, QScrollArea, QSpinBox,
                             QStackedWidget, QTextBrowser, QToolButton,
                             QVBoxLayout, QWidget)

from .dfnb_client import DfnbClient  # noqa: E402
from .dragonflynotebook_core import (SNIPPET_ORDER, SNIPPET_SPECS,  # noqa: E402
                                     NotebookDocument, generate_snippet,
                                     registry_load, registry_save,
                                     sanitize_identifier)
from .dragonflynotebook_core import __version__ as _PLUGIN_VERSION  # noqa: E402
from .notebook_cells import (NotebookCellEditor, PythonCellHighlighter,  # noqa: E402
                             _RenderedMarkdownKeyFilter, code_font,
                             render_outputs_html, render_stream_chunk)
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

# The output browsers' height cap (round 9): an output browser grows to
# its laid-out content (the editor's resizeToContent treatment, mirrored
# in _fit_browser_to_content) but only UP TO this cap -- outputs keep
# their own scrollbar BY DESIGN, so a giant array output cannot blow the
# cell (and the notebook panel) out to thousands of pixels; past the cap
# the browser scrolls instead of growing.
_MAX_OUTPUT_HEIGHT = 400

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
QFrame#status_strip { border-top: 1px solid #3C3C3C; background: #1E1E1E; }
QLabel#watermark_label { color: #808080; font-size: 8pt; }

/* --- Scrollbars, ported VERBATIM from Dragonfly's app stylesheet
   (resource/stylesheet/main.txt, the QScrollBar blocks) in round 9: the
   standalone application process has no app-global QSS to inherit, and
   the default OS scrollbar on the main panel was the one light element
   on the window. SCOPED on purpose (the round-4b house rule): child
   dialogs inherit ancestor stylesheets, so every scrollbar selector
   names its surface -- the main cells panel (QScrollArea#mainScrollArea)
   and the per-cell output browsers (QTextBrowser#cell_output, which KEEP
   their own scrollbars; the code editors have none at all). An unscoped
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
QScrollArea#mainScrollArea QScrollBar:vertical,
QTextBrowser#cell_output QScrollBar:vertical
{
      background: QLinearGradient( x1: 0, y1: 0, x2: 1, y2: 0, stop: 0.0 #121212, stop: 0.2 #282828, stop: 1 #484848);
      width: 9px;
      margin: 16px 0 16px 0;
      border: 1px solid #222222;
}
QScrollArea#mainScrollArea QScrollBar::handle:vertical,
QTextBrowser#cell_output QScrollBar::handle:vertical
{
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #606060, stop: 0.5 #606060, stop: 1 #606060);
      min-height: 20px;
      border-radius: 2px;
}
QScrollArea#mainScrollArea QScrollBar::add-line:vertical,
QTextBrowser#cell_output QScrollBar::add-line:vertical
{
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: bottom;
      subcontrol-origin: margin;
}
QScrollArea#mainScrollArea QScrollBar::sub-line:vertical,
QTextBrowser#cell_output QScrollBar::sub-line:vertical
{
      border: 1px solid #1b1b19;
      border-radius: 2px;
      background: QLinearGradient( x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #404040, stop: 1 #404040);
      height: 14px;
      subcontrol-position: top;
      subcontrol-origin: margin;
}
QScrollArea#mainScrollArea QScrollBar::up-arrow:vertical,
QTextBrowser#cell_output QScrollBar::up-arrow:vertical,
QScrollArea#mainScrollArea QScrollBar::down-arrow:vertical,
QTextBrowser#cell_output QScrollBar::down-arrow:vertical
{
      border: 1px solid black;
      width: 1px;
      height: 1px;
      background: white;
}
QScrollArea#mainScrollArea QScrollBar::add-page:vertical,
QTextBrowser#cell_output QScrollBar::add-page:vertical,
QScrollArea#mainScrollArea QScrollBar::sub-page:vertical,
QTextBrowser#cell_output QScrollBar::sub-page:vertical
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
        self.setWindowTitle('dfnb.{} — {}'.format(spec['function'],
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
        self._current_cell_index = -1    # the editor that last had focus
        self._running = False            # a cell (or Run All) is executing
        self._rebuilding = False         # suppresses spurious dirty flags
        self._running_cell_index = -1    # the cell currently running
        self._run_started_at = 0.0       # time.monotonic() at the run start
        self._run_advance = False        # Shift+Enter pending for the run
        self._run_insert_after = False   # Alt+Enter pending for the run
        # The Run All chain: the runs are ASYNCHRONOUS now, so the chain
        # advances from _on_run_finished (one QTimer.singleShot(0) hop per
        # cell -- no recursion inside a signal handler).
        self._run_all_active = False
        self._run_all_total = 0
        self._run_all_ran = 0
        self._smoke_ticks = 0            # DFNB_SMOKE: live-tick counter

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
        self._rebuild_cells()
        self._update_title()
        self._set_status('New notebook — kernel starts on the first run.')

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
            'Dragonfly is disconnected — dfnb calls fail until the bridge '
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
        status_strip = QFrame(self)
        status_strip.setObjectName('status_strip')
        strip_layout = QHBoxLayout(status_strip)
        strip_layout.setContentsMargins(0, 4, 0, 0)
        strip_layout.addWidget(self.status_label)
        strip_layout.addStretch(1)
        strip_layout.addWidget(self.busy_bar)
        outerLayout.addWidget(status_strip)
        # The watermark is the LAST widget of the outer layout: below the
        # status strip, always visible (the h5 importer's understated-footer
        # convention — no print/log/box emission).
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
        self._refresh_cell_accent()
        if focus_index is None:
            focus_index = 0 if self._cell_widgets else -1
        if 0 <= focus_index < len(self._cell_widgets):
            self._focus_cell(focus_index)

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
            edit_button = QPushButton('✎ Edit')
            edit_button.setObjectName('markdown_edit_button')
            edit_button.setToolTip('Back to editing')
            edit_button.clicked.connect(
                lambda _checked=False, idx=index:
                self._on_markdown_edit_button_clicked(idx))
            header.addWidget(edit_button)

        editor = NotebookCellEditor()
        editor.document().setDefaultFont(code_font())
        editor.setPlainText(cell['source'])
        # A dfnb snippet drag onto this editor announces itself; the form
        # runs the picker (through the bridge client it owns) and inserts
        # the generated line at THIS editor's cursor.
        editor.snippetDropped.connect(
            lambda kind, ed=editor: self._on_snippet_dropped(ed, kind))
        highlighter = None
        if is_code:
            highlighter = PythonCellHighlighter(editor.document())
            highlighter.relex(editor.toPlainText())
            editor.textChanged.connect(
                lambda ed=editor, hl=highlighter:
                self._on_editor_text_changed(ed, hl))
        editor.document().documentLayout().documentSizeChanged.connect(
            lambda _size, ed=editor: self._on_editor_size_changed(ed))
        QTimer.singleShot(0, lambda ed=editor: ed.resizeToContent())

        browser = QTextBrowser()
        browser.setObjectName('cell_output')
        browser.setOpenExternalLinks(False)
        browser.setFont(code_font())
        # The output browser grows with its content, up to the cap: the
        # editor's documentSizeChanged seam, mirrored. Connected BEFORE
        # any setHtml/setMarkdown below, so every content change rides it
        # -- the build-time stored outputs, the markdown preview, and the
        # later live chunks (_append_live_html) and authoritative
        # re-renders (_render_outputs) alike.
        browser.document().documentLayout().documentSizeChanged.connect(
            lambda _size, b=browser: self._fit_browser_to_content(b))
        if is_code:
            stored_html = render_outputs_html(cell.get('outputs') or [])
            if stored_html:
                browser.setHtml(stored_html)
            else:
                browser.hide()
        else:
            browser.setMarkdown(cell['source'])
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
        self.scrollArea.ensureWidgetVisible(record['frame'], 20, 20)

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

    def _fit_browser_to_content(self, browser):
        """Grows an output browser with its content, up to the cap.

        Round 9 -- the editor's resizeToContent treatment, mirrored for
        the per-cell output browser: QTextBrowser's sizeHint is a
        CONSTANT 256x192 no matter what the document holds, so a
        matplotlib figure clipped to a ~50 px band (only the x-axis
        strip visible) and a multi-line text output clipped mid-glyph
        with a scrollbar. document().size() IS the laid-out content
        height, images included (data-URL images enter the layout
        synchronously -- probed: 4 text lines -> 60.0 px, a 600x300
        figure -> 308.0 px). The fixed height is that document size plus
        the frame allowance, CLAMPED to _MAX_OUTPUT_HEIGHT: outputs keep
        their own scrollbar (the round-9 decision), so a giant output
        caps at 400 px and scrolls instead of stretching the cell to
        thousands of pixels. The documentSizeChanged seam in
        _add_cell_widget (connected before any setHtml/setMarkdown)
        routes every content change here -- the build-time stored
        outputs, the live stream chunks, the authoritative re-render and
        the markdown preview. A document does not lay out until the
        widget is shown, and showing re-lays-out and fires the signal,
        so the fit lands on the post-show truth with no explicit fit at
        the show sites. Idempotent: an unchanged fixed height does not
        re-enter the layout (probed: two consecutive fits, one height,
        zero signal churn after settling)."""
        height = min(int(math.ceil(browser.document().size().height()))
                     + _OUTPUT_HEIGHT_FUDGE, _MAX_OUTPUT_HEIGHT)
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
            self._set_status('A cell is running — wait for it.')
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
            self._set_status('A cell is running — wait for it.')
            return
        params = snippet_parameters_dialog(kind, self.client, self)
        if params is None:
            return  # cancelled, or its ONE failure box is already shown
        editor.insert_at_cursor(generate_snippet(kind, **params))
        if not self._rebuilding:
            self.document.is_dirty = True
        self._set_status('Snippet inserted: {}.'
                         .format(SNIPPET_SPECS[kind]['label']))

    # --------------------------------------------------------------- run

    def _guard_idle(self):
        """False (with a status note) while a cell runs. Round 8 made cell
        runs ASYNCHRONOUS, so every structural action now needs this guard
        -- a rebuild mid-run would tear down the running cell's widgets,
        which the synchronous rounds could not race (the GUI thread was
        busy inside the run)."""
        if self._running:
            self._set_status('A cell is running — wait for it (or press '
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
        rendered, round 12) or a refused run.
        ``run_total`` (Run All only) labels the status 'cell N of M'."""
        if self._running:
            self._set_status('A cell is already running — wait for it.')
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
        self.document.set_cell_source(index, code)

        if cell['cell_type'] == 'markdown':
            record['browser'].setMarkdown(code)
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

    def _on_result_arrived(self, data, metadata):
        self._append_live_html(render_outputs_html(
            [{'output_type': 'execute_result', 'data': data,
              'metadata': metadata, 'execution_count': None}]))

    def _on_error_arrived(self, ename, evalue, traceback_lines):
        self._append_live_html(render_outputs_html(
            [{'output_type': 'error', 'ename': ename, 'evalue': evalue,
              'traceback': traceback_lines}]))

    def _on_kernel_started(self):
        if self._running:
            self._set_status('Running cell {}...'.format(
                self._running_cell_index + 1))
        else:
            self._set_status('Kernel started — ' + self._kernel_status_text())

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
                'Cell [{}] interrupted after {:.1f} s — if a cell seems '
                'stuck (a C extension that never returns), use Restart '
                'kernel.'.format(index + 1, elapsed))
        else:
            self._set_status('Cell [{}] done — {}'.format(
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
            self._set_status('Kernel restarted (namespace wiped) — '
                             + self._kernel_status_text())
        else:
            self._set_status('The kernel did not restart: {}'.format(message))
            _show_message(self, 'The kernel did not restart:\n{}'
                          .format(message))

    def _continue_run_all(self, index, interrupted):
        """The Run All chain, advanced from _on_run_finished: the next code
        cell starts on a zero-timer hop (no recursion inside a signal
        handler); an interrupted cell stops the chain (the user asked for
        a stop, not for the rest of the notebook)."""
        if not self._run_all_active:
            return
        self._run_all_ran += 1
        if interrupted:
            self._run_all_active = False
            self._set_status('Run All stopped at cell {}.'.format(index + 1))
            return
        next_index = next(
            (i for i in range(index + 1, self.document.cell_count())
             if self.document.get_cell(i)['cell_type'] == 'code'), None)
        if next_index is None:
            self._run_all_active = False
            self._set_status('Run All finished ({} code cells) — {}'.format(
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
            self._set_status('A cell is already running — wait for it.')
            return
        self._sync_editors_to_document()
        total = sum(1 for index in range(self.document.cell_count())
                    if self.document.get_cell(index)['cell_type'] == 'code')
        if not total:
            self._set_status('No code cells to run.')
            return
        first = next(i for i in range(self.document.cell_count())
                     if self.document.get_cell(i)['cell_type'] == 'code')
        self._run_all_active = True
        self._run_all_total = total
        self._run_all_ran = 0
        self._run_cell_at(first, run_total=total)

    def on_restart_kernel(self):
        if self._running:
            self._set_status('A cell is still running — wait for it.')
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
        self._set_status('Connected to Dragonfly — ' +
                         self._kernel_status_text())
        if _SMOKE:
            print('DFNB-CONNECTED', flush=True)

    def _on_client_disconnected(self):
        self._disconnected_banner.show()
        self._set_status('Dragonfly disconnected — dfnb calls fail until '
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
        self._rebuild_cells()
        self._update_title()
        self._set_status('New notebook — ' + self._kernel_status_text())

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
        folder, rebuilds the cells, updates the title and reports."""
        self.document = document
        self.file_path = document.file_path
        registry_save({'last_used_folder': os.path.dirname(path)})
        # Round 11: a loaded document starts every markdown cell RENDERED --
        # the EDIT set belongs to the previous document, clear it (hygiene
        # against id collisions across notebooks).
        self._markdown_edit_ids.clear()
        self._rebuild_cells()
        self._update_title()
        self._set_status('Opened {} ({} cells) — {}'.format(
            os.path.basename(path), document.cell_count(),
            self._kernel_status_text()))

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
            self._set_status('A cell is still running — wait for it.')
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
            self._set_status('Cannot delete the last cell — clear it '
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
        self.setWindowTitle('Dragonfly Notebook — {}'.format(name))

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            self._set_status('A cell is still running — wait for it to '
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
