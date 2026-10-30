r"""The standalone Dragonfly Notebook application (round 8): the entry
point.

Dragonfly's plugin launcher (``DragonflyNotebook.DragonflyNotebook``)
spawns THIS process detached -- one new notebook window per launcher
click, several allowed -- with the bridge pipe name as ``argv[1]``
(``dfnb_bridge_<Dragonfly pid>``). The process is ORS-FREE by design:
it loads no ORS DLLs, imports no Dragonfly module — it sets
``DFNB_ORS_FREE_APP=1`` at startup so the package ``__init__`` skips
the ORS glue's eager re-export by PROCESS IDENTITY, not by ORS
importability — and talks to Dragonfly only through the bridge client
(``dfnb_client.DfnbClient``, the object the kernel seed pushes as
``dfnb``). The editor UI lives
here (``mainform_dragonflynotebook.DragonflyNotebookForm``) and the
IPython kernel runs here too, on the kernel's worker thread.

``QT_QPA_PLATFORM`` is NOT forced: a normal desktop launch shows a real
window, and the tier-3 verification harness runs this entry offscreen by
setting the variable itself (offscreen is testable).

Smoke hooks (environment-driven, for the tier-3 spawn test -- all prints
are line-flushed so a piped-stdout harness sees them as they happen):

* ``DFNB_SMOKE``: prints ``DFNB-APP-ORSFREE <True|False>`` right after
  the package import (``'ORSServiceClass' not in sys.modules`` — the
  process-identity pin: the app process never had ORS loaded), then
  ``DFNB-APP-UP`` (the form is shown), and the
  form-level prints join in -- ``DFNB-BUSY-BAR`` (the busy bar's
  no-percentage state, once per run start) and ``DFNB-TICKS`` (the live
  timer ticks that prove the GUI thread stays responsive mid-run).
  Under DFNB_SMOKE the application also sets the ``running_in_test``
  property (the form's failure seams LOG instead of going modal -- an
  automated spawn test can never hang on a dialog).
* ``DFNB_SMOKE`` + connected: prints ``DFNB-APP-CONNECTED`` and then
  performs ONE real bridge call (``dfnb.list_objects``) --
  ``DFNB-DFNB-OK objects=N`` on success, ``DFNB-DFNB-FAIL <error>``
  otherwise (the dfnb round-trip pin).
* ``argv[2]`` (round 9): a notebook FILE PATH -- the launcher's
  drag-and-drop handoff. Opened right after the form is shown (the
  DFNB-APP-UP print order is pinned) through the form's own drop entry
  (``_open_notebook_at``); under DFNB_SMOKE the outcome is printed as
  ``DFNB-APP-OPENED <path>`` or ``DFNB-APP-OPEN-FAIL <reason>``.
* ``DFNB_AUTORUN``: after the round-trip, puts a sleeping cell into the
  first cell (``import time`` / ``time.sleep(3)`` / ``print(...)`` --
  overridable via ``DFNB_AUTORUN_CODE``) and runs it; the busy-bar and
  tick prints above fire while it sleeps.
* ``DFNB_AUTOSTOP_AFTER``: seconds after the autorun starts, the
  kernel's stop flag is set -- exactly what the form's red Stop button
  does; the cell ends interrupted and the app prints ``DFNB-STOP-OK``
  when the run finishes with a ``KeyboardInterrupt``. The stop survives
  the kernel's lazy start: a request made before the cell begins (the
  first run still importing ipykernel) interrupts it at its first
  Python line -- the flag is deliberately not cleared at run start.
* the disconnect pin needs no hook: when the harness closes the bridge,
  the form's banner shows and it prints ``DFNB-DISCONNECTED``.
"""

import os

# Same multiple-OpenMP-runtime workaround as every module in this project:
# numpy is imported transitively by everything downstream, and this file
# is the application's first line.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import sys  # noqa: E402

# The application imports its own package (``DragonflyNotebook.`` below) --
# but Python puts THIS FILE'S DIRECTORY (the package dir itself) on
# ``sys.path[0]`` when it runs a script, never the package's PARENT, and the
# launcher strips ``PYTHONPATH`` from the child environment on purpose (the
# parent's ambient ORS auto-imports must not apply here). Insert the parent
# explicitly: in the installed tree that is
# ``...\\pythonUserExtensions\\Plugins`` (so ``DragonflyNotebook`` resolves to
# the installed package), in a source checkout the project root.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_PARENT = os.path.dirname(_APP_DIR)
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)

# The process-identity marker the package __init__ trusts over environment
# probing: THIS process must never load the ORS glue, so the package's
# eager re-export is skipped by marker (DFNB_ORS_FREE_APP=1), set BEFORE
# any DragonflyNotebook import -- never by whether ORS happens to be
# findable on sys.path (a broken-but-findable ORS must not crash the app
# at package import, a fully importable one must never silently load the
# glue -- the round-5-7 crash class).
os.environ['DFNB_ORS_FREE_APP'] = '1'

_AUTORUN_DEFAULT_CODE = 'import time\ntime.sleep(3)\nprint("after sleep")'


def _open_notebook_from_argv(form, file_path, smoke):
    """The argv[2] open (round 9, the launcher's drag-and-drop handoff):
    runs AFTER the form is shown -- the DFNB-APP-UP print order is pinned
    -- through the form's own drop entry ``_open_notebook_at`` (the idle
    guard, the discard confirmation, the document read, the rebuild).
    Under DFNB_SMOKE the outcome is printed as the markers the tier-3
    spawn test pins: ``DFNB-APP-OPENED <path>`` or
    ``DFNB-APP-OPEN-FAIL <reason>`` (a failed READ additionally surfaced
    the form's one-message seam, which under the smoke's
    ``running_in_test`` is a log entry, never a modal)."""
    reason = form._open_notebook_at(file_path)
    if not smoke:
        return
    if reason is None:
        print('DFNB-APP-OPENED {}'.format(form.file_path), flush=True)
    else:
        print('DFNB-APP-OPEN-FAIL {}'.format(reason), flush=True)


def main(argv=None):
    """Builds the application, shows one notebook window and runs the
    event loop until the window closes. ``argv[1]`` (when present) is the
    bridge pipe name the launcher passed; ``argv[2]`` (round 9, when
    present) is a notebook file path the launcher hands over -- opened
    right after the window is up."""
    argv = list(sys.argv if argv is None else argv)
    pipe_name = argv[1] if len(argv) > 1 else None
    file_path = argv[2] if len(argv) > 2 else None
    smoke = bool(os.environ.get('DFNB_SMOKE'))
    autorun = bool(os.environ.get('DFNB_AUTORUN'))
    autostop_after = float(os.environ.get('DFNB_AUTOSTOP_AFTER') or 0)

    from PyQt6.QtCore import QTimer  # noqa: E402
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    from DragonflyNotebook.mainform_dragonflynotebook import \
        DragonflyNotebookForm  # noqa: E402

    if smoke:
        print('DFNB-APP-ORSFREE {}'.format(
            'ORSServiceClass' not in sys.modules), flush=True)

    app = QApplication(argv or ['Dragonfly Notebook'])
    app.setApplicationName('Dragonfly Notebook')
    if smoke:
        # The smoke IS a test run: the form's failure seams log instead of
        # going modal (the running_in_test convention), so an automated
        # spawn test -- including the round-9 argv open of a corrupt
        # notebook -- can never hang on a dialog.
        app.setProperty('running_in_test', True)

    form = DragonflyNotebookForm(pipe_name=pipe_name)
    form.show()
    if smoke:
        print('DFNB-APP-UP', flush=True)

    if file_path:
        _open_notebook_from_argv(form, file_path, smoke)

    def _autorun():
        """The smoke's scripted cell: a sleeping cell in the first slot,
        then the stop timer -- the Stop-button path, verbatim."""
        if form.document.cell_count() < 1:
            form.document.add_cell('code')
            form._rebuild_cells()
        code = os.environ.get('DFNB_AUTORUN_CODE') or _AUTORUN_DEFAULT_CODE
        form._cell_widgets[0]['editor'].setPlainText(code)
        form._run_cell_at(0)
        if autostop_after > 0:
            QTimer.singleShot(int(autostop_after * 1000),
                              form.kernel.request_stop)

    def _roundtrip():
        """The dfnb round-trip pin: one real bridge call (the object list
        through the pipe into Dragonfly)."""
        try:
            payload = form.client.call('list_objects', {
                'class_name': 'Channel', 'representable_only': True})
        except Exception as exc:
            print('DFNB-DFNB-FAIL {}'.format(exc), flush=True)
            return
        print('DFNB-DFNB-OK objects={}'.format(
            len(payload.get('objects') or [])), flush=True)
        if autorun:
            QTimer.singleShot(0, _autorun)

    def _on_run_finished(_count, outputs):
        if smoke and any(o.get('output_type') == 'error'
                         and o.get('ename') == 'KeyboardInterrupt'
                         for o in outputs):
            print('DFNB-STOP-OK', flush=True)

    state = {'kicked': False}

    def _kick():
        """Runs once, when the bridge first turns out connected: the
        connect pin and the round-trip. The form connects its own slots
        first, so the banner already handled this transition."""
        if state['kicked']:
            return
        state['kicked'] = True
        if smoke:
            print('DFNB-APP-CONNECTED', flush=True)
            QTimer.singleShot(0, _roundtrip)
        elif autorun:
            QTimer.singleShot(0, _autorun)

    form.kernel.run_finished.connect(_on_run_finished)
    if form.client.is_connected():
        QTimer.singleShot(0, _kick)
    else:
        form.client.connected_signal.connect(_kick)

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
