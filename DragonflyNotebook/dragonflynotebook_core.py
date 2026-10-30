r"""Dragonfly Notebook core (stdlib only, framework-free).

Everything the plugin needs that does NOT touch ORSModel or Qt lives here:

  * the ``.ipynb`` document model -- a tolerant nbformat-4 reader (sources
    as str OR list-of-strings, missing cell ids generated, code/markdown/raw
    cells, outputs preserved verbatim), a canonical nbformat-4.5 writer
    (1-space indent, sorted keys, trailing newline, the Dragonfly
    kernelspec), and the cell operations the notebook form drives (add /
    remove / move / retype / clear outputs, dirty tracking);
  * the eight ``dfnb`` helper-function snippet templates and their code
    generation (identifiers sanitized, titles repr-escaped) used by the
    functions dock drag and the picker dialogs;
  * the small JSON registry (last-used notebook folder) the form's open/save
    dialogs remember, the H5Importer registry pattern.

``nbformat`` is NOT installed in Dragonfly's embedded environment (verified
by import on ``C:\Program Files\Dragonfly\Python_env\python.exe``:
ModuleNotFoundError) and must never be pip-installed into it -- this module
hand-rolls the subset of the format the plugin needs, on stdlib ``json``.

Pure-data contract: no ORS/Qt import anywhere in this module, so the tier-1
suite runs it on the bare embedded interpreter without the dfpy_ors launcher.
No numpy import either -- the notebook model is text and JSON.

Notebook model, exactly what is read/written
--------------------------------------------
A cell is stored internally in NORMALIZED shape (source always one str):

  ``{'cell_type': 'code'|'markdown'|'raw', 'id': str, 'metadata': dict,
     'source': str}`` plus, for code cells only, ``'execution_count'``
  (int | None) and ``'outputs'`` (list of whatever JSON objects the file
  carried -- passed through untouched; the kernel engine and the form own
  their interpretation).

The writer emits nbformat 4.5 Jupyter-canonical JSON: 1-space indent, sorted
keys, a trailing newline, every cell carrying a unique non-empty ``id``,
code-cell ``source`` (and every other cell's) as a list of lines with
trailing newlines kept (``s.splitlines(keepends=True)``), and notebook
``metadata`` including the Dragonfly kernelspec::

    {'kernelspec': {'name': 'dragonfly', 'display_name': 'Dragonfly',
                    'language': 'python'}}

A document read from a file keeps the file's own metadata (tolerance) and
cell ids; a document created fresh gets the Dragonfly kernelspec. Writing is
deterministic (``sort_keys=True``), so read -> write on an already-canonical
file is byte-identical.
"""

import json
import keyword
import os
import re
import uuid

# The ONE plugin-version source (round 15): the ORS glue re-exports it
# (DragonflyNotebook.py) and the ORS-free form's footer watermark prints
# it (mainform). The two processes never see each other's modules -- the
# notebook application process imports no ORS glue -- so the version
# lives here, in the one module both import, and can never drift.
__version__ = '0.6.3'

# Same multiple-OpenMP-runtime workaround as every module in this project:
# this module itself imports no numpy, but it is the FIRST line of the core
# convention and costs nothing.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')


# ---------------------------------------------------------------------------
# notebook format constants
# ---------------------------------------------------------------------------

NBFORMAT_MAJOR = 4
NBFORMAT_MINOR = 5

DEFAULT_KERNELSPEC = {'name': 'dragonfly',
                      'display_name': 'Dragonfly',
                      'language': 'python'}

CELL_TYPES = ('code', 'markdown', 'raw')

# The two supported source spellings inside a cell (nbformat 4.x allows both):
SOURCE_AS_STR, SOURCE_AS_LIST = 'str', 'list'


def _error(message):
    """Every reader rejection is a ValueError with a clear, quoted message."""
    return ValueError(message)


# ---------------------------------------------------------------------------
# cell normalization / canonicalization
# ---------------------------------------------------------------------------

def _generate_cell_id(existing_ids):
    """A fresh unique cell id (nbformat 4.5 requires one per cell)."""
    while True:
        candidate = uuid.uuid4().hex[:16]
        if candidate not in existing_ids:
            return candidate


def _normalize_source(raw_source):
    """Accepts nbformat's two source spellings and returns one str.

    A plain str passes through (empty string -> ''); a list of strings is
    joined with '' -- nbformat list items already carry their trailing
    newlines, so the join reconstructs the source exactly. Anything else is
    a ValueError (an int source is corruption, not tolerance).
    """
    if raw_source is None:
        return ''
    if isinstance(raw_source, str):
        return raw_source
    if isinstance(raw_source, list):
        for item in raw_source:
            if not isinstance(item, str):
                raise _error('a cell source list must hold only strings, '
                             'got {!r}'.format(type(item).__name__))
        return ''.join(raw_source)
    raise _error('a cell source must be a string or a list of strings, '
                 'got {!r}'.format(type(raw_source).__name__))


def _normalize_execution_count(raw_count):
    """An int (or float holding an int) passes; anything else -> None."""
    if raw_count is None:
        return None
    if isinstance(raw_count, bool):
        return None
    if isinstance(raw_count, int):
        return raw_count
    if isinstance(raw_count, float) and raw_count.is_integer():
        return int(raw_count)
    return None


def _normalize_cell(raw_cell, existing_ids):
    """One raw JSON cell dict -> the internal normalized dict (a copy).

    Raises ValueError with a clear message on a non-dict cell, a missing
    cell_type, or a cell_type outside code/markdown/raw. Outputs are
    preserved verbatim (code cells); a missing/odd outputs list becomes [].
    """
    if not isinstance(raw_cell, dict):
        raise _error('every notebook cell must be a JSON object, '
                     'got {!r}'.format(type(raw_cell).__name__))
    cell_type = raw_cell.get('cell_type')
    if not isinstance(cell_type, str) or cell_type not in CELL_TYPES:
        raise _error("unknown cell type {!r} (expected one of {})."
                     .format(cell_type, ', '.join(CELL_TYPES)))

    cell = {'cell_type': cell_type}
    cell_id = raw_cell.get('id')
    if isinstance(cell_id, str) and cell_id:
        cell['id'] = cell_id
        existing_ids.add(cell_id)
    else:
        cell['id'] = _generate_cell_id(existing_ids)
        existing_ids.add(cell['id'])

    metadata = raw_cell.get('metadata')
    cell['metadata'] = dict(metadata) if isinstance(metadata, dict) else {}
    cell['source'] = _normalize_source(raw_cell.get('source'))

    if cell_type == 'code':
        outputs = raw_cell.get('outputs')
        cell['outputs'] = list(outputs) if isinstance(outputs, list) else []
        cell['execution_count'] = _normalize_execution_count(
            raw_cell.get('execution_count'))
    return cell


def _canonical_source_lines(source):
    """The writer's source spelling: a list of lines with trailing newlines
    kept (Jupyter-canonical); the empty source is an empty list."""
    if not source:
        return []
    return source.splitlines(keepends=True)


def _canonical_cell(cell):
    """The internal normalized dict -> the writer's canonical dict (code
    cells carry execution_count + outputs, others do not)."""
    canonical = {'cell_type': cell['cell_type'],
                 'id': cell['id'],
                 'metadata': dict(cell.get('metadata') or {}),
                 'source': _canonical_source_lines(cell.get('source', ''))}
    if canonical['cell_type'] == 'code':
        canonical['execution_count'] = cell.get('execution_count')
        canonical['outputs'] = list(cell.get('outputs') or [])
    return canonical


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------

class NotebookDocument(object):
    """One .ipynb in memory: normalized cells, metadata, dirty flag."""

    def __init__(self, cells=None, metadata=None):
        if cells is None:
            cells = [self.new_cell('code')]
        self.cells = list(cells)
        if metadata is None:
            metadata = {'kernelspec': dict(DEFAULT_KERNELSPEC)}
        self.metadata = dict(metadata)
        self.file_path = None
        self.is_dirty = False

    # ---- construction helpers -------------------------------------------

    @staticmethod
    def new_cell(cell_type='code', source=''):
        """A fresh normalized cell dict (fresh unique id)."""
        return _normalize_cell({'cell_type': cell_type,
                                'source': source,
                                'metadata': {}},
                               existing_ids=set())

    @classmethod
    def new_notebook(cls):
        """A fresh notebook: Dragonfly kernelspec + one empty code cell."""
        return cls()

    # ---- (de)serialization ----------------------------------------------

    @classmethod
    def from_json(cls, text):
        """Parses notebook JSON text (tolerant reader) -> a clean document."""
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise _error('not a valid Jupyter notebook: the file is not '
                         'valid JSON ({})'.format(exc))
        if not isinstance(data, dict):
            raise _error('not a valid Jupyter notebook: the top-level JSON '
                         'value must be an object, got {!r}'
                         .format(type(data).__name__))
        major = data.get('nbformat')
        if major != NBFORMAT_MAJOR:
            raise _error('unsupported notebook nbformat {} (this plugin '
                         'reads nbformat 4).'.format(major))
        raw_cells = data.get('cells')
        if not isinstance(raw_cells, list):
            raise _error('not a valid Jupyter notebook: the "cells" entry '
                         'is missing or is not a list.')
        existing_ids = set()
        cells = [_normalize_cell(raw_cell, existing_ids)
                 for raw_cell in raw_cells]
        metadata = data.get('metadata')
        if not isinstance(metadata, dict) or not metadata:
            metadata = {'kernelspec': dict(DEFAULT_KERNELSPEC)}
        document = cls(cells=cells, metadata=metadata)
        document.mark_clean()
        return document

    @classmethod
    def from_file(cls, path):
        """Reads a .ipynb from disk; the path is remembered for Save."""
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
        document = cls.from_json(text)
        document.file_path = path
        return document

    def to_json(self):
        """The canonical nbformat-4.5 text (deterministic, trailing \\n)."""
        notebook = {'cells': [_canonical_cell(cell) for cell in self.cells],
                    'metadata': self.metadata,
                    'nbformat': NBFORMAT_MAJOR,
                    'nbformat_minor': NBFORMAT_MINOR}
        return json.dumps(notebook, indent=1, sort_keys=True,
                          ensure_ascii=False) + '\n'

    def save(self, path=None):
        """Writes the notebook; ``path=None`` re-saves to the remembered
        file (ValueError when the document was never saved/named). Clears
        the dirty flag."""
        target = path or self.file_path
        if not target:
            raise _error('the notebook has no file path yet -- use '
                         'Save As first.')
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write(self.to_json())
        self.file_path = target
        self.mark_clean()

    def mark_clean(self):
        self.is_dirty = False

    def _touch(self):
        self.is_dirty = True

    def _clip_index(self, index):
        """An insert index clamped into [0, len(cells)] (out-of-range
        inserts clamp to the nearest end instead of raising)."""
        return max(0, min(int(index), len(self.cells)))

    # ---- cell operations -------------------------------------------------

    def cell_count(self):
        return len(self.cells)

    def get_cell(self, index):
        """The normalized cell dict at ``index`` (IndexError when out of
        range -- the form indexes honestly)."""
        return self.cells[index]

    def add_cell(self, cell_type='code', index=None, source=''):
        """Inserts a new cell (appends when ``index`` is None) and returns
        its normalized dict."""
        if cell_type not in CELL_TYPES:
            raise _error("unknown cell type {!r} (expected one of {})."
                         .format(cell_type, ', '.join(CELL_TYPES)))
        cell = self.new_cell(cell_type, source)
        if index is None:
            self.cells.append(cell)
        else:
            index = self._clip_index(index)
            self.cells.insert(index, cell)
        self._touch()
        return cell

    def remove_cell(self, index):
        """Removes the cell at ``index`` and returns it."""
        cell = self.cells.pop(index)
        self._touch()
        return cell

    def move_cell(self, index, delta):
        """Moves the cell ``delta`` positions (-1 up, +1 down). Returns
        True when moved; False (unchanged) at either boundary."""
        target = index + delta
        if target < 0 or target >= len(self.cells) or target == index:
            return False
        cell = self.cells.pop(index)
        self.cells.insert(target, cell)
        self._touch()
        return True

    def set_cell_source(self, index, source):
        self.cells[index]['source'] = source
        self._touch()

    def set_cell_type(self, index, cell_type):
        """Converts a cell in place: code -> markdown/raw drops the code
        extras (outputs + execution_count); markdown/raw -> code gains an
        empty outputs list and execution_count None. The source is kept."""
        if cell_type not in CELL_TYPES:
            raise _error("unknown cell type {!r} (expected one of {})."
                         .format(cell_type, ', '.join(CELL_TYPES)))
        cell = self.cells[index]
        if cell['cell_type'] == cell_type:
            return cell
        cell['cell_type'] = cell_type
        if cell_type == 'code':
            cell['outputs'] = []
            cell['execution_count'] = None
        else:
            cell.pop('outputs', None)
            cell.pop('execution_count', None)
        self._touch()
        return cell

    def clear_outputs(self, index=None):
        """Clears the outputs of one code cell (``index``) or of EVERY code
        cell (``index=None``). Markdown/raw cells have no outputs and are
        left alone. Returns the number of code cells cleared."""
        targets = range(len(self.cells)) if index is None else (index,)
        cleared = 0
        for one_index in targets:
            cell = self.cells[one_index]
            if cell['cell_type'] == 'code' and cell.get('outputs'):
                cell['outputs'] = []
                cleared += 1
        if cleared:
            self._touch()
        return cleared

    def record_execution(self, index, execution_count, outputs):
        """The kernel engine's write-back after a cell ran: the execution
        count and the structured outputs land on the code cell."""
        cell = self.cells[index]
        cell['execution_count'] = execution_count
        cell['outputs'] = list(outputs)
        self._touch()


# ---------------------------------------------------------------------------
# the dfnb snippet templates
# ---------------------------------------------------------------------------

SNIPPET_MIME = 'application/x-dfnb-snippet'

# The seven dock items, in dock order: kind -> {'label', 'function'}.
# ``function`` is the dfnb.<function>(...) call the generated snippet makes;
# the picker dialogs (one per kind) fill its parameters.
SNIPPET_SPECS = {
    'import_volume': {
        'label': 'Import volume as numpy',
        'function': 'import_volume_as_np',
    },
    'import_roi': {
        'label': 'Import ROI as numpy',
        'function': 'import_roi_as_np',
    },
    'import_multiroi': {
        'label': 'Import MultiROI as numpy',
        'function': 'import_multiroi_as_np',
    },
    'publish_volume': {
        'label': 'Publish numpy as volume',
        'function': 'publish_np_as_volume',
    },
    'publish_roi': {
        'label': 'Publish numpy as ROI',
        'function': 'publish_np_as_roi',
    },
    'publish_multiroi': {
        'label': 'Publish numpy as multiROI',
        'function': 'publish_np_as_multiroi',
    },
    'import_properties': {
        'label': 'Import measured properties (MultiROI)',
        'function': 'import_measured_properties',
    },
    'export_properties': {
        'label': 'Export measured properties (MultiROI)',
        'function': 'export_measured_properties',
    },
}

SNIPPET_ORDER = ('import_volume', 'import_roi', 'import_multiroi',
                 'publish_volume', 'publish_roi', 'publish_multiroi',
                 'import_properties', 'export_properties')

# Placeholder defaults: the text/plain drag fallback (dropped anywhere that
# does not understand the custom mime) carries a snippet with placeholder
# titles the user edits -- and every generated snippet's DEFAULT parameters
# when a dialog is not involved.
_SNIPPET_DEFAULTS = {
    'import_volume': {'variable': 'volume_np', 'source': '<volume title>'},
    'import_roi': {'variable': 'mask_np', 'source': '<ROI title>'},
    'import_multiroi': {'variable': 'labels_np', 'source': '<MultiROI title>'},
    'publish_volume': {'array': 'volume_np', 'title': 'Result volume',
                       'reference': None, 'spacing': None},
    'publish_roi': {'array': 'mask_np', 'title': 'Processed ROI',
                    'reference': None},
    'publish_multiroi': {'array': 'labels_np', 'title': 'New segmentation',
                         'reference': None},
    'import_properties': {'variable': 'props', 'source': '<MultiROI title>',
                          'properties': None},
    'export_properties': {'source': '<MultiROI title>',
                          'properties': 'props_df'},
}


def sanitize_identifier(name, fallback='value'):
    """A Python identifier from any object title: non-word characters become
    '_', repeats collapse, a leading digit (or empty result) gets a leading
    '_', and a Python keyword gets a trailing '_'. Never empty."""
    cleaned = re.sub(r'\W+', '_', str(name or '').strip()).strip('_')
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = '_' + cleaned
    if keyword.iskeyword(cleaned):
        cleaned += '_'
    return cleaned


_PUBLISH_NOUNS = {'publish_volume': 'volume',
                  'publish_roi': 'roi',
                  'publish_multiroi': 'multiroi',
                  # the export kind's publish mode names its new MultiROI
                  # new_mroi (not new_multiroi): it is a MEASURED variant
                  # of the published segmentation
                  'export_properties': 'mroi'}


def generate_snippet(kind, variable=None, source=None, timestep=0, title=None,
                     reference=None, spacing=None, properties=None,
                     array=None, wipe=False):
    """The one generated code line for a dock/picker kind.

    :param kind: a SNIPPET_SPECS key
    :param variable: the assignment target of the import* / measured-
                     properties kinds (sanitized by the caller or defaulted;
                     publish kinds default to new_volume/new_roi/new_multiroi
                     and may override it too; the export kind's publish mode
                     assigns the new MultiROI to it)
    :param source: the object title or GUID the dfnb function resolves
                   (repr-escaped into the snippet)
    :param timestep: appended as ``timestep=N`` unless 0
    :param title: the published object's title (omitted when None/'')
    :param reference: an optional source volume the published object inherits
                      geometry from (omitted when None/'')
    :param spacing: an optional (sx, sy, sz) for publish_volume and the
                    export kind's publish mode (omitted when None)
    :param properties: the measured-properties column subset for
                       import_properties (omitted when None/empty = import
                       every slot); for publish_multiroi and the export
                       kind it is the KERNEL VARIABLE holding the properties
                       (a DataFrame or dict), appended as
                       ``properties=<variable>``
    :param array: the kernel variable holding the numpy array to publish
    :param wipe: the export kind's replace-all flag for mode A (write into
                 an EXISTING MultiROI): appended as ``wipe=True`` only when
                 True (the timestep-nonzero convention — generated lines
                 stay minimal); mode B (publish) NEVER renders it — a wipe
                 is meaningless alongside an array (the fresh MultiROI has
                 no existing properties to replace), so a wipe passed with
                 an array is simply not emitted
    :return: the snippet WITHOUT a trailing newline

    Called with no parameters beyond ``kind`` (the text/plain drag fallback),
    every placeholder/default fills in so the pasted line is still readable.

    The export kind generates one of TWO lines: without ``array`` it writes
    into an EXISTING MultiROI (a bare ``dfnb.export_measured_properties``
    call — the function returns None, so no assignment target); with
    ``array`` it publishes a NEW MultiROI from the array and carries the
    properties (``dfnb.publish_np_as_multiroi(..., properties=<variable>)``).
    """
    if kind not in SNIPPET_SPECS:
        raise _error('unknown snippet kind {!r} (expected one of {}).'
                     .format(kind, ', '.join(SNIPPET_ORDER)))
    defaults = _SNIPPET_DEFAULTS[kind]
    function = SNIPPET_SPECS[kind]['function']

    if kind == 'export_properties' and array is None:
        # write into an EXISTING MultiROI: a bare call, no assignment
        # target (export_measured_properties returns None)
        props = sanitize_identifier(properties
                                    if properties is not None
                                    else defaults['properties'])
        args = [repr(source if source is not None else defaults['source']),
                props]
        if timestep:
            args.append('timestep={}'.format(int(timestep)))
        # the replace-all flag renders ONLY when set: the default line is
        # byte-identical to the pre-wipe shape
        if wipe:
            args.append('wipe=True')
        return 'dfnb.{}({})'.format(function, ', '.join(args))

    if kind in ('import_volume', 'import_roi', 'import_multiroi',
                'import_properties'):
        target = sanitize_identifier(variable
                                     if variable is not None
                                     else defaults['variable'])
        args = [repr(source if source is not None else defaults['source'])]
        if timestep:
            args.append('timestep={}'.format(int(timestep)))
        if kind == 'import_properties' and properties:
            args.append('properties=[{}]'.format(
                ', '.join(repr(one) for one in properties)))
        return '{} = dfnb.{}({})'.format(target, function, ', '.join(args))

    # the publish kinds -- and export_properties' publish mode, which
    # publishes the new MultiROI from an array and carries the properties
    # variable (dfnb.publish_np_as_multiroi(<array>, ..., properties=props))
    export_publish = kind == 'export_properties' and array is not None
    args = [array if array is not None else defaults.get('array')]
    shown_title = title if title is not None else defaults.get('title')
    if shown_title:
        args.append('title={}'.format(repr(shown_title)))
    shown_reference = (reference if reference is not None
                       else defaults.get('reference'))
    if shown_reference:
        args.append('reference={}'.format(repr(shown_reference)))
    if kind in ('publish_volume', 'publish_multiroi', 'export_properties'):
        shown_spacing = (spacing if spacing is not None
                         else defaults.get('spacing'))
        if shown_spacing:
            args.append('spacing=({}, {}, {})'.format(*shown_spacing))
    if properties and (kind == 'publish_multiroi' or export_publish):
        args.append('properties={}'.format(sanitize_identifier(properties)))
    target = sanitize_identifier(variable
                                 if variable is not None
                                 else 'new_' + _PUBLISH_NOUNS.get(kind,
                                                                  'multiroi'))
    if export_publish:
        # the export dialog's publish mode emits the publish call itself
        function = SNIPPET_SPECS['publish_multiroi']['function']
    return '{} = dfnb.{}({})'.format(target, function, ', '.join(args))


# ---------------------------------------------------------------------------
# the form's registry (last-used notebook folder)
# ---------------------------------------------------------------------------

def registry_default_path():
    """%LOCALAPPDATA%\\DragonflyNotebook\\notebook_registry.json."""
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        base = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    return os.path.join(base, 'DragonflyNotebook', 'notebook_registry.json')


def registry_load(registry_file=None):
    """The registry, or the empty default when no file exists yet / the file
    is malformed. Shape: {'last_used_folder': str|None}. Never raises: a
    corrupt registry must not brick open/save (the H5Importer rule)."""
    path = registry_file or registry_default_path()
    if not os.path.isfile(path):
        return {'last_used_folder': None}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (ValueError, OSError):
        return {'last_used_folder': None}
    if not isinstance(data, dict):
        return {'last_used_folder': None}
    folder = data.get('last_used_folder')
    if not isinstance(folder, str) or not folder:
        folder = None
    return {'last_used_folder': folder}


def registry_save(registry, registry_file=None):
    """Writes the registry back (best-effort: a full disk must not take the
    Save dialog down with it)."""
    path = registry_file or registry_default_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(registry, handle, indent=1, sort_keys=True)
    except OSError:
        pass
