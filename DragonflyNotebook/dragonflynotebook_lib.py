r"""The ``dfnb`` data layer of the Dragonfly Notebook plugin.

This module is the bridge between numpy arrays in notebook cells and ORS
objects in the running Dragonfly session. The notebook kernel seeds it as a
plain module object under the name ``dfnb`` (kernel.shell.push({'dfnb': ...})),
so every cell can call it without imports:

    volume_np = dfnb.import_volume_as_np('CT scan')
    smoothed = volume_np.astype(np.float32) * 2.0
    dfnb.publish_np_as_volume(smoothed, title='Doubled', reference='CT scan')

Object resolution (``source`` / ``reference`` arguments). Every source
argument accepts either an ORS object instance or a string holding the
object's GUID or its title:

  * a GUID string is resolved first (``orsObj``) and matched exactly;
  * a title is matched against the representable objects of the expected
    class (``getAllObjectsOfClass`` + ``getIsRepresentable``) — temporary /
    scratch objects are never pickable by title;
  * an ambiguous title raises a rename-first error listing every match's
    GUID (rename all but one, or pass the GUID);
  * a title that exists only on ANOTHER class raises naming the classes
    that carry it (the round-12 report: the generic "No Channel with the
    title..." was misleading when same-titled objects DID exist);
  * ``delete_object`` resolves with no expected class: a title matches any
    representable object of the classes dfnb addresses;
  * a wrong-class object raises with the actual class named.

Dtype policy (``publish_np_as_*``). ORS channels hold uint8, uint16, uint32
or float32 data — ``createChannelFromNumpyArray`` re-initializes the target
channel from the array's itemsize, and it silently shifts signed arrays by
their minimum (a ``view`` + offset trick), which corrupts negative data. The
conforming layer here therefore pins the output's own type before any ORS
call:

  ============================  =========================
  input dtype                   published channel type
  ============================  =========================
  float32                       float32 (as-is)
  uint8 / uint16 / uint32       as-is
  float16 / float64             float32
  bool                          uint8 (False/True -> 0/1)
  signed ints, min >= 0         smallest uint that fits
  signed ints with negatives    float32 (values preserved)
  uint64                        uint32 if values fit, else ValueError
  ============================  =========================

Shape policy: 2-D arrays publish as single-slice volumes (Z=1); 1-D and >3-D
arrays are refused. The shape of the published object comes from the array,
never from the reference (the Fiber Tracer trap: ``copyShapeFromStructuredGrid``
on a *populated* channel re-initializes the buffer in the template's type —
here shape is only ever copied onto an EMPTY channel, before the fill).

Progress contract (``progress`` argument of the publish functions):

  * ``True`` (default) — this module creates, starts, closes and deletes a
    real ``ORSModel.ors.Progress`` (caption + ``step_count`` range,
    cancellable);
  * ``None`` — silent, no progress object (the headless / test seam);
  * a Progress object (real or duck-typed) — caller-owned: it is started
    best-effort (never twice) and never deleted by this module. The bridge
    service (round 8) hands the publish handlers a duck progress that
    forwards each step to the notebook application over the pipe, with
    cancellation driven by the app's cancel frames — so the 3-step
    progress with its cancel checkpoints is preserved on the bridge path
    without ever opening a native ORS progress dialog.

Each of the 3 steps runs ``getIsCancelled()`` exactly once, BEFORE its work
(the plugin-canonical loop: check, work, ``updateProgress``) — so a cancel
aborts before the next object is created or filled, and a completed step is
never rolled back. A cancel raises ``RuntimeError('Operation cancelled by
user.')`` and every scratch object is deleted. Duck-typed progress objects
are never forwarded to native ORS calls (only real ``Progress`` instances
are).

The ``pump`` argument (round 8): the depth-0 hosts — the Dragonfly Python
console, the launcher-driven publishes, direct API use — keep the per-step
event pump that keeps the progress dialog repainting; a notebook-originated
bridge request runs with ``pump=False`` and NEVER pumps the event loop
mid-handler (an unrestricted ``processEvents()`` on Dragonfly's GUI thread
while a request is in flight is the round-6 crash class: it delivers ORS's
deferred live-UI work re-entrantly into the handler).

Every scratch ORS object is ``deleteObject()``-ed in a ``finally``.

Since round 8 the notebook editor and its kernel live in a separate
application process and reach this module only through the bridge handlers
(``BRIDGE_HANDLERS`` at the bottom of this module): every ORS call happens
on Dragonfly's GUI thread, dispatched by ``bridge_service``.
"""

import os

# Same multiple-OpenMP-runtime workaround as the rest of the plugin: MUST
# precede the numpy import.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import numpy as np

from ORSModel import Channel, ROI, MultiROI, Managed, Progress
from ORSModel import createChannelFromNumpyArray, orsObj
from OrsHelpers.arrayhelper import ArrayHelper
from OrsHelpers.datasethelper import DatasetHelper

__all__ = [
    'import_volume_as_np', 'import_roi_as_np', 'import_multiroi_as_np',
    'publish_np_as_volume', 'publish_np_as_roi', 'publish_np_as_multiroi',
    'import_measured_properties', 'export_measured_properties',
    'list_measured_properties', 'list_objects', 'delete_object',
]

_CANCEL_MESSAGE = 'Operation cancelled by user.'


# ---------------------------------------------------------------------------
# object resolution
# ---------------------------------------------------------------------------

def _class_label(expected_class):
    """The human name used in every resolution error message — the Python
    class name (``getClassNameStatic`` returns the internal 'CxvChannel'
    spelling users never see)."""
    return getattr(expected_class, '__name__', 'object')


def _validate_class(obj, expected_class, expected_label, source):
    """Raises a clear ValueError when ``obj`` is not a(n instance of a
    subclass of) ``expected_class``."""
    if isinstance(obj, expected_class):
        return
    raise ValueError(
        f"dfnb expected a {expected_label}, got {_class_label(type(obj))}. "
        f"Pass a {expected_label} object, its GUID or its exact title.")


def _iter_instances_of(expected_class):
    """Every live instance of the class (the getAllObjectsOfClass seam)."""
    return list(expected_class.getAllObjectsOfClass(
        expected_class.getClassNameStatic()))


#: The classes dfnb addresses by title — the representable objects the
#: Dragonfly UI offers. The cross-class resolution (``delete_object``) and
#: the cross-class error scans enumerate exactly these, through the same
#: per-class machinery ``list_objects`` uses.
_ADDRESSABLE_CLASSES = (Channel, ROI, MultiROI)


def _grouped_class_counts(matches):
    """The count-accurate class grouping of resolution matches —
    ``'1 ROI, 2 MultiROIs'`` — sorted by class name; a count of 1 keeps the
    class name unpluralized the way the resolution messages do."""
    counts = {}
    for _obj, cls in matches:
        counts[cls] = counts.get(cls, 0) + 1
    return ', '.join(
        f"{count} {_class_label(cls)}{'s' if count > 1 else ''}"
        for cls, count in sorted(
            counts.items(), key=lambda item: _class_label(item[0])))


def _title_carriers(text, expected_class):
    """Representable objects of the OTHER addressable classes carrying the
    exact title — the cross-class scan behind the round-12 distinct
    duplicate-name errors. Empty when nothing outside ``expected_class``
    carries the title (the generic no-object error then stays)."""
    carriers = []
    for cls in _ADDRESSABLE_CLASSES:
        if cls is expected_class:
            continue
        for obj in _iter_instances_of(cls):
            try:
                if not obj.getIsRepresentable():
                    continue
                if obj.getTitle() == text:
                    carriers.append((obj, cls))
            except Exception:
                continue  # a dying/deleted object must not break the search
    return carriers


def _resolve(source, expected_class, expected_label):
    """Resolves ``source`` (instance | GUID string | title string) to an
    object of ``expected_class`` — or, with ``expected_class=None``, to any
    representable object of the addressable classes (the cross-class
    resolution ``delete_object`` uses). Titles only match representable
    objects; GUIDs match any live object. Ambiguity and wrong classes
    raise."""
    if expected_class is not None and isinstance(source, expected_class):
        return source
    if expected_class is None and isinstance(source, Managed):
        return source
    if not isinstance(source, str):
        if expected_class is None:
            raise ValueError(
                f"dfnb expected an object, got {type(source).__name__}. "
                f"Pass an object, its GUID or its exact title.")
        raise ValueError(
            f"dfnb expected a {expected_label}, got "
            f"{type(source).__name__}. Pass a {expected_label} object, its "
            f"GUID or its exact title.")
    text = source.strip()
    if not text:
        if expected_class is None:
            raise ValueError(
                "dfnb expects an object or the GUID/title of one; got an "
                "empty string.")
        raise ValueError(
            f"dfnb expects a {expected_label} object or the GUID/title of one; "
            f"got an empty string.")
    # GUIDs resolve first (they are unambiguous); an unknown GUID yields None.
    try:
        by_guid = orsObj(text)
    except Exception:
        by_guid = None
    if by_guid is not None:
        if expected_class is not None:
            _validate_class(by_guid, expected_class, expected_label, source)
        return by_guid
    # Title search over the representable instances — of the expected class,
    # or of every addressable class when none is given.
    if expected_class is None:
        candidates = [(obj, cls) for cls in _ADDRESSABLE_CLASSES
                      for obj in _iter_instances_of(cls)]
    else:
        candidates = [(obj, expected_class)
                      for obj in _iter_instances_of(expected_class)]
    matches = []
    for obj, cls in candidates:
        try:
            if not obj.getIsRepresentable():
                continue
            if obj.getTitle() == text:
                matches.append((obj, cls))
        except Exception:
            continue  # a dying/deleted object must not break the search
    if len(matches) == 1:
        return matches[0][0]
    if not matches:
        if expected_class is None:
            raise ValueError(
                f"No object with the title or GUID {text!r} exists in the "
                f"open Dragonfly session.")
        if expected_class is not None:
            # the round-12 blind spot: the name DOES exist, on another
            # class — name the carriers instead of the misleading generic
            # no-object error
            carriers = _title_carriers(text, expected_class)
            if carriers:
                label = _class_label(expected_class)
                raise ValueError(
                    f"No representable {label} is named {text!r} — that "
                    f"name is carried by {_grouped_class_counts(carriers)}. "
                    f"Please rename the other object(s) or use the {label}'s "
                    f"own title or GUID.")
        raise ValueError(
            f"No {_class_label(expected_class)} with the title or GUID {text!r} "
            f"was found. Use dfnb.list_objects({_class_label(expected_class)!r}) "
            f"to list the titles and GUIDs of the available objects.")
    listed = ', '.join(f"{o.getTitle()} ({o.getGUID()})"
                       for o, _cls in matches)
    if expected_class is None:
        raise ValueError(
            f"Multiple objects named {text!r} exist "
            f"({_grouped_class_counts(matches)}). Please rename all but "
            f"one of them, or pass the GUID of the one you meant: {listed}")
    raise ValueError(
        f"Multiple objects named {text!r} exist ({len(matches)} "
        f"{_class_label(expected_class)}s). Please rename all but one of "
        f"them, or pass the GUID of the one you meant: {listed}")


def _check_timestep(timestep):
    if isinstance(timestep, bool) or not isinstance(timestep, (int, np.integer)) \
            or int(timestep) < 0:
        raise ValueError(
            f"timestep must be a non-negative integer; got {timestep!r}.")
    return int(timestep)


def _check_timestep_in_range(timestep, t_size, what):
    if timestep >= t_size:
        raise ValueError(
            f"timestep {timestep} is out of range: {what} has {t_size} "
            f"timestep(s).")


# ---------------------------------------------------------------------------
# list helpers
# ---------------------------------------------------------------------------

def list_objects(class_name='Channel', representable_only=True):
    """Lists the live ORS objects of a class as dictionaries.

    :param class_name: an ORS class name, e.g. ``'Channel'``, ``'ROI'``,
        ``'MultiROI'`` (anything resolvable in ``ORSModel``).
    :param representable_only: when True (the default) temporary / scratch
        objects are left out — those are exactly the ones the Dragonfly UI
        offers, so they are the ones a title/GUID can address.
    :return: a list of ``{'guid': ..., 'title': ..., 'class_name': ...}``
        dicts sorted by title (then GUID).
    """
    if not isinstance(class_name, str) or not class_name.strip():
        raise ValueError(
            f"class_name must be an ORS class name such as 'Channel', 'ROI' "
            f"or 'MultiROI'; got {class_name!r}.")
    name = class_name.strip()
    cls = getattr(_ORSModelModule(), name, None)
    if cls is None or not isinstance(cls, type) \
            or not hasattr(cls, 'getClassNameStatic'):
        raise ValueError(
            f"Unknown ORS class {class_name!r}. Try 'Channel', 'ROI' or "
            f"'MultiROI'.")
    out = []
    for obj in _iter_instances_of(cls):
        try:
            if representable_only and not obj.getIsRepresentable():
                continue
            out.append({'guid': obj.getGUID(), 'title': obj.getTitle(),
                        'class_name': name})
        except Exception:
            continue  # a dying/deleted object must not break the listing
    out.sort(key=lambda entry: (entry['title'], entry['guid']))
    return out


def delete_object(source):
    """Deletes one object from the open Dragonfly session — notebook
    housekeeping: a stale same-titled object from an earlier run can be
    removed before a new one is published with the same title.

    :param source: an ORS object instance, or its GUID, or its exact title.
        Unlike the typed functions there is no expected class here: a title
        matches ANY representable object of the classes dfnb addresses
        (Channel, ROI, MultiROI); an ambiguous title raises with every
        match's class and GUID.
    :return: None.
    :raises ValueError: when nothing carries the title/GUID, or when several
        objects do (rename all but one, or pass the GUID of the one meant).
    """
    obj = _resolve(source, None, 'object')
    obj.deleteObject()


def _ORSModelModule():
    import ORSModel
    return ORSModel


# ---------------------------------------------------------------------------
# array conforming (the dtype / shape policy)
# ---------------------------------------------------------------------------

def _smallest_uint_type(max_value, what):
    if max_value <= 255:
        return np.uint8
    if max_value <= 65535:
        return np.uint16
    if max_value <= 4294967295:
        return np.uint32
    raise ValueError(
        f"{what} holds values up to {max_value}, which exceed the largest "
        f"ORS channel type (uint32). Clip or scale the data first.")


def _conform_array_for_channel(array, what):
    """Validates shape (2-D or 3-D) and converts ``array`` to one of the four
    ORS channel dtypes (uint8/uint16/uint32/float32) per the module dtype
    policy. Returns a C-contiguous copy safe to hand to
    ``createChannelFromNumpyArray`` (which silently shifts signed arrays by
    their minimum — the conforming must never let one through)."""
    a = np.asarray(array)
    if a.ndim not in (2, 3):
        raise ValueError(
            f"{what} must be a 2-D (Y, X) or 3-D (Z, Y, X) numpy array; "
            f"got a {a.ndim}-D array of shape {tuple(a.shape)}.")
    if a.size == 0:
        raise ValueError(f"{what} is empty (shape {tuple(a.shape)}).")
    dtype = a.dtype
    if dtype == np.float32 or dtype in (np.uint8, np.uint16, np.uint32):
        out = a
    elif dtype == np.bool_:
        out = a.astype(np.uint8)
    elif dtype in (np.float16, np.float64):
        out = a.astype(np.float32)  # ORS channels have no 16/64-bit floats
    elif dtype in (np.int8, np.int16, np.int32, np.int64):
        if int(a.min()) >= 0:
            out = a.astype(_smallest_uint_type(int(a.max()), what))
        else:
            # createChannelFromNumpyArray shifts signed data by its minimum,
            # which would rewrite every value: float32 preserves them.
            out = a.astype(np.float32)
    elif dtype == np.uint64:
        out = a.astype(_smallest_uint_type(int(a.max()), what))
    else:
        raise ValueError(
            f"{what} has unsupported dtype {dtype}. Use one of uint8, "
            f"uint16, uint32, float32 — or anything the dfnb dtype policy "
            f"converts (bool, float16/64, signed ints, uint64).")
    return np.ascontiguousarray(out)


def _conform_labels_for_multiroi(array, what):
    """Validates a labels array (whole numbers, >= 0, at least one labeled
    voxel) and routes its dtype by label count — the same routing
    ``MultiROI.getAsNDArray`` uses, so an imported MultiROI re-publishes in
    its original dtype: <= 254 labels -> uint8, <= 65534 -> uint16,
    otherwise uint32 (never cast below what the largest label needs)."""
    a = np.asarray(array)
    if a.ndim not in (2, 3):
        raise ValueError(
            f"{what} must be a 2-D (Y, X) or 3-D (Z, Y, X) numpy array; "
            f"got a {a.ndim}-D array of shape {tuple(a.shape)}.")
    if a.size == 0:
        raise ValueError(f"{what} is empty (shape {tuple(a.shape)}).")
    kind = a.dtype.kind
    if kind == 'b':
        labels = a.astype(np.float64)
    elif kind == 'f':
        if not bool(np.isfinite(a).all()):
            raise ValueError(f"{what} contains NaN or infinity; labels must "
                             f"be finite whole numbers.")
        rounded = np.round(a)
        deviation = np.abs(a - rounded)
        if bool((deviation > 0).any()):
            worst = int(np.argmax(deviation))
            bad = float(np.asarray(a, dtype=np.float64).ravel()[worst])
            raise ValueError(
                f"{what} contains non-integer labels (e.g. {bad!r}); "
                f"round or cast the labels first.")
        labels = rounded
    elif kind in 'iu':
        labels = a.astype(np.float64)
    else:
        raise ValueError(
            f"{what} has unsupported dtype {a.dtype}; labels must be whole "
            f"numbers (integer, float or boolean).")
    if int(labels.min()) < 0:
        raise ValueError(f"{what} contains negative values; labels must be "
                         f"0 (background) or positive integers.")
    label_count = int(labels.max())
    if label_count == 0:
        raise ValueError(
            f"{what} contains no labels (every voxel is 0); nothing would be "
            f"segmented.")
    target = _label_dtype(label_count)
    return np.ascontiguousarray(labels.astype(target)), label_count


def _label_dtype(label_count):
    if label_count <= 254:
        return np.uint8
    if label_count <= 65534:
        return np.uint16
    return np.uint32


def _resolve_reference(reference, what):
    if reference is None:
        return None
    return _resolve(reference, Channel, what)


def _check_spacing(spacing):
    if spacing is None:
        return None
    try:
        values = tuple(float(v) for v in spacing)
    except (TypeError, ValueError):
        raise ValueError(
            f"spacing must be None or a sequence of three positive numbers "
            f"(spacing_x, spacing_y, spacing_z); got {spacing!r}.") from None
    if len(values) != 3 or not all(np.isfinite(values)) or not all(v > 0 for v in values):
        raise ValueError(
            f"spacing must be None or a sequence of three positive numbers "
            f"(spacing_x, spacing_y, spacing_z); got {spacing!r}.")
    return values


# ---------------------------------------------------------------------------
# progress seam
# ---------------------------------------------------------------------------

def _pump_events():
    """One Qt event-loop pass so the ORS progress dialog repaints and its
    Cancel button stays reachable during a long publish. Best-effort by
    rule: no QApplication (the headless tiers) means a no-op, and a failing
    pump must never break a publish.

    Round 8: notebook cells no longer run inside this process -- they run on
    a worker thread in the standalone notebook application -- so the old
    notebook-run suppression is gone. A notebook-originated BRIDGE request
    instead disables this pump entirely (``pump=False`` through
    ``_ProgressSession``): pumping here would deliver Dragonfly's deferred
    live-UI work re-entrantly into the handler on the GUI thread -- the
    round-6 crash class -- and there is no native progress dialog to
    repaint anyway (the progress travels to the app as bridge events)."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


class _ProgressSession(object):
    """Normalizes the three-way progress contract into check/update pairs
    around each step's work: ``check()`` (exactly one ``getIsCancelled``)
    runs BEFORE the step's work, ``update()`` (one ``updateProgress``) after
    it — a cancel therefore aborts before anything more is created.

    ``pump=False`` (the bridge path) stands the per-step event pump down:
    a notebook-originated request never pumps Dragonfly's event loop."""

    def __init__(self, progress, caption, step_count, pump=True):
        self._pump = bool(pump)
        self._owned = False
        if progress is True:
            progress = Progress()
            self._owned = True
        self._progress = progress
        # Only real ORS Progress objects are forwarded to native ORS calls;
        # duck-typed test doubles are not.
        self._native = progress if isinstance(progress, Progress) else None
        self._done = 0
        if progress is not None:
            try:
                progress.startProgressWithCaption(caption, int(step_count), True)
            except Exception:
                pass  # a caller-owned progress may already be running

    @property
    def native(self):
        """The real ORS Progress to hand to native ORS calls, or None."""
        return self._native

    def check(self):
        """Pumps the Qt event loop once unless the pump stands down (the
        bridge path), then exactly one ``getIsCancelled()`` per step, before
        its work."""
        if self._pump:
            _pump_events()
        if self._progress is not None and self._progress.getIsCancelled():
            raise RuntimeError(_CANCEL_MESSAGE)

    def update(self):
        """Marks one step done (``updateProgress`` with the 1-based index)."""
        if self._progress is not None:
            try:
                self._progress.updateProgress(self._done + 1)
            except Exception:
                pass  # a caller-owned progress may already be closed
        self._done += 1

    def close(self):
        """Closes and deletes a lib-owned progress (the ORS recipe:
        ``closeProgress()`` then ``deleteObject()``); never a caller-owned
        one. The publish functions publish INSIDE their ``try`` (a raise at
        publish time must leave nothing behind), so this close runs after
        the result is published."""
        if self._owned and self._progress is not None:
            try:
                self._progress.closeProgress()
            except Exception:
                pass
            try:
                self._progress.deleteObject()
            except Exception:
                pass
            self._owned = False


def _delete_quietly(obj):
    try:
        obj.deleteObject()
    except Exception:
        pass


def _apply_spacing(grid, spacing, reference_channel):
    """spacing argument > reference geometry > isotropic 1.0 default. The
    reference's spacing is re-applied explicitly (instead of trusting what
    survived the fill) so the geometry contract holds regardless of ORS
    internals."""
    if spacing is not None:
        sx, sy, sz = spacing
        grid.setXSpacing(sx)
        grid.setYSpacing(sy)
        grid.setZSpacing(sz)
    elif reference_channel is not None:
        grid.setXSpacing(reference_channel.getXSpacing())
        grid.setYSpacing(reference_channel.getYSpacing())
        grid.setZSpacing(reference_channel.getZSpacing())
    else:
        grid.setXSpacing(1.0)
        grid.setYSpacing(1.0)
        grid.setZSpacing(1.0)


def _pick_title(title, default_title):
    if title is None:
        return default_title
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"title must be a non-empty string; got {title!r}.")
    return title.strip()


# ---------------------------------------------------------------------------
# imports (ORS -> numpy)
# ---------------------------------------------------------------------------

def import_volume_as_np(source, timestep=0):
    """Imports a Channel's voxel data as an independent ``(Z, Y, X)`` array.

    :param source: a Channel object, or its GUID, or its exact title.
    :param timestep: the timestep to read (0 on single-timestep volumes).
    :return: a fresh ``numpy.ndarray`` — a real copy: editing it never edits
        the Dragonfly volume.
    """
    channel = _resolve(source, Channel, 'Channel')
    t = _check_timestep(timestep)
    _check_timestep_in_range(t, channel.getTSize(), 'the volume')
    # getNDArray returns a live numpy view over the channel's raw memory:
    # copy it, so cell-side numpy edits can never write through to the volume.
    return np.array(channel.getNDArray(t), copy=True)


def import_roi_as_np(source, timestep=0):
    """Imports a ROI as a ``(Z, Y, X)`` ``uint8`` mask (0 background, 1 inside).

    :param source: a ROI object, or its GUID, or its exact title.
    :param timestep: the timestep to read (0 on single-timestep ROIs).
    """
    roi = _resolve(source, ROI, 'ROI')
    t = _check_timestep(timestep)
    _check_timestep_in_range(t, roi.getTSize(), 'the ROI')
    return np.array(roi.getAsNDArray(t), copy=True)


def import_multiroi_as_np(source, timestep=0):
    """Imports a MultiROI as a ``(Z, Y, X)`` labels array.

    The dtype follows the label count, exactly like Dragonfly's own
    ``MultiROI.getAsNDArray``: <= 254 labels -> uint8, <= 65534 -> uint16,
    otherwise uint32.

    :param source: a MultiROI object, or its GUID, or its exact title.
    :param timestep: the timestep to read (0 on single-timestep MultiROIs).
    """
    multiroi = _resolve(source, MultiROI, 'MultiROI')
    t = _check_timestep(timestep)
    _check_timestep_in_range(t, multiroi.getTSize(), 'the MultiROI')
    return np.array(multiroi.getAsNDArray(t), copy=True)


# ---------------------------------------------------------------------------
# publishes (numpy -> ORS)
# ---------------------------------------------------------------------------

def publish_np_as_volume(array, title=None, reference=None, spacing=None,
                         progress=True, pump=True):
    """Publishes a numpy array as a new Channel (a volume) in the session.

    :param array: a 2-D (Y, X) or 3-D (Z, Y, X) array; the dtype policy in
        the module docstring decides the channel type.
    :param title: the new volume's title (default ``'Result volume'``).
    :param reference: optional Channel (or GUID/title) whose world geometry
        (origin, spacing, orientation) the new volume inherits; the voxel
        size still comes from ``array``.
    :param spacing: optional ``(sx, sy, sz)``; wins over the reference's
        spacing. Without either, 1.0 isotropic.
    :param progress: ``True`` (default) | ``None`` | a Progress object.
    :param pump: when False (the bridge path) the per-step event pump is
        stood down — a notebook-originated request never pumps events.
    :return: the new Channel (representable and published).
    """
    spacing = _check_spacing(spacing)
    session = _ProgressSession(progress, 'Publishing numpy array as volume', 3,
                               pump=pump)
    channel = None  # non-None only once tmp has become the kept result
    tmp = None
    try:
        # step 1 — prepare: validate + conform the array, resolve the reference
        session.check()
        arr = _conform_array_for_channel(array, 'The volume array')
        reference_channel = _resolve_reference(reference, 'reference volume')
        session.update()
        # step 2 — create the EMPTY output channel and give it the world
        # geometry (on a populated channel copyShapeFromStructuredGrid would
        # re-initialize the buffer in the reference's type: the Fiber Tracer
        # trap)
        session.check()
        tmp = Channel()
        if reference_channel is not None:
            tmp.copyShapeFromStructuredGrid(reference_channel)
        session.update()
        # step 3 — write the voxel data and finalize
        session.check()
        createChannelFromNumpyArray(arr, channelGUID=tmp.getGUID(), ZAxis=True)
        _apply_spacing(tmp, spacing, reference_channel)
        tmp.setTitle(_pick_title(title, 'Result volume'))
        tmp.publish()
        # publish INSIDE the try: a raise here must leave nothing behind --
        # the finally deletes tmp while `channel` (the kept-result marker) is
        # still None (the one creation path the round-7 review closed)
        channel = tmp
        session.update()
    finally:
        if channel is None and tmp is not None:
            _delete_quietly(tmp)
        session.close()
    return channel


def publish_np_as_roi(array, title=None, reference=None, spacing=None,
                      progress=True, pump=True):
    """Publishes a numpy mask as a new ROI.

    Every non-zero voxel becomes ROI. The mask is first filled into a
    scratch Channel which ``StructuredGrid.getAsROI`` converts; the scratch
    channel is deleted in a ``finally``.

    :param array: a 2-D (Y, X) or 3-D (Z, Y, X) mask; 0 is background.
    :param title: the new ROI's title (default ``'Processed ROI'``).
    :param reference / spacing / progress / pump: as in
        ``publish_np_as_volume``.
    :return: the new ROI (representable and published).
    """
    spacing = _check_spacing(spacing)
    session = _ProgressSession(progress, 'Publishing numpy array as ROI', 3,
                               pump=pump)
    roi = None
    result = None  # non-None only once the ROI is titled and published
    tmp = None
    try:
        # step 1 — prepare: validate + conform the mask, resolve the reference
        session.check()
        mask = _conform_array_for_channel(array, 'The ROI mask')
        if not bool(mask.any()):
            raise ValueError(
                'The ROI mask contains no foreground voxels (every value is '
                '0); an empty ROI would not be created.')
        reference_channel = _resolve_reference(reference, 'reference volume')
        session.update()
        # step 2 — scratch channel + world geometry
        session.check()
        tmp = Channel()
        if reference_channel is not None:
            tmp.copyShapeFromStructuredGrid(reference_channel)
        session.update()
        # step 3 — fill the scratch channel and convert it to a ROI
        session.check()
        createChannelFromNumpyArray(mask, channelGUID=tmp.getGUID(), ZAxis=True)
        _apply_spacing(tmp, spacing, reference_channel)
        roi = tmp.getAsROI(None, session.native)
        if roi is not None:
            # the title is picked INSIDE the try: _pick_title raises
            # ValueError on a non-str / whitespace-only title, and a raise
            # here must leave nothing behind (the freshly created ROI is
            # deleted in the finally below)
            roi.setTitle(_pick_title(title, 'Processed ROI'))
            roi.publish()
            # a raise INSIDE publish must leave nothing behind: the finally's
            # `result is None` branch deletes the fresh ROI -- `result` is the
            # kept-result marker, assigned only once the publish succeeded
            result = roi
        session.update()
    finally:
        if tmp is not None:
            _delete_quietly(tmp)  # the scratch channel never survives
        if result is None and roi is not None:
            _delete_quietly(roi)  # a failure after creation leaves nothing behind
        session.close()
    if result is None:
        if session.native is not None:
            # the native conversion was given a real progress: a None result
            # is a cancelled conversion
            raise RuntimeError(_CANCEL_MESSAGE)
        raise ValueError('Dragonfly did not return a ROI for this mask.')
    return result


def publish_np_as_multiroi(array, title=None, reference=None, spacing=None,
                           progress=True, pump=True, properties=None):
    """Publishes a numpy labels array as a new MultiROI.

    Uses the DatasetHelper recipe: the labels are filled into a scratch
    Channel, ``createMultiROIFromDataset`` turns it into a MultiROI, and the
    label count is pinned to the largest label (so label values stay their
    identity through import -> publish round trips).

    :param array: a 2-D (Y, X) or 3-D (Z, Y, X) labels array: 0 is
        background, every other whole number is a label.
    :param title: the new MultiROI's title (default ``'New segmentation'``).
    :param reference / spacing / progress / pump: as in
        ``publish_np_as_volume``.
    :param properties: optional measured properties written onto the fresh
        MultiROI before it is returned — a ``pandas.DataFrame`` (one column
        per property) or a ``dict`` ``{name: array-like or {label: value}}``
        (the per-region dict keyed by the labels 1..labelCount — see
        ``export_measured_properties``); the same validation, auto-padding
        and name dedupe as ``export_measured_properties`` (a rejected call
        raises ValueError and leaves NOTHING behind — the fresh MultiROI is
        deleted again). There is deliberately NO ``wipe`` parameter here:
        the write always lands on a FRESH 0-slot MultiROI, so a wipe would
        be a no-op — replace-all only ever applies through
        ``export_measured_properties(wipe=True)`` onto an existing object.
    :return: the new MultiROI (representable and published).
    """
    spacing = _check_spacing(spacing)
    session = _ProgressSession(progress, 'Publishing numpy array as multiROI',
                               3, pump=pump)
    multiroi = None
    result = None  # non-None only once the MultiROI is titled and published
    tmp = None
    label_count = 0
    try:
        # step 1 — prepare: validate + route the labels, resolve the reference
        session.check()
        labels, label_count = _conform_labels_for_multiroi(
            array, 'The labels array')
        reference_channel = _resolve_reference(reference, 'reference volume')
        session.update()
        # step 2 — scratch channel + world geometry
        session.check()
        tmp = Channel()
        if reference_channel is not None:
            tmp.copyShapeFromStructuredGrid(reference_channel)
        session.update()
        # step 3 — fill the scratch channel and convert it to a MultiROI
        # (the BounTI recipe)
        session.check()
        createChannelFromNumpyArray(labels, channelGUID=tmp.getGUID(),
                                    ZAxis=True)
        _apply_spacing(tmp, spacing, reference_channel)
        multiroi = DatasetHelper.createMultiROIFromDataset(
            tmp, None, session.native)
        if multiroi is not None:
            # the title is picked INSIDE the try: _pick_title raises
            # ValueError on a non-str / whitespace-only title, and a raise
            # here must leave nothing behind (the freshly created MultiROI
            # is deleted in the finally below)
            multiroi.setTitle(_pick_title(title, 'New segmentation'))
            multiroi.setLabelCount(label_count)
            if properties is not None:
                # the property write validates EVERY column before the first
                # slot is grown; a raise here leaves ``result`` None, so the
                # finally deletes the fresh MultiROI -- the failure path
                # creates nothing (same contract as a rejected title)
                _write_measured_properties(multiroi, properties, 0)
            multiroi.publish()
            # same contract as the ROI path: a publish-time raise leaves nothing
            # behind (the finally deletes the fresh MultiROI while `result` is
            # still None)
            result = multiroi
        session.update()
    finally:
        if tmp is not None:
            _delete_quietly(tmp)  # the scratch channel never survives
        if result is None and multiroi is not None:
            _delete_quietly(multiroi)  # a failure after creation leaves nothing behind
        session.close()
    if result is None:
        if session.native is not None:
            # the native conversion was given a real progress: a None result
            # is a cancelled conversion
            raise RuntimeError(_CANCEL_MESSAGE)
        raise ValueError(
            'Dragonfly did not return a MultiROI for these labels.')
    return result


# ---------------------------------------------------------------------------
# measured properties (MultiROI scalar values)
# ---------------------------------------------------------------------------

def list_measured_properties(source, timestep=0):
    """Lists a MultiROI's measured-property column names, in slot order.

    :param source: a MultiROI object, or its GUID, or its exact title.
    :param timestep: the timestep whose slot descriptions to read.
    :return: the list of column names ``import_measured_properties`` accepts.
    """
    multiroi = _resolve(source, MultiROI, 'MultiROI')
    t = _check_timestep(timestep)
    _check_timestep_in_range(t, multiroi.getTSize(), 'the MultiROI')
    return [multiroi.getScalarValueDescription(slot, t)
            for slot in range(multiroi.getScalarValuesSlotCount())]


def import_measured_properties(source, properties=None, timestep=0):
    """Imports a MultiROI's measured properties as a ``pandas.DataFrame``.

    One row per label slot — row 0 is the background slot Dragonfly
    reserves, rows 1..N are labels 1..N — and one column per measured
    property. For the WRITE path (custom property names onto an existing
    or a freshly published MultiROI) see ``export_measured_properties``:
    ``MultiROI.setScalarValuesFromPandaDataFrame`` itself is broken in the
    ORS framework and is deliberately not exposed.

    :param source: a MultiROI object, or its GUID, or its exact title.
    :param properties: optional list of column names to keep (default: all
        of them, in slot order); an unknown name raises with the available
        ones listed.
    :param timestep: the timestep to read (0 on single-timestep MultiROIs).
    :return: a ``pandas.DataFrame``.
    """
    multiroi = _resolve(source, MultiROI, 'MultiROI')
    t = _check_timestep(timestep)
    _check_timestep_in_range(t, multiroi.getTSize(), 'the MultiROI')
    frame = multiroi.getScalarValuesAsPandaDataFrame(t)
    if properties is None:
        return frame
    if isinstance(properties, str):
        properties = [properties]
    available = [str(column) for column in frame.columns]
    missing = [str(p) for p in properties if str(p) not in available]
    if missing:
        raise ValueError(
            f"Measured property column(s) {missing} not found. Available "
            f"columns: {available}. Use "
            f"dfnb.list_measured_properties({source!r}) to list them.")
    return frame[[str(p) for p in properties]]


# ---------------------------------------------------------------------------
# measured-property WRITES (the export path)
# ---------------------------------------------------------------------------

def _label_keyed_values(values, label_count, name):
    """A ``{label: value}`` column's values, in label order.

    The natural cell-side shape for per-region measurements — a dict
    keyed by the label numbers 1..labelCount (one entry per region), the
    shape regionprops-driven cell code produces. Keys may be ints or
    anything ``int()`` maps onto the labels 1..N; any other key set is
    rejected with a message that names the property.
    """
    try:
        keys = [int(k) for k in values.keys()]
    except (TypeError, ValueError):
        keys = None
    if keys is None or sorted(keys) != list(range(1, label_count + 1)):
        shown = sorted(map(repr, values.keys()))[:8]
        if len(values) > 8:
            shown.append('...')
        raise ValueError(
            f"a dict-valued measured property maps each label "
            f"1..{label_count} to one value; property {name!r} keys are "
            f"{shown}.") from None
    return [values[k] for k in sorted(values, key=lambda k: int(k))]


def _normalize_property_columns(properties, label_count):
    """Validates and normalizes the ``properties`` argument of the measured-
    property write into ``[(name, float64 array of label_count + 1)]``.

    PRE-FLIGHT by contract: every column is validated here, BEFORE any slot
    is grown, so a rejected call mutates nothing.

      * a ``pandas.DataFrame`` contributes one property per column (rows in
        order, row 0 = background); a duplicate column NAME is two
        properties (the write dedupes the slot names);
      * a ``dict`` ``{name: array-like}`` contributes one property per
        key — and a column whose values are THEMSELVES a ``dict`` must
        map each label ``1..labelCount`` to one value (``_label_keyed_
        values`` orders them into a column);
      * anything else is a ValueError.

    Each column must carry ``label_count + 1`` values (index 0 = the
    background row Dragonfly reserves) or exactly ``label_count`` values —
    the latter is auto-padded with a 0.0 background row in FRONT (rows
    1..N are labels 1..N). Empty / whitespace-only names are refused (an
    empty-name slot vanishes from the properties-pane comboboxes).
    """
    import pandas as pd

    if isinstance(properties, pd.DataFrame):
        items = [(str(column), properties.iloc[:, index].to_numpy())
                 for index, column in enumerate(properties.columns)]
    elif isinstance(properties, dict):
        items = [(str(name), values) for name, values in properties.items()]
    else:
        raise ValueError(
            "properties must be a pandas.DataFrame (one column per "
            "property) or a dict {name: values}; got "
            f"{type(properties).__name__}.")

    expected = label_count + 1
    normalized = []
    for name, values in items:
        if not name.strip():
            raise ValueError(
                f"measured property names must be non-empty (an empty-name "
                f"slot vanishes from the properties pane); got {name!r}.")
        if isinstance(values, dict):
            values = _label_keyed_values(values, label_count, name)
        try:
            column = np.asarray(values, dtype=np.float64)
        except (TypeError, ValueError):
            raise ValueError(
                f"the values of measured property {name!r} are not "
                f"convertible to a float64 array.") from None
        if column.ndim != 1:
            raise ValueError(
                f"the values of measured property {name!r} must be "
                f"one-dimensional; got {column.ndim} dimensions.")
        if len(column) == label_count:
            # one value per label (background row omitted): pad a 0.0
            # background row in FRONT (index 0 is the background slot)
            column = np.concatenate((np.zeros(1, np.float64), column))
        elif len(column) != expected:
            raise ValueError(
                f"the values of measured property {name!r} must carry "
                f"{expected} entries (labelCount + 1, row 0 = background) "
                f"or {label_count} (auto-padded with the 0.0 background "
                f"row); got {len(column)}.")
        normalized.append((name, np.ascontiguousarray(column, np.float64)))
    return normalized


def _write_measured_properties(multiroi, properties, timestep, wipe=False):
    """The shared slot-write path behind ``export_measured_properties`` and
    ``publish_np_as_multiroi(properties=...)``.

    The ORS write order (the shipped ScalarGenerator's proven pattern,
    multiroibasicmeasurements.py:805-827 — MINUS its double
    ``deleteObject``): for each property, GROW the slot count first (the
    slot count is global while descriptions/values are per (slot,
    timestep)), write the description, then the float64 values through
    ``ArrayHelper.ConvertNumpyToOrsArray`` (float64 -> ArrayDouble), and
    delete the converted array immediately — ALWAYS exactly once. After
    all slots: ``setPropertyDirty()`` and
    ``OrsLabeledMultiRoiScalarValueHelper.initializeScalarValuesRangesFromValues``
    so the properties pane shows each column's min/max.

    Two modes. ``wipe=False`` (default) is add-with-dedupe: the new
    columns JOIN the MultiROI's existing slots. ``wipe=True`` is
    replace-all: the measured-property slot set is reset to empty FIRST
    (``multiroi.setScalarValuesSlotCount(0)`` — the round-10
    probe-confirmed clean reset: grown slots shrink to 0 and a re-grown
    set's ``getScalarValuesAsPandaDataFrame`` readback shows ONLY the new
    columns, with the ranges helper working fine after the wipe), then
    the given properties are written as the ONLY ones.

    The reset happens only AFTER the pre-flight validation: everything is
    validated up front (see ``_normalize_property_columns``) and the
    first ORS call — including the wipe — happens only when every column
    passed, so a properties payload that fails validation can never
    destroy existing data (the nothing-is-written-if-any-column-fails
    contract). Duplicate names WITHIN the batch are deduped CSV-style
    with ' (2)', ' (3)' suffixes in both modes; against existing slots
    only in add mode (after a wipe there are none).
    """
    t = _check_timestep(timestep)
    _check_timestep_in_range(t, multiroi.getTSize(), 'the MultiROI')
    label_count = int(multiroi.getLabelCount())
    columns = _normalize_property_columns(properties, label_count)

    if wipe:
        # after the reset there are no existing slots — the existing-
        # descriptions read is skipped; in-batch duplicates still dedupe
        multiroi.setScalarValuesSlotCount(0)
        taken = set()
    else:
        taken = set()
        for slot in range(multiroi.getScalarValuesSlotCount()):
            taken.add(str(multiroi.getScalarValueDescription(slot, t)))
    deduped = []
    for name, values in columns:
        unique, suffix = name, 2
        while unique in taken:
            unique = '{} ({})'.format(name, suffix)
            suffix += 1
        taken.add(unique)
        deduped.append((unique, values))

    slot = multiroi.getScalarValuesSlotCount()
    for name, values in deduped:
        multiroi.setScalarValuesSlotCount(slot + 1)
        multiroi.setScalarValueDescription(slot, name, t)
        ors_array = ArrayHelper.ConvertNumpyToOrsArray(values)
        try:
            multiroi.setScalarValues(ors_array, slot, t)
        finally:
            ors_array.deleteObject()
        slot += 1
    multiroi.setPropertyDirty()
    # the properties pane's min/max readout: the shipped helper recomputes
    # every slot's range/window min-max from the written values (and sets
    # the property dirty again)
    from OrsHelpers.scalarvaluehelper.orslabeledmultiroiscalarvaluehelper \
        import OrsLabeledMultiRoiScalarValueHelper
    OrsLabeledMultiRoiScalarValueHelper.initializeScalarValuesRangesFromValues(
        multiroi, t)


def export_measured_properties(source, properties, timestep=0, wipe=False):
    """Writes measured properties onto an EXISTING MultiROI.

    Property names are plain strings — they do NOT have to come from
    vanilla Dragonfly's measured-properties menu (that list is only the UI
    menu; slots accept any name). This is the working write path: growing
    the slot count per property, writing the description and the float64
    values per (slot, timestep), then initializing the ranges so the
    properties pane shows min/max. (``setScalarValuesFromPandaDataFrame``
    is broken in the ORS framework and is never used here.)

    :param source: a MultiROI object, or its GUID, or its exact title.
    :param properties: a ``pandas.DataFrame`` (columns -> slot names, rows
        0..labelCount) or a ``dict`` ``{name: values}`` — the values of
        one property may be any array-like (list / numpy array / pandas
        column) OR a ``dict`` ``{label: value}`` keyed by the labels
        1..labelCount (the per-region shape regionprops-driven cell code
        naturally produces). Every column is validated BEFORE anything
        is written: each carries ``labelCount + 1`` values (row 0 =
        background) or exactly ``labelCount`` values (auto-padded with a
        0.0 background row); empty / whitespace-only names are refused;
        duplicate names are deduped CSV-style with ' (2)', ' (3)'
        suffixes (also against the MultiROI's existing slots).
    :param timestep: the timestep to write (0 on single-timestep MultiROIs).
    :param wipe: ``False`` (default) adds the properties alongside the
        MultiROI's existing ones (add-with-dedupe). ``True`` is
        replace-all: the existing measured-property slot set is reset to
        empty first (``setScalarValuesSlotCount(0)`` — the round-10
        probe-confirmed clean reset) and the given properties are written
        as the ONLY ones. The reset runs only AFTER the validation: a
        payload that fails pre-flight raises with NOTHING written and the
        existing data untouched.
    :return: None — the MultiROI is modified in place, so the generated
        snippet is a bare call that echoes nothing into the cell.
    :raises ValueError: on an unresolvable source, a wrong class, or any
        invalid column — with NOTHING written (the pre-flight runs before
        the first slot is grown and, under ``wipe=True``, before the
        reset).
    """
    multiroi = _resolve(source, MultiROI, 'MultiROI')
    _write_measured_properties(multiroi, properties, timestep, wipe=wipe)


# ---------------------------------------------------------------------------
# the bridge handler layer (round 8)
# ---------------------------------------------------------------------------
# The standalone notebook application reaches this module ONLY through the
# handlers below: the bridge service dispatches one request -> one handler,
# always on Dragonfly's GUI thread. Handlers take a decoded params dict and
# a duck progress object (or None) and return a bridge-serializable payload
# dict. Every contract of the public functions is preserved verbatim: the
# publishes still run their 3-step progress with cancel checkpoints (the
# progress travels to the app as bridge events, never a native dialog), and
# every ValueError / RuntimeError text crosses back through the error
# envelope unchanged.


def _bridge_source_param(value, what):
    """Normalizes a bridge source/reference parameter into the string the
    resolvers accept: a ``{'guid', 'title', 'class_name'}`` published-object
    handle (a previous ``publish_np_as_*`` result) contributes its GUID —
    the precise handle; a plain string is its title or GUID."""
    if isinstance(value, dict):
        guid = str(value.get('guid') or '').strip()
        if guid:
            return guid
        title = str(value.get('title') or '').strip()
        if title:
            return title
        raise ValueError(
            f'The bridge carried an empty {what} handle '
            f'({value!r}); it must name an object.')
    if isinstance(value, str):
        return value
    raise ValueError(
        f'{what} must be an object title, a GUID or a published-object '
        f'handle; got {value!r}.')


def _bridge_timestep_param(params, key='timestep'):
    timestep = params.get(key, 0)
    if timestep is None:
        timestep = 0
    return timestep


def _bridge_property_keys(properties):
    """Restores the integer keys of a label-keyed properties dict: JSON
    object keys are always strings, so ``{3: 30.0}`` arrives as
    ``{'3': 30.0}`` and would fail the label-keyed validation."""
    if isinstance(properties, dict):
        restored = {}
        for key, value in properties.items():
            if isinstance(key, str):
                try:
                    key = int(key)
                except ValueError:
                    pass
            restored[key] = value
        return restored
    return properties


def _bridge_published_payload(created):
    """The ``{'published': ...}`` payload of a successful publish: the
    created object as the handle dict later source/reference parameters
    re-accept."""
    return {'published': {
        'guid': str(created.getGUID()),
        'title': str(created.getTitle() or ''),
        'class_name': _class_label(type(created)),
    }}


def _bridge_publish(params, progress, function, property_key=None):
    """One publish through its public function. The bridge's duck progress
    drives the SAME 3-step progress / cancel checkpoints (each step is
    forwarded to the app as a progress event; a native ORS dialog is never
    created because a duck progress is never forwarded to native calls),
    and ``pump=False`` stands the event pump down — the round-6 class stays
    closed on the notebook path."""
    kwargs = {
        'array': params.get('array'),
        'title': params.get('title'),
        'reference': (_bridge_source_param(params.get('reference'),
                                           'reference volume')
                      if params.get('reference') is not None else None),
        'spacing': params.get('spacing'),
        'progress': progress,
        'pump': False,
    }
    if property_key is not None and params.get(property_key) is not None:
        kwargs[property_key] = _bridge_property_keys(params[property_key])
    created = function(**kwargs)
    return _bridge_published_payload(created)


def discard_bridge_object(guid):
    """Bridge-service seam: deletes the object a GUID designates. When a
    publish completed but its response could not be delivered (the
    application disconnected mid-request), the freshly published object
    would otherwise linger in the session — this is the disconnect
    cleanup. Best-effort: a stale or already-deleted GUID is a no-op."""
    if not isinstance(guid, str) or not guid.strip():
        return
    try:
        obj = orsObj(guid.strip())
    except Exception:
        return
    if obj is None:
        return
    _delete_quietly(obj)


def _handle_import_volume_as_np(params, progress):
    return {'array': import_volume_as_np(
        _bridge_source_param(params.get('source'), 'Channel'),
        _bridge_timestep_param(params))}


def _handle_import_roi_as_np(params, progress):
    return {'array': import_roi_as_np(
        _bridge_source_param(params.get('source'), 'ROI'),
        _bridge_timestep_param(params))}


def _handle_import_multiroi_as_np(params, progress):
    return {'array': import_multiroi_as_np(
        _bridge_source_param(params.get('source'), 'MultiROI'),
        _bridge_timestep_param(params))}


def _handle_publish_np_as_volume(params, progress):
    return _bridge_publish(params, progress, publish_np_as_volume)


def _handle_publish_np_as_roi(params, progress):
    return _bridge_publish(params, progress, publish_np_as_roi)


def _handle_publish_np_as_multiroi(params, progress):
    return _bridge_publish(params, progress, publish_np_as_multiroi,
                           property_key='properties')


def _handle_list_objects(params, progress):
    return {'objects': list_objects(
        params.get('class_name'),
        bool(params.get('representable_only', True)))}


def _handle_delete_object(params, progress):
    delete_object(_bridge_source_param(params.get('source'), 'object'))
    return {}


def _handle_list_measured_properties(params, progress):
    return {'properties': list_measured_properties(
        _bridge_source_param(params.get('source'), 'MultiROI'),
        _bridge_timestep_param(params))}


def _handle_import_measured_properties(params, progress):
    table = import_measured_properties(
        _bridge_source_param(params.get('source'), 'MultiROI'),
        params.get('properties'),
        _bridge_timestep_param(params))
    return {'table': table}


def _handle_export_measured_properties(params, progress):
    export_measured_properties(
        _bridge_source_param(params.get('source'), 'MultiROI'),
        _bridge_property_keys(params.get('properties')),
        _bridge_timestep_param(params),
        wipe=bool(params.get('wipe', False)))
    return {}


#: The method table ``bridge_service`` dispatches against: method name ->
#: handler(params, progress) -> payload dict. Injected into the service at
#: construction, so the service itself stays ORS-free and unit-testable.
BRIDGE_HANDLERS = {
    'import_volume_as_np': _handle_import_volume_as_np,
    'import_roi_as_np': _handle_import_roi_as_np,
    'import_multiroi_as_np': _handle_import_multiroi_as_np,
    'publish_np_as_volume': _handle_publish_np_as_volume,
    'publish_np_as_roi': _handle_publish_np_as_roi,
    'publish_np_as_multiroi': _handle_publish_np_as_multiroi,
    'import_measured_properties': _handle_import_measured_properties,
    'export_measured_properties': _handle_export_measured_properties,
    'list_measured_properties': _handle_list_measured_properties,
    'list_objects': _handle_list_objects,
    'delete_object': _handle_delete_object,
}
