r"""The .ipynb drag-and-drop decision shared by all three drop surfaces
(round 9).

One rule, three consumers -- each of which must treat the SAME drags the
same way, so the predicate lives here once and every surface imports it:

* the notebook APPLICATION window (``mainform_dragonflynotebook``): a
  consumed drag opens the dropped notebook in THAT window;
* the per-cell editors (``notebook_cells``): a consumed drag is IGNORED
  (the editor must not swallow it as text -- probed round 9: the base
  ``QPlainTextEdit`` accepts the drag and inserts the URL as text, and Qt
  even synthesizes ``text/plain`` from the URL list, so ``hasText()`` is
  True for a plain Explorer file drag);
* the Dragonfly-side glue's APPLICATION-level event filter
  (``DragonflyNotebook._IpynbDropFilter``): a consumed drag launches the
  notebook application with the dropped file (the C++ file-drop
  dispatcher has hardcoded extension lists and no hook a plugin can
  register .ipynb with, so the filter intercepts one level up).

The decision is DUCK-TYPED on purpose: it only calls ``hasUrls()`` /
``urls()`` / ``isLocalFile()`` / ``toLocalFile()`` on the object it is
given (a real ``QMimeData`` in production, a synthetic one in the suites),
so this module imports no Qt at all and loads in every host -- the
ORS-free application process, the Dragonfly process, and the bare
tier-1 interpreter.

A drag is 'consume' ONLY when it carries at least one URL and EVERY URL
is a LOCAL file whose name ends in ``.ipynb`` (case-insensitive -- the
Windows Explorer's extensions are uppercase as often as not). Anything
else -- mixed .ipynb + other files, non-notebook files, remote URLs,
non-URL drags, no data -- is 'pass': the platform's own drop behavior
(the C++ dispatcher in Dragonfly, the plain-text insert in the editors)
stands untouched, exactly as before round 9.
"""

import os

# Same multiple-OpenMP-runtime workaround as every module in this project:
# this module itself imports no numpy and no Qt, but the convention is
# uniform and the guard costs nothing.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

NOTEBOOK_SUFFIX = '.ipynb'


def is_local_ipynb_url(url):
    """True when ``url`` is a LOCAL file whose name ends in ``.ipynb``
    (case-insensitive). A remote URL, a non-file URL or a local
    non-notebook file is False -- one wrong URL makes the WHOLE drag
    'pass' (the mixed-drag rule)."""
    if url is None or not url.isLocalFile():
        return False
    return url.toLocalFile().lower().endswith(NOTEBOOK_SUFFIX)


def ipynb_drop_decision(mime):
    """The shared drop decision: 'consume' when the drag carries ONLY
    local .ipynb file URLs, 'pass' for everything else (see the module
    docstring for the three consumers and why the rule lives here)."""
    if mime is None or not mime.hasUrls():
        return 'pass'
    urls = mime.urls()
    if not urls:
        return 'pass'
    for url in urls:
        if not is_local_ipynb_url(url):
            return 'pass'
    return 'consume'
