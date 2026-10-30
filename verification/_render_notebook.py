r"""The vision-loop render harness: renders the standalone notebook form
offscreen into the PNG set the interpret-images acceptance pass reads
(round 9's visual judge step; reusable every round).

Construction mirrors the tier-3 headless-form section exactly: a real
bridge service serving the project's handlers, a connected form built
the way notebook_app.py builds it, and cells executed through the REAL
in-process kernel so outputs and elapsed footers are the genuine
article -- a bracket-heavy cell, a tall 40-line cell (the grow pin's
size), the 7.17 s sleep cell (the elapsed-footer evidence) and an
inline figure cell.

The round-9 font lesson is baked in: the offscreen platform has NO
bundled font directory (QFontDatabase logged 'Cannot find font directory
C:/Program Files/Dragonfly/lib/fonts' and every glyph rendered tofu in
the first render set) -- QT_QPA_FONTDIR must name a real font directory
BEFORE QApplication is constructed. The harness fails if the directory
is missing and prints the family count so the log shows the fix held.

Run under dfpy_ors (the bridge handlers import ORS), from the project
root:

  uv run --no-project --python "C:/Program Files/Dragonfly/Python_env/python.exe" \
      verification/dfpy_ors.py verification/_render_notebook.py [outdir]

[outdir] defaults to verification/renders_round9 and receives
01_top.png (the full 1280x800 form), 02_scrolled.png (the panel
scrolled -- the styled main scrollbar), 03_cell_zoom.png (the tall
cell's frame at native resolution) and 04_output_zoom.png (the figure
cell's frame). Prints RENDER-OK on success, exits nonzero otherwise.
"""

import os

# Same multiple-OpenMP-runtime workaround as every module in this project.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

# The offscreen platform must find real fonts (round 9: without this,
# every glyph rendered tofu and the vision pass had nothing to judge).
_FONT_DIR = r'C:\Windows\Fonts'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
assert os.path.isdir(_FONT_DIR), \
    "no Windows font directory at {} -- the render would be tofu".format(_FONT_DIR)
os.environ['QT_QPA_FONTDIR'] = _FONT_DIR

import sys  # noqa: E402
import time  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from DragonflyNotebook import (bridge_service, dragonflynotebook_lib,  # noqa: E402
                               mainform_dragonflynotebook)

# Four code cells: brackets/identifiers (the previously-black tokens), the
# tall grow-cell body, the sleep cell, the inline figure.
_CELL_BRACKETS = (
    'import numpy as np\n'
    'squares = [x ** 2 for x in range(10)]\n'
    'summary = {"sum": sum(squares), "mean": np.mean(squares)}\n'
    'print(squares)\n'
    'print(summary)\n')
_CELL_TALL = "\n".join([
    "def analyze(series, threshold=0.5):",
    '    """Return the values above the threshold."""',
    "    kept = [value for value in series if value > threshold]",
    "    total = sum(kept)",
    "    return kept, total",
    "",
    "",
    "def classify(value, bands=(0.25, 0.5, 0.75)):",
    "    for index, edge in enumerate(bands):",
    "        if value < edge:",
    "            return index",
    "    return len(bands)",
    "",
    "",
    "readings = [0.1, 0.3, 0.42, 0.55, 0.61, 0.8, 0.95]",
    "kept, total = analyze(readings)",
    "classes = [classify(value) for value in kept]",
    "print('kept:', kept)",
    "print('total: {:.2f}'.format(total))",
    "print('classes:', classes)",
    "",
    "",
    "matrix = np.array([readings[3:], kept]).T",
    "print('matrix shape:', matrix.shape)",
    "for row in matrix[:3]:",
    "    print(row, row.mean())",
    "",
    "",
    "labels = {'low': 0, 'mid': 1, 'high': 2}",
    "inverse = {v: k for k, v in labels.items()}",
    "print(inverse[2], len(labels))",
    "",
    "",
    "for name in ('alpha', 'beta', 'gamma'):",
    "    score = sum(ord(c) for c in name) % 7",
    "    print(name, score, score / 7)",
    "",
    "",
    "print('done')",
])
_CELL_SLEEP = 'import time\ntime.sleep(7.17)'
_CELL_FIGURE = (
    'import matplotlib.pyplot as plt\n'
    'plt.figure(figsize=(6, 3))\n'
    'plt.plot([0, 1, 2, 3, 4, 5], [1, 4, 2, 8, 5, 9], marker="o")\n'
    'plt.title("sample series")\n'
    'plt.show()')


def _wait_run_done(form, app, timeout_s=120.0):
    """Pumps the event loop until the form's run finishes (the runs are
    asynchronous by design; the GUI thread stays free while they run)."""
    deadline = time.monotonic() + timeout_s
    while form._running:
        if time.monotonic() > deadline:
            raise RuntimeError("a cell run did not finish in {} s".format(timeout_s))
        app.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.02)


def main(argv):
    outdir = argv[1] if len(argv) > 1 else os.path.join(
        _ROOT, 'verification', 'renders_round9')
    os.makedirs(outdir, exist_ok=True)

    from PyQt6.QtGui import QFontDatabase  # noqa: E402
    from PyQt6.QtWidgets import QApplication, QScrollArea  # noqa: E402

    app = QApplication(['dfnb_render_harness'])
    families = QFontDatabase.families()
    print('DFNB-RENDER-FONTS {} families'.format(len(families)), flush=True)
    print('DFNB-RENDER-FONTS {} families'.format(len(families)), flush=True)
    if len(families) < 10:
        raise RuntimeError(
            "the offscreen platform still cannot see fonts ({} families) "
            "-- QT_QPA_FONTDIR={}".format(len(families),
                                          os.environ['QT_QPA_FONTDIR']))

    service = bridge_service.NotebookBridgeService(
        dict(dragonflynotebook_lib.BRIDGE_HANDLERS),
        discard_hook=dragonflynotebook_lib.discard_bridge_object)
    try:
        form = mainform_dragonflynotebook.DragonflyNotebookForm(
            pipe_name=service.pipe_name)
        form.show()
        app.processEvents()

        # Four code cells (a fresh form carries one), each editor fed the
        # round-9 evidence code -- the run path syncs editors to the
        # document itself.
        while form.document.cell_count() < 4:
            form.on_add_code_cell()
        for record, code in zip(
                form._cell_widgets,
                (_CELL_BRACKETS, _CELL_TALL, _CELL_SLEEP, _CELL_FIGURE)):
            record['editor'].setPlainText(code)
        app.processEvents()

        for index in range(4):
            if not form._run_cell_at(index):
                raise RuntimeError("cell {} refused to start".format(index))
            _wait_run_done(form, app)

        # The full set: 1280x800, top and scrolled, then the two native-
        # resolution cell frames (code zoom + figure/output zoom).
        form.resize(1280, 800)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)
        form.grab().save(os.path.join(outdir, '01_top.png'))

        scroll = form.findChild(QScrollArea)
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum() * 55 // 100)
        app.processEvents()
        form.grab().save(os.path.join(outdir, '02_scrolled.png'))

        tall_frame = form._cell_widgets[1]['frame']
        tall_frame.grab().save(os.path.join(outdir, '03_cell_zoom.png'))
        figure_frame = form._cell_widgets[3]['frame']
        figure_frame.grab().save(os.path.join(outdir, '04_output_zoom.png'))

        elapsed = [r['elapsed_label'].text()
                   for r in form._cell_widgets]
        print('DFNB-RENDER-ELAPSED {}'.format(elapsed), flush=True)
        print('RENDER-OK {} (01_top, 02_scrolled, 03_cell_zoom, '
              '04_output_zoom)'.format(outdir), flush=True)
        return 0
    finally:
        service.close()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
