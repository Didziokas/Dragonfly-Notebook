r"""Headless data-path test of the Dragonfly Notebook bridge HANDLER layer
(``DragonflyNotebook/dragonflynotebook_lib.py`` ``BRIDGE_HANDLERS``), run
on real ORS objects -- plus the end-to-end pipe loopback.

Round 8: the notebook editor and its kernel live in their own process and
reach ORS ONLY through the bridge (``bridge_service`` dispatching one
request -> one handler, always on Dragonfly's GUI thread). The contracts
of the eleven public ``dfnb`` functions are UNCHANGED -- they are pinned here,
one layer down: the handlers take decoded bridge params (GUID/title
strings and ``{'guid', 'title', 'class_name'}`` handle dicts in; arrays,
tables and handle dicts out) and every ValueError / RuntimeError text is
preserved verbatim (it crosses back through the error envelope).

Modeled on the same launcher / case style as the reference datapath suites:
synthetic in-memory fixtures built through ORS itself, every ORS object
tracked and ``deleteObject()``-ed at teardown, and every pinned value read
back through a DIFFERENT ORS path than the one under test. The lib is
imported through the plugin package (``from DragonflyNotebook import
dragonflynotebook_lib``) — the copy that ships, not a file-path twin.

Pinned here:

  * source resolution at the bridge boundary: a GUID string, an exact
    title and a published-object handle (its GUID wins over a stale title)
    all resolve to the same data; an unknown title, an empty string, a
    non-string, an empty handle and a wrong-class GUID raise clear
    ValueErrors (the actual class named); two objects sharing a title
    raise the rename-first error with BOTH GUIDs listed (an ORS instance
    cannot cross the pipe, so the instance form is gone by construction --
    the client refuses it, pinned in the tier-1 suite); a title carried
    only by ANOTHER class raises naming the carrying classes (round 12);
  * import_volume_as_np: (Z, Y, X) shape, the channel's dtype, values
    bitwise equal, and an INDEPENDENT copy (editing the returned array must
    never write through to the volume — ``Channel.getNDArray`` returns a
    live view over the channel's raw memory);
  * import_roi_as_np / import_multiroi_as_np: uint8 mask / label-count
    routed dtype (<=254 labels -> uint8, up to 300 labels -> uint16 here),
    values bitwise equal to the fixture;
  * publish_np_as_volume: voxel sizes and T from the array (a 2-D array
    publishes as Z=1), the channel's own dtype per the module policy
    (float64->float32, bool->uint8, signed-non-negative->smallest uint,
    signed-with-negatives->float32, uint16 as-is, uint64->uint32 / refused
    above uint32), values bitwise equal, explicit spacing pinned,
    1.0-isotropic default without one, and the reference path: an uint8
    array published against a float32 reference must keep the ARRAY's dtype
    and shape and inherit the reference's spacing (the Fiber Tracer
    dtype-trap rule: shape is only ever copied onto an EMPTY channel);
  * publish_np_as_roi: sizes and voxel content round-trip the mask bitwise,
    title set, an all-zero mask refused with no ORS object left behind;
  * publish_np_as_multiroi: the BounTI recipe end to end — label count
    pinned to the largest label (round-trip identity), content bitwise,
    dtype routed (3 labels -> uint8, 300 -> uint16), empty / fractional /
    negative labels refused;
  * a raise INSIDE publish() (the round-7 review closure, through the
    handler): it propagates out of the handler and the fresh
    Channel/ROI/MultiROI is deleted — plus the scratch channel on the
    ROI/MultiROI paths — pinned through duck-typed doubles in the
    Progress seam's __new__ spy pattern (the kept-result marker is
    assigned only after a successful publish, so the existing finally
    covers the publish-time raise with no new teardown);
  * the progress seam through the handler: a duck-typed progress is
    started exactly once with (caption, 3, True), gets updateProgress(1..3),
    one getIsCancelled() per step, is NEVER deleted by the lib, and its
    results are identical; a progress that reports cancelled on the third
    check raises RuntimeError('Operation cancelled by user.') after two
    updates and leaves NO channel instance behind; progress=None is silent
    (every other case drives the handlers headless through it);
  * round 8, the structural pin replacing round 7's run-suppression case:
    notebook-originated requests reach the lib ONLY through the handler
    layer, and every publish handler calls its public function with a DUCK
    progress (never ``True`` — no native ORS progress object is ever
    constructed on the notebook path) and ``pump=False`` (the event pump
    stands down — a bridge request never pumps Dragonfly's loop); the
    handle-to-GUID and label-key restoration are pinned on the same
    intercepted calls, plus the handler table's exact shape;
  * measured properties: list_measured_properties returns the slot
    descriptions in slot order; import_measured_properties returns a
    pandas.DataFrame with those columns and the fixture values (one row per
    label slot — row 0 is the background slot Dragonfly reserves, rows
    1..N are labels 1..N), honors a column subset in the given order, and
    refuses unknown columns listing the available ones;
  * measured-property WRITES (export_measured_properties +
    publish_np_as_multiroi(properties=...)): custom slot names round-trip
    through the read path (dict AND DataFrame, unicode/space names, a
    labelCount column auto-padded with the 0.0 background row, a
    {label: value} dict column ordered by label), the
    ranges helper initializes each slot's min/max to the written values
    over the LABEL rows (background row 0 is not an object), duplicate
    names dedupe CSV-style ' (2)', ' (3)' (within a batch,
    against existing slots, and for duplicate DataFrame columns), every
    refusal (bad length, empty name, 2-D values, non-DataFrame/dict,
    0-based/partial/non-int/colliding {label: value} key sets,
    wrong class, unresolvable source) happens PRE-FLIGHT — zero slots
    added, zero objects created — and a publish whose properties are
    rejected leaves NO MultiROI and no scratch channel behind;
  * delete_object (the 11th function, round 12): the title and GUID forms
    delete a published object (gone from list_objects; the next by-title
    lookup raises the generic no-object error), a cross-class ambiguous
    title raises the rename-first error with the class grouping and both
    GUIDs, and an unknown title raises the clean no-object ValueError;
  * list_objects: {'guid', 'title', 'class_name'} dicts sorted by title,
    the representable-only filter hiding a temporary object that
    representable_only=False still lists, unknown class names refused;
  * THE PIPE LOOPBACK (real QLocalServer + real DfnbClient in this one
    headless process): the exact framing, version handshake, chunked
    transfer, error and progress envelopes the two processes use — real
    arrays cross as npy bytes, a DataFrame crosses as column/index arrays,
    a published handle comes back and re-resolves, the dtype-trap rule
    holds across the pipe, an export with INT label keys lands after the
    JSON string-key crossing (the service restores them), ValueError
    semantics cross the error envelope unchanged, progress frames arrive
    on the client callback, and a cancel frame sent mid-handler aborts the
    handler at its checkpoint with the exact RuntimeError (the checkpoint
    pulls pending bytes with a timed-out waitForReadyRead — probed on the
    shipped Qt: no event loop, no queued-slot delivery).

FIRST, before any ORS work, the tier-1 core suite runs IN-PROCESS (import
test_dragonflynotebook_core, then run_all()): if it does not pass the script
prints SUITE FAILED and exits 1 immediately. The single gate command below
therefore covers gate 1 (tier-1 core) AND gate 2 (tier-2 bridge data path).

Usage (from the generated project root -- note the dfpy_ors launcher):

  uv run --no-project --python "C:\Program Files\Dragonfly\Python_env\python.exe" \
      verification/dfpy_ors.py verification/test_dragonflynotebook_datapath.py

NOT covered here (needs the live application; the tier-3 installed suite
and a manual in-app checklist verify them): the launcher's bridge startup
and app spawn, the notebook window itself, and the kernel's worker-thread
execution.
"""

import os

# Same multiple-OpenMP-runtime workaround as the core: MUST precede numpy.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import sys
import inspect
import threading
import time
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)  # the plugin package (<PluginClass>/ sits at the project root)
sys.path.insert(0, os.path.join(_ROOT, "verification"))  # test_dragonflynotebook_core (imported bare)

import test_dragonflynotebook_core as tier1  # noqa: E402  (the core suite -- runs FIRST)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import ORSModel  # noqa: E402
from ORSModel import Channel, ROI, MultiROI, Managed  # noqa: E402 (fixture builders)
from ORSModel import createChannelFromNumpyArray, orsObj  # noqa: E402
from OrsHelpers.datasethelper import DatasetHelper  # noqa: E402
from OrsHelpers.arrayhelper import ArrayHelper  # noqa: E402

from PyQt6.QtCore import QCoreApplication, QEventLoop  # noqa: E402
from PyQt6.QtNetwork import QLocalSocket  # noqa: E402

from DragonflyNotebook import dragonflynotebook_lib as dfnb  # noqa: E402 (the shipped copy)
from DragonflyNotebook import notebook_bridge_protocol as protocol  # noqa: E402
from DragonflyNotebook.bridge_service import NotebookBridgeService  # noqa: E402
from DragonflyNotebook.dfnb_client import DfnbClient  # noqa: E402

_CREATED = []            # ORS objects to deleteObject() at teardown
_CREATED_TITLES = set()  # titles of every tracked object, for the no-leak check


def _track(obj):
    """Records an ORS object for the reversed teardown and, when it carries
    one, its title for the no-leftover check."""
    _CREATED.append(obj)
    try:
        _CREATED_TITLES.add(obj.getTitle())
    except Exception:
        pass  # not every tracked object carries a title
    return obj


# ---- the bridge calls under test --------------------------------------------

def _import_via_bridge(method, source, timestep=0):
    """One import through its bridge handler: ``source`` is what the pipe
    can carry -- a GUID string, an exact title or a handle dict."""
    payload = dfnb.BRIDGE_HANDLERS[method](
        {'source': source, 'timestep': timestep}, None)
    return payload['array']


def _publish_via_bridge(method, params, progress=None):
    """One publish through its bridge handler: the returned handle dict is
    resolved back to the ORS object through a DIFFERENT path (``orsObj``)
    and tracked for teardown."""
    payload = dfnb.BRIDGE_HANDLERS[method](params, progress)
    published = payload['published']
    obj = orsObj(published['guid'])
    assert obj is not None, \
        f"the published handle {published!r} must resolve to a live object"
    assert published['class_name'] == type(obj).__name__, \
        f"the handle must carry the class name, got {published!r}"
    return _track(obj)


# ---- fixtures: ORS builders independent of the lib under test --------------

def _channel_from(arr, title="probe_channel"):
    ch = createChannelFromNumpyArray(arr)
    ch.setTitle(title)
    return _track(ch)


def _multiroi_from(template_channel, label_array, title="probe_labels",
                   label_count=None):
    """A label MultiROI holding ``label_array`` with the geometry of the
    template: the BounTI publish-path pattern, built WITHOUT the lib under
    test so the import cases never depend on the publish cases. The
    temporary channel of the pattern is deleted the same way; when
    ``label_count`` is given the label count is pinned explicitly (as a
    real Dragonfly segmentation of that label count would carry)."""
    ch = Channel()
    ch.copyShapeFromStructuredGrid(template_channel)
    ORSModel.createChannelFromNumpyArray(label_array, channelGUID=ch.getGUID(), ZAxis=True)
    m = DatasetHelper.createMultiROIFromDataset(ch, None)
    ch.deleteObject()
    if m is None:
        raise AssertionError("createMultiROIFromDataset returned None for " + title)
    if label_count is not None:
        m.setLabelCount(label_count)
    m.setTitle(title)
    return _track(m)


def _roi_from(mask, title="probe_roi"):
    """A ROI from a 0/1 mask through StructuredGrid.getAsROI, with the mask
    channel built and deleted inside the builder."""
    ch = createChannelFromNumpyArray(np.ascontiguousarray(mask, dtype=np.uint8))
    try:
        roi = ch.getAsROI(None, None)
    finally:
        ch.deleteObject()
    if roi is None:
        raise AssertionError("getAsROI returned None for " + title)
    roi.setTitle(title)
    return _track(roi)


def _multiroi_with_properties(label_array, descriptions, columns, title):
    """A MultiROI fixture carrying measured properties: one scalar slot per
    description, filled through ArrayHelper.ConvertNumpyToOrsArray — the
    same path the ORS framework's own (broken) pandas setter uses, but on
    the working READ path only."""
    template = _channel_from(np.zeros(label_array.shape, np.uint16),
                             title + "_template")
    m = _multiroi_from(template, label_array, title, label_count=int(label_array.max()))
    template.deleteObject()
    m.setScalarValuesSlotCount(len(descriptions))
    for slot, description in enumerate(descriptions):
        m.setScalarValueDescription(slot, description, 0)
        ors_array = ArrayHelper.ConvertNumpyToOrsArray(
            np.asarray(columns[slot], dtype=np.float64))
        m.setScalarValues(ors_array, slot, 0)
        ors_array.deleteObject()
    return m


def _readback_volume(channel, timestep=0):
    """The volume through the ORS native read path, as an independent
    (Z, Y, X) copy."""
    return np.array(channel.getNDArray(timestep), copy=True)


def _channel_count():
    return len(list(Channel.getAllInstances()))


# ---- the duck-typed ORS Progress (the caller-owned progress seam) ----------

class _RecordingProgress(object):
    """Duck-typed ORS Progress: records startProgressWithCaption /
    updateProgress / getIsCancelled / deleteObject calls. When
    ``cancel_after`` is set, the (cancel_after+1)-th getIsCancelled() call
    reports cancelled."""

    def __init__(self, cancel_after=None):
        self.starts = []
        self.updates = []
        self.cancel_calls = 0
        self.deleted = False
        self._cancel_after = cancel_after

    def startProgressWithCaption(self, caption, iRange, bCancellable):
        self.starts.append((caption, iRange, bCancellable))

    def updateProgress(self, iPosition):
        self.updates.append(iPosition)

    def getIsCancelled(self):
        self.cancel_calls += 1
        if self._cancel_after is None:
            return False
        return self.cancel_calls > self._cancel_after

    def deleteObject(self):
        self.deleted = True


# ---------------------------------------------------------------------------
# cases (one behavior each)
# ---------------------------------------------------------------------------

def case_bridge_source_resolution_pins_guid_title_and_handle_forms():
    """The pipe can only carry strings and handle dicts: a source resolves
    by GUID string, by exact title and by a published-object handle (its
    GUID wins over any title it carries). Everything else raises a clear
    ValueError: an unknown title (pointing at dfnb.list_objects), an empty
    string, a non-string, an empty handle and a wrong-class GUID (the
    actual class named). An ambiguous title lists BOTH GUIDs."""
    vol = _channel_from(_VOL_F32, "tp_resolve_volume")
    guid = vol.getGUID()

    through_guid = _import_via_bridge('import_volume_as_np', guid)
    through_title = _import_via_bridge('import_volume_as_np', "tp_resolve_volume")
    through_handle = _import_via_bridge(
        'import_volume_as_np',
        {'guid': guid, 'title': 'a stale title', 'class_name': 'Channel'})
    for got in (through_guid, through_title, through_handle):
        assert np.array_equal(got, _VOL_F32), \
            f"every resolution form must import the same data, got {got!r}"

    with _expect_value_error("No Channel with the title or GUID"):
        _import_via_bridge('import_volume_as_np', "tp_no_such_volume")
    with _expect_value_error("got an empty string"):
        _import_via_bridge('import_volume_as_np', "   ")
    with _expect_value_error("must be an object title, a GUID or a "
                             "published-object handle"):
        _import_via_bridge('import_volume_as_np', 42)
    with _expect_value_error("The bridge carried an empty"):
        _import_via_bridge('import_volume_as_np', {'guid': '', 'title': '  '})
    with _expect_value_error("expected a ROI, got Channel"):
        _import_via_bridge('import_roi_as_np', guid)
    with _expect_value_error("expected a Channel, got MultiROI"):
        _import_via_bridge('import_volume_as_np',
                           _resolve_multiroi_fixture().getGUID())

    # an ambiguous title must lead with the rename ask and list the GUIDs
    # of every match (the round-12 reworded duplicate error)
    dup_a = _channel_from(_VOL_F32, "tp_dup_title")
    dup_b = _channel_from(_VOL_F32, "tp_dup_title")
    with _expect_value_error("Multiple objects named") as ctx:
        _import_via_bridge('import_volume_as_np', "tp_dup_title")
    message = str(ctx.exception)
    assert "Please rename all but one of them" in message \
        and "pass the GUID of the one you meant" in message, \
        f"the ambiguity error must carry the rename ask, got {message!r}"
    assert dup_a.getGUID() in message and dup_b.getGUID() in message, \
        f"the ambiguity error must list both GUIDs, got {message!r}"
    print("  GUID/title/handle resolve to the same data (the handle's GUID "
          "wins); unknown, empty, non-string, empty-handle, wrong-class and "
          "ambiguous sources raise clear ValueErrors (rename-first message "
          "listing both GUIDs on ambiguity)")


def case_cross_class_title_raises_the_carrying_classes():
    """The round-12 blind spot: a title that exists ONLY on another class
    no longer raises the misleading generic no-object error — the resolver
    names the classes that carry it ('No representable Channel is named ...
    that name is carried by 1 ROI'; the qualifier keeps the claim true even
    when a NON-representable same-class twin carries the title). The
    cross-class error must NOT fire when the expected class DOES carry the
    title (a same-class match wins even when another class shares the
    name), and a title nothing carries keeps the generic error (pinned in
    the resolution case above)."""
    carrier = _roi_from(_MASK, "tp_crossclass_name")
    carrier.publish()  # the published-ROI path the lib's own publish uses
    assert carrier.getIsRepresentable() is True, \
        "the carrier fixture must be representable (fixture sanity)"

    with _expect_value_error("No representable Channel is named") as ctx:
        _import_via_bridge('import_volume_as_np', "tp_crossclass_name")
    message = str(ctx.exception)
    assert "that name is carried by 1 ROI" in message \
        and "Please rename the other object(s)" in message \
        and "use the Channel's own title or GUID" in message, \
        f"the cross-class error must name the carrier and the ask, got {message!r}"

    # a Channel WITH that title resolves normally: the cross-class error
    # never fires when the expected class carries the name too
    same_named = _channel_from(_VOL_F32, "tp_crossclass_name")
    got = _import_via_bridge('import_volume_as_np', "tp_crossclass_name")
    assert np.array_equal(got, _VOL_F32), \
        "the same-class match must win over a same-titled other-class object"
    print("  a Channel-only-missing title raises the cross-class error "
          "naming the carrying ROI; with a same-titled Channel present the "
          "ordinary match wins")


def case_delete_object_removes_by_title_and_guid_and_refuses_unknown():
    """delete_object, the 11th dfnb function: a published object is gone
    from list_objects after delete_object(title) (and the next by-title
    lookup raises the generic no-object error), the GUID form deletes too,
    a cross-class ambiguous title raises the rename-first error with the
    count-accurate class grouping and BOTH GUIDs, and an unknown title
    raises the clean no-object ValueError."""
    doomed = _publish_via_bridge('publish_np_as_volume', {
        'array': _VOL_F32, 'title': 'tp_delete_me',
        'spacing': [1.0, 1.0, 1.0]})
    dfnb.BRIDGE_HANDLERS['delete_object']({'source': "tp_delete_me"}, None)
    # already deleted: the teardown must not see it again
    _CREATED.remove(doomed)
    assert all(entry['title'] != 'tp_delete_me'
               for entry in dfnb.BRIDGE_HANDLERS['list_objects'](
                   {'class_name': 'Channel'}, None)['objects']), \
        "the deleted object must be gone from list_objects"
    with _expect_value_error("No Channel with the title or GUID"):
        _import_via_bridge('import_volume_as_np', "tp_delete_me")

    doomed_too = _publish_via_bridge('publish_np_as_volume', {
        'array': _VOL_F32, 'title': 'tp_delete_me_too',
        'spacing': [1.0, 1.0, 1.0]})
    dfnb.BRIDGE_HANDLERS['delete_object'](
        {'source': doomed_too.getGUID()}, None)
    _CREATED.remove(doomed_too)
    assert all(entry['title'] != 'tp_delete_me_too'
               for entry in dfnb.BRIDGE_HANDLERS['list_objects'](
                   {'class_name': 'Channel'}, None)['objects']), \
        "the GUID form must delete too"

    # cross-class ambiguity: the rename-first error with the class grouping
    dup_channel = _channel_from(_VOL_F32, "tp_delete_dup")
    dup_roi = _roi_from(_MASK, "tp_delete_dup")
    dup_roi.publish()
    with _expect_value_error("Multiple objects named") as ctx:
        dfnb.BRIDGE_HANDLERS['delete_object']({'source': "tp_delete_dup"},
                                              None)
    message = str(ctx.exception)
    assert "(1 Channel, 1 ROI)" in message \
        and "Please rename all but one of them" in message, \
        f"the cross-class ambiguity error must group by class, got {message!r}"
    assert dup_channel.getGUID() in message and dup_roi.getGUID() in message, \
        f"the cross-class ambiguity error must list both GUIDs, got {message!r}"

    with _expect_value_error("No object with the title or GUID "
                             "'tp_never_published' exists in the open "
                             "Dragonfly session"):
        dfnb.BRIDGE_HANDLERS['delete_object'](
            {'source': 'tp_never_published'}, None)
    print("  title and GUID forms delete (gone from list_objects, the next "
          "lookup raises the generic error); cross-class ambiguity groups "
          "'1 Channel, 1 ROI' with both GUIDs; an unknown title raises the "
          "clean no-object error")


def case_import_volume_as_np_returns_independent_z_y_x_copy():
    """import_volume_as_np through the bridge handler: (Z, Y, X) shape, the
    channel's own dtype, values bitwise equal — and an independent
    numpy.ndarray: editing the returned array must not change the volume on
    re-import (Channel.getNDArray is a live view over the raw memory; the
    lib must copy). The timestep refusals fire in the lib exactly as
    before."""
    vol = _channel_from(_VOL_F32, "tp_import_volume")
    got = _import_via_bridge('import_volume_as_np', vol.getGUID())
    assert type(got) is np.ndarray, \
        f"the import must return a plain numpy.ndarray, got {type(got)}"
    assert got.shape == (4, 5, 6), f"expected (Z, Y, X) = (4, 5, 6), got {got.shape}"
    assert got.dtype == np.float32, f"expected the channel dtype float32, got {got.dtype}"
    assert np.array_equal(got, _VOL_F32), \
        f"the imported values must be bitwise the fixture's\n{got!r}\nvs\n{_VOL_F32!r}"

    got[0, 0, 0] = 999.0  # a write through the returned array...
    again = _import_via_bridge('import_volume_as_np', "tp_import_volume")
    assert again[0, 0, 0] == _VOL_F32[0, 0, 0], \
        "must never write through: the volume must be unchanged on re-import"
    assert np.array_equal(_readback_volume(vol), _VOL_F32), \
        "the channel itself must be untouched after a cell edits the import"

    with _expect_value_error("timestep 1 is out of range"):
        _import_via_bridge('import_volume_as_np', vol.getGUID(), 1)
    with _expect_value_error("timestep must be a non-negative integer"):
        _import_via_bridge('import_volume_as_np', vol.getGUID(), -1)
    with _expect_value_error("timestep must be a non-negative integer"):
        _import_via_bridge('import_volume_as_np', vol.getGUID(), True)
    with _expect_value_error("timestep must be a non-negative integer"):
        _import_via_bridge('import_volume_as_np', vol.getGUID(), 1.5)
    print("  (Z, Y, X) float32 copy, bitwise equal; edits never write "
          "through; out-of-range / negative / bool / float timesteps refused")


def case_import_roi_as_np_pins_binary_uint8_readout():
    """import_roi_as_np through the bridge handler: the mask round-trips
    bitwise as uint8 (Z, Y, X)."""
    roi = _roi_from(_MASK, "tp_import_roi")
    got = _import_via_bridge('import_roi_as_np', roi.getGUID())
    assert got.dtype == np.uint8, f"ROI imports are uint8 masks, got {got.dtype}"
    assert got.shape == (4, 5, 6), f"expected (4, 5, 6), got {got.shape}"
    assert np.array_equal(got, _MASK), \
        f"the mask must round-trip bitwise\n{got!r}\nvs\n{_MASK!r}"
    assert set(np.unique(got)) <= {0, 1}, "a ROI import is a 0/1 mask"
    with _expect_value_error("timestep 1 is out of range"):
        _import_via_bridge('import_roi_as_np', roi.getGUID(), 1)
    print("  ROI imports as a bitwise-equal uint8 0/1 (Z, Y, X) mask; "
          "out-of-range timestep refused")


def case_import_multiroi_as_np_pins_label_dtype_routing():
    """import_multiroi_as_np through the bridge handler: dtype routed by
    label count exactly like Dragonfly's own MultiROI.getAsNDArray — <=254
    labels -> uint8, and a 300-label MultiROI reads back as uint16 — with
    bitwise-equal values."""
    template = _channel_from(np.zeros(_LABELS.shape, np.uint16),
                             "tp_import_mroi_template")
    small = _multiroi_from(template, _LABELS, "tp_import_mroi_small",
                           label_count=9)
    got = _import_via_bridge('import_multiroi_as_np', small.getGUID())
    assert got.dtype == np.uint8, f"9 labels must read back uint8, got {got.dtype}"
    assert np.array_equal(got, _LABELS.astype(np.uint8)), \
        "the labels must round-trip bitwise"

    big_labels = np.zeros((2, 3, 4), np.uint16)
    big_labels[0, 1, 1] = 1
    big_labels[1, 2, 3] = 300
    big = _multiroi_from(template, big_labels, "tp_import_mroi_big",
                         label_count=300)
    got_big = _import_via_bridge('import_multiroi_as_np', big.getGUID())
    assert got_big.dtype == np.uint16, \
        f"300 labels must read back uint16, got {got_big.dtype}"
    assert np.array_equal(got_big, big_labels), "the big labels must round-trip"
    template.deleteObject()
    with _expect_value_error("timestep 1 is out of range"):
        _import_via_bridge('import_multiroi_as_np', small.getGUID(), 1)
    print("  label dtype routing pinned: 9 labels -> uint8, 300 labels -> "
          "uint16, values bitwise; out-of-range timestep refused")


def case_publish_np_as_volume_without_reference_pins_shape_dtype_spacing():
    """publish_np_as_volume through the bridge handler, headless
    (progress=None): the voxel sizes and T come from the array (a 2-D array
    publishes as Z=1), the values round-trip bitwise, an explicit spacing
    is pinned, and without one the spacing is 1.0 isotropic. Default title
    'Result volume'. The handler answers with the handle dict; the created
    channel is read back through ``orsObj`` — a different path."""
    arr = np.arange(60, dtype=np.float32).reshape(3, 4, 5) + 0.5
    vol = _publish_via_bridge('publish_np_as_volume', {
        'array': arr, 'title': 'tp_pub_vol', 'reference': None,
        'spacing': [2.0, 3.0, 4.0]})
    assert isinstance(vol, Channel), f"expected a Channel, got {type(vol)}"
    assert (vol.getZSize(), vol.getYSize(), vol.getXSize()) == (3, 4, 5), \
        "the voxel sizes must come from the array (Z, Y, X)"
    assert vol.getTSize() == 1, "a 3-D array publishes a single timestep"
    assert vol.dtype == np.float32, f"float32 stays float32, got {vol.dtype}"
    assert (vol.getXSpacing(), vol.getYSpacing(), vol.getZSpacing()) == \
        (2.0, 3.0, 4.0), "the explicit spacing must be pinned"
    assert vol.getTitle() == "tp_pub_vol", "the title must be set"
    assert np.array_equal(_readback_volume(vol), arr), \
        "the voxel values must round-trip bitwise"
    assert vol.getIsRepresentable() is True, \
        "publish() must set the volume representable"

    default_titled = _publish_via_bridge('publish_np_as_volume',
                                         {'array': arr})
    assert default_titled.getTitle() == "Result volume", \
        f"the default title must be 'Result volume', got {default_titled.getTitle()!r}"

    flat = np.arange(6, dtype=np.float32).reshape(2, 3)
    planar = _publish_via_bridge('publish_np_as_volume',
                                 {'array': flat, 'title': 'tp_pub_flat'})
    assert (planar.getZSize(), planar.getYSize(), planar.getXSize()) == (1, 2, 3), \
        "a 2-D array publishes as a single-slice volume (Z=1)"
    assert np.array_equal(_readback_volume(planar)[0], flat), \
        "the 2-D array must round-trip into the single slice"
    assert (planar.getXSpacing(), planar.getYSpacing(), planar.getZSpacing()) == \
        (1.0, 1.0, 1.0), "without a spacing the default is 1.0 isotropic"
    print("  shape/T from the array (2-D -> Z=1), bitwise values, explicit "
          "and 1.0-isotropic spacing pinned, default title 'Result volume'")


def case_publish_np_as_volume_uint8_with_reference_pins_dtype_trap_rule():
    """The Fiber Tracer trap, pinned from the publish side through the
    handler (the reference crosses as a handle dict): an uint8 array
    published against a float32 reference keeps the ARRAY's dtype and shape
    and inherits the reference's spacing — copyShapeFromStructuredGrid only
    ever runs on the EMPTY output channel, before the fill."""
    reference = _channel_from(np.zeros((2, 3, 4), np.float32),
                              "tp_pub_ref_volume")
    reference.setXSpacing(1.5)
    reference.setYSpacing(2.0)
    reference.setZSpacing(2.5)

    arr = (np.arange(60, dtype=np.uint8).reshape(3, 4, 5) % 7) * 30
    vol = _publish_via_bridge('publish_np_as_volume', {
        'array': arr, 'title': 'tp_pub_trapped',
        'reference': {'guid': reference.getGUID(),
                      'title': 'tp_pub_ref_volume',
                      'class_name': 'Channel'}})
    assert vol.dtype == np.uint8, \
        f"the output must keep the ARRAY's dtype (uint8), got {vol.dtype}"
    assert (vol.getZSize(), vol.getYSize(), vol.getXSize()) == (3, 4, 5), \
        "the output shape must come from the array, not the reference"
    assert (vol.getXSpacing(), vol.getYSpacing(), vol.getZSpacing()) == \
        (1.5, 2.0, 2.5), "the output must inherit the reference's spacing"
    assert np.array_equal(_readback_volume(vol), arr), \
        "the uint8 values must round-trip bitwise (no re-type corruption)"
    assert np.array_equal(_readback_volume(reference),
                          np.zeros((2, 3, 4), np.float32)), \
        "the reference volume must be untouched"

    # an explicit spacing wins over the reference's
    overridden = _publish_via_bridge('publish_np_as_volume', {
        'array': arr, 'title': 'tp_pub_override',
        'reference': reference.getGUID(),
        'spacing': [9.0, 9.0, 9.0]})
    assert (overridden.getXSpacing(), overridden.getYSpacing(),
            overridden.getZSpacing()) == (9.0, 9.0, 9.0), \
        "an explicit spacing must win over the reference's"
    with _expect_value_error("expected a reference volume, got MultiROI"):
        _publish_via_bridge('publish_np_as_volume', {
            'array': arr,
            'reference': _resolve_multiroi_fixture().getGUID()})
    with _expect_value_error("spacing must be None or a sequence of three "
                             "positive numbers"):
        _publish_via_bridge('publish_np_as_volume', {
            'array': arr, 'spacing': [1.0, 0.0, 1.0]})
    with _expect_value_error("spacing must be None or a sequence of three "
                             "positive numbers"):
        _publish_via_bridge('publish_np_as_volume', {
            'array': arr, 'spacing': [1.0, 1.0]})
    print("  uint8-on-float32-reference keeps the array's dtype/shape and "
          "the reference's spacing; values bitwise; reference untouched; "
          "spacing override wins; bad reference/spacing refused")


def case_publish_np_as_volume_dtype_policy_matrix():
    """The dtype policy, one handler publish per row, each value-checked
    through the ORS read path: float64->float32 (exact-representable
    values), bool->uint8, signed non-negative -> smallest uint, signed with
    negatives -> float32 (values preserved — createChannelFromNumpyArray
    would otherwise shift them by the minimum), uint16 as-is, uint64 ->
    uint32 when it fits and ValueError when it does not; complex / 1-D /
    4-D / empty refused."""
    checks = (
        ("float64 goes to float32",
         np.array([[0.5, 1.25], [2.75, -3.5]], np.float64), np.float32,
         np.array([[0.5, 1.25], [2.75, -3.5]], np.float32), None),
        ("bool goes to uint8",
         np.array([[True, False], [False, True]]), np.uint8,
         np.array([[1, 0], [0, 1]], np.uint8), None),
        ("signed non-negative goes to the smallest uint",
         np.array([[0, 3], [200, 255]], np.int32), np.uint8,
         np.array([[0, 3], [200, 255]], np.uint8), None),
        ("signed with negatives goes to float32, values preserved",
         np.array([[0, -3], [2, -1]], np.int16), np.float32,
         np.array([[0.0, -3.0], [2.0, -1.0]], np.float32), None),
        ("uint16 stays uint16",
         np.array([[0, 300], [1000, 65535]], np.uint16), np.uint16,
         np.array([[0, 300], [1000, 65535]], np.uint16), None),
        ("uint64 goes to uint32 when it fits",
         np.array([1, 4000000000], np.uint64).reshape(1, 2), np.uint32,
         np.array([1, 4000000000], np.uint32).reshape(1, 2), None),
        ("uint64 above uint32 is refused",
         np.array([1, 5000000000], np.uint64).reshape(1, 2), None, None,
         "exceed the largest ORS channel type"),
        ("complex is refused", np.zeros((2, 2), np.complex64), None, None,
         "unsupported dtype"),
        ("1-D is refused", np.zeros(5, np.float32), None, None,
         "2-D (Y, X) or 3-D (Z, Y, X)"),
        ("4-D is refused", np.zeros((2, 2, 2, 2), np.float32), None, None,
         "2-D (Y, X) or 3-D (Z, Y, X)"),
        ("empty is refused", np.zeros((0, 3), np.float32), None, None,
         "is empty"),
    )
    index = 0
    for description, arr, expected_dtype, expected_values, error_fragment in checks:
        index += 1
        title = f"tp_pub_dtype_{index}"
        if error_fragment is not None:
            with _expect_value_error(error_fragment):
                _publish_via_bridge('publish_np_as_volume',
                                    {'array': arr, 'title': title})
            continue
        vol = _publish_via_bridge('publish_np_as_volume',
                                  {'array': arr, 'title': title})
        assert vol.dtype == expected_dtype, \
            f"{description}: expected {np.dtype(expected_dtype)}, got {vol.dtype}"
        assert np.array_equal(_readback_volume(vol)[0], expected_values), \
            f"{description}: values must round-trip bitwise"
    print(f"  dtype policy pinned on {index} rows (value-checked, refused "
          f"inputs raise with the policy in the message)")


def case_publish_np_as_roi_pins_voxel_mask_roundtrip():
    """publish_np_as_roi through the handler: sizes and voxel content
    round-trip the mask bitwise through a DIFFERENT read path
    (ROI.getAsNDArray), the title is set, the scratch channel leaves
    nothing behind, and an all-zero mask is refused with no ORS object
    created."""
    roi = _publish_via_bridge('publish_np_as_roi', {
        'array': _MASK, 'title': 'tp_pub_roi',
        'spacing': [2.0, 2.0, 2.0]})
    assert isinstance(roi, ROI), f"expected a ROI, got {type(roi)}"
    assert (roi.getZSize(), roi.getYSize(), roi.getXSize()) == (4, 5, 6), \
        "the ROI geometry must come from the mask"
    assert (roi.getXSpacing(), roi.getYSpacing(), roi.getZSpacing()) == \
        (2.0, 2.0, 2.0), "the ROI must carry the explicit spacing"
    assert roi.getTitle() == "tp_pub_roi", "the title must be set"
    got = roi.getAsNDArray(0)
    assert got.dtype == np.uint8 and np.array_equal(got, _MASK), \
        f"the ROI voxels must round-trip the mask bitwise\n{got!r}\nvs\n{_MASK!r}"
    assert roi.getIsRepresentable() is True, "publish() must set the ROI representable"

    before = _channel_count()
    with _expect_value_error("no foreground voxels"):
        _publish_via_bridge('publish_np_as_roi', {
            'array': np.zeros((2, 3, 4), np.uint8),
            'title': 'tp_pub_roi_empty'})
    assert _channel_count() == before, \
        "a refused mask must leave no scratch channel behind"
    with _expect_value_error("expected a reference volume, got MultiROI"):
        _publish_via_bridge('publish_np_as_roi', {
            'array': _MASK,
            'reference': _resolve_multiroi_fixture().getGUID()})
    # a rejected title raises AFTER the ROI is created: the freshly created
    # ROI must be deleted again -- nothing created on the failure path
    before_roi = len(list(ROI.getAllInstances()))
    with _expect_value_error("title must be a non-empty string"):
        _publish_via_bridge('publish_np_as_roi', {
            'array': _MASK, 'title': '   '})
    assert len(list(ROI.getAllInstances())) == before_roi, \
        "a rejected title must leave no freshly created ROI behind"
    print("  ROI voxel mask round-trips bitwise with pinned spacing; "
          "all-zero mask refused with nothing created; bad reference refused; "
          "bad title refused with the created ROI deleted")


def case_publish_np_as_multiroi_pins_labels_and_label_count():
    """publish_np_as_multiroi through the handler, the BounTI recipe end to
    end: label count pinned to the largest label (so a re-import keeps
    label identity and dtype), content bitwise through MultiROI.getAsNDArray,
    dtype routed (3 labels -> uint8, 300 -> uint16); empty / fractional /
    negative labels refused."""
    labels = np.zeros((2, 3, 4), np.uint8)
    labels[0, 0, 0] = 3
    labels[0, 2, 1] = 1
    labels[1, 1, 3] = 2
    mroi = _publish_via_bridge('publish_np_as_multiroi', {
        'array': labels, 'title': 'tp_pub_mroi'})
    assert isinstance(mroi, MultiROI), f"expected a MultiROI, got {type(mroi)}"
    assert mroi.getLabelCount() == 3, \
        f"the label count must be pinned to the largest label (3), got {mroi.getLabelCount()}"
    assert mroi.getTitle() == "tp_pub_mroi", "the title must be set"
    got = mroi.getAsNDArray(0)
    assert got.dtype == np.uint8 and np.array_equal(got, labels), \
        f"the labels must round-trip bitwise\n{got!r}\nvs\n{labels!r}"
    assert mroi.getIsRepresentable() is True, \
        "publish() must set the MultiROI representable"

    big = np.zeros((1, 2, 3), np.uint16)
    big[0, 0, 0] = 300
    big[0, 1, 2] = 7
    big_mroi = _publish_via_bridge('publish_np_as_multiroi', {
        'array': big, 'title': 'tp_pub_mroi_big'})
    assert big_mroi.getLabelCount() == 300, \
        f"300 labels expected, got {big_mroi.getLabelCount()}"
    got_big = big_mroi.getAsNDArray(0)
    assert got_big.dtype == np.uint16 and np.array_equal(got_big, big), \
        "the 300-label MultiROI must round-trip bitwise as uint16"

    before = _channel_count()
    with _expect_value_error("contains no labels"):
        _publish_via_bridge('publish_np_as_multiroi', {
            'array': np.zeros((2, 3, 4), np.uint8),
            'title': 'tp_pub_mroi_empty'})
    with _expect_value_error("non-integer labels"):
        _publish_via_bridge('publish_np_as_multiroi', {
            'array': np.array([[1.0, 2.5]], np.float32),
            'title': 'tp_pub_mroi_frac'})
    with _expect_value_error("negative"):
        _publish_via_bridge('publish_np_as_multiroi', {
            'array': np.array([[-1, 2]], np.int32),
            'title': 'tp_pub_mroi_neg'})
    assert _channel_count() == before, \
        "refused labels must leave no scratch channel behind"
    # a rejected title raises AFTER the MultiROI is created: the freshly
    # created MultiROI must be deleted again -- nothing created on failure
    before_mroi = len(list(MultiROI.getAllInstances()))
    with _expect_value_error("title must be a non-empty string"):
        _publish_via_bridge('publish_np_as_multiroi', {
            'array': labels, 'title': '   '})
    assert len(list(MultiROI.getAllInstances())) == before_mroi, \
        "a rejected title must leave no freshly created MultiROI behind"
    print("  label count pinned to the largest label, content bitwise, "
          "dtype routed (3 -> uint8, 300 -> uint16); empty/fractional/"
          "negative labels refused with nothing created; bad title refused "
          "with the created MultiROI deleted")


def case_raise_inside_publish_leaves_nothing_behind():
    """The publish() call runs INSIDE the try of every publish function
    (the round-7 review closure), so it also holds one handler layer up:
    a failure raised INSIDE publish must propagate OUT of the handler AND
    delete the fresh Channel/ROI/MultiROI — and, on the ROI/MultiROI paths,
    the scratch channel too — because the kept-result marker
    (``channel``/``result``) is assigned only once the publish succeeded,
    so the existing ``finally`` conditions cover a publish-time raise with
    no new teardown. A real ORS object cannot be made to raise inside its
    own publish(), so the fresh objects are duck-typed doubles installed
    through the same ``__new__`` spy pattern as the Progress seam above
    (the product is deliberately NOT an instance of the spy class); the
    doubles record their ``deleteObject``."""
    error = RuntimeError('publish failed on purpose (the leak pin)')
    arr = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    created = []

    class _PublishRaisesDouble(object):
        """Duck-typed fresh Channel/ROI/MultiROI: exactly the touchpoints
        the three publish paths use, ``publish()`` raising the planted
        error, ``deleteObject`` recording."""

        def __init__(self, kind):
            self.kind = kind
            self.title = None
            self.deleted = False
            self.publish_calls = 0

        def getGUID(self):
            return 'tp_publish_raises_' + self.kind

        # _apply_spacing's 1.0-isotropic default (no spacing, no reference)
        def setXSpacing(self, _value):
            pass

        def setYSpacing(self, _value):
            pass

        def setZSpacing(self, _value):
            pass

        def setTitle(self, title):
            self.title = title

        def setLabelCount(self, label_count):
            self.label_count = label_count

        def getAsROI(self, _timestep, _progress):
            roi = _PublishRaisesDouble('ROI')
            created.append(roi)
            return roi

        def publish(self):
            self.publish_calls += 1
            raise error

        def deleteObject(self):
            self.deleted = True

    class _ChannelConstructionSpy:
        """Installed as ``dfnb.Channel``: every construction hands back a
        duck-typed _PublishRaisesDouble -- NOT an instance of the spy
        class, the same pattern as the Progress construction spy."""

        def __new__(cls):
            double = _PublishRaisesDouble('Channel')
            created.append(double)
            return double

    class _DatasetHelperSpy:
        @staticmethod
        def createMultiROIFromDataset(_channel, _timestep, _progress):
            double = _PublishRaisesDouble('MultiROI')
            created.append(double)
            return double

    def _fake_fill(_array, channelGUID=None, ZAxis=False):
        """Stands in for ``createChannelFromNumpyArray`` while the fresh
        channel is a double: the real fill would need a live ORS GUID."""
        return None

    real_channel = dfnb.Channel
    real_fill = dfnb.createChannelFromNumpyArray
    real_helper = dfnb.DatasetHelper
    dfnb.Channel = _ChannelConstructionSpy
    dfnb.createChannelFromNumpyArray = _fake_fill
    dfnb.DatasetHelper = _DatasetHelperSpy
    # nothing REAL can be created while the seams are doubled: snapshot the
    # instance counts (the family's no-leftover mechanism) around all three
    # paths
    channels_before = _channel_count()
    rois_before = len(list(ROI.getAllInstances()))
    mrois_before = len(list(MultiROI.getAllInstances()))
    try:
        # the volume path: the fresh channel is built, filled and titled,
        # then publish() raises -- the finally deletes it while ``channel``
        # is still None; the exception crosses OUT of the handler (the
        # service would wrap it into the error envelope)
        with _expect_runtime_error('publish failed on purpose'):
            dfnb.BRIDGE_HANDLERS['publish_np_as_volume'](
                {'array': arr, 'title': 'tp_leak_volume'}, None)
        volume = created.pop(0)
        assert volume.publish_calls == 1 and volume.title == 'tp_leak_volume', \
            "the raise must have happened at the publish step (after the title)"
        assert volume.deleted is True, \
            "a publish-time raise must delete the fresh channel"

        # the ROI path: publish raises INSIDE the ``if roi is not None:``
        # block -- BOTH the fresh ROI and the scratch channel are deleted
        created.clear()
        with _expect_runtime_error('publish failed on purpose'):
            dfnb.BRIDGE_HANDLERS['publish_np_as_roi'](
                {'array': _MASK, 'title': 'tp_leak_roi'}, None)
        scratch, fresh_roi = created
        assert fresh_roi.publish_calls == 1 \
            and fresh_roi.title == 'tp_leak_roi', \
            "the raise must have happened at the publish step (after the title)"
        assert fresh_roi.deleted is True, \
            "a publish-time raise must delete the fresh ROI"
        assert scratch.deleted is True, \
            "a publish-time raise must delete the scratch channel too"

        # the MultiROI path: same contract -- the fresh MultiROI and the
        # scratch channel are both deleted when publish() raises
        created.clear()
        with _expect_runtime_error('publish failed on purpose'):
            dfnb.BRIDGE_HANDLERS['publish_np_as_multiroi'](
                {'array': _PROPS_LABELS, 'title': 'tp_leak_mroi'}, None)
        scratch, fresh_mroi = created
        assert fresh_mroi.publish_calls == 1 \
            and fresh_mroi.title == 'tp_leak_mroi', \
            "the raise must have happened at the publish step (after the title)"
        assert fresh_mroi.deleted is True, \
            "a publish-time raise must delete the fresh MultiROI"
        assert scratch.deleted is True, \
            "a publish-time raise must delete the scratch channel too"
    finally:
        dfnb.Channel = real_channel
        dfnb.createChannelFromNumpyArray = real_fill
        dfnb.DatasetHelper = real_helper

    # nothing REAL was created on any of the three paths (the doubles are
    # not ORS objects): the instance counts must be untouched
    assert _channel_count() == channels_before \
        and len(list(ROI.getAllInstances())) == rois_before \
        and len(list(MultiROI.getAllInstances())) == mrois_before, \
        "a publish-time raise must create no real Channel/ROI/MultiROI"
    print("  a raise INSIDE publish propagates out of the handler and "
          "deletes the fresh Channel/ROI/MultiROI (and the scratch channel "
          "on the ROI/MultiROI paths) -- the kept-result marker is assigned "
          "only after a successful publish")


def case_progress_seam_pins_start_update_cancel_and_ownership():
    """The duck-typed progress through the handler: started exactly once
    with (caption, 3, True), updateProgress(1..3) on success, exactly one
    getIsCancelled() per step, never deleted by the lib, and the published
    result is identical to the progress=None path. A progress reporting
    cancelled on the third check raises RuntimeError('Operation cancelled
    by user.') after two updates and leaves NO channel instance behind; a
    progress cancelling on the very first check raises before anything is
    created."""
    arr = np.arange(24, dtype=np.float32).reshape(2, 3, 4)

    happy = _RecordingProgress()
    vol = _publish_via_bridge('publish_np_as_volume', {
        'array': arr, 'title': 'tp_prog_happy'}, progress=happy)
    assert happy.starts == [("Publishing numpy array as volume", 3, True)], \
        f"started exactly once with (caption, 3, True), got {happy.starts}"
    assert happy.updates == [1, 2, 3], \
        f"one updateProgress per step with 1-based positions, got {happy.updates}"
    assert happy.cancel_calls == 3, \
        f"exactly one getIsCancelled() per step, got {happy.cancel_calls}"
    assert happy.deleted is False, \
        "a caller-owned progress is never deleted by the lib"
    assert np.array_equal(_readback_volume(vol), arr), \
        "the progress object must not distort the result"

    cancelled_late = _RecordingProgress(cancel_after=2)
    before = _channel_count()
    with _expect_runtime_error("Operation cancelled by user."):
        _publish_via_bridge('publish_np_as_volume', {
            'array': arr, 'title': 'tp_prog_cancelled'},
            progress=cancelled_late)
    assert cancelled_late.updates == [1, 2], \
        f"two steps completed before the cancel, got {cancelled_late.updates}"
    assert cancelled_late.cancel_calls == 3, \
        f"exactly one getIsCancelled() per step, got {cancelled_late.cancel_calls}"
    assert _channel_count() == before, \
        "a cancelled publish must leave no half-written channel behind"

    cancelled_early = _RecordingProgress(cancel_after=0)
    before = _channel_count()
    with _expect_runtime_error("Operation cancelled by user."):
        _publish_via_bridge('publish_np_as_volume', {
            'array': arr, 'title': 'tp_prog_early'},
            progress=cancelled_early)
    assert cancelled_early.updates == [], \
        f"nothing completed before the cancel, got {cancelled_early.updates}"
    assert _channel_count() == before, \
        "an immediately cancelled publish must create nothing"

    cancelled_roi = _RecordingProgress(cancel_after=2)
    with _expect_runtime_error("Operation cancelled by user."):
        _publish_via_bridge('publish_np_as_roi', {
            'array': _MASK, 'title': 'tp_prog_roi_cancelled'},
            progress=cancelled_roi)
    assert _channel_count() == before, \
        "a cancelled ROI publish must leave no scratch channel behind"
    print("  progress seam pinned through the handler: start (caption, 3, "
          "True) once, updates 1..3, one getIsCancelled per step, caller "
          "keeps ownership, results identical; cancels on the 1st/3rd check "
          "raise the exact RuntimeError and leave nothing behind")


def case_bridge_requests_carry_duck_progress_and_pump_false():
    """Round 8's structural pin, replacing round 7's run-suppression case:
    notebook-originated requests reach the lib ONLY through the handler
    layer, and every publish handler calls its public function with a DUCK
    progress — never ``True``, so no native ORS progress object is ever
    constructed on the notebook path — and with ``pump=False``, so a
    bridge request never pumps Dragonfly's event loop mid-handler. Pinned
    by intercepting the three public functions and reading the exact
    kwargs the handlers hand them, plus the duck-vs-native class rule and
    the handler table's exact shape."""
    real = {name: getattr(dfnb, name) for name in
            ('publish_np_as_volume', 'publish_np_as_roi',
             'publish_np_as_multiroi')}
    calls = []

    class _SpyCreated(object):
        """What a spied publish function returns: enough of an ORS object
        for ``_bridge_published_payload`` (GUID + title)."""

        def __init__(self, guid):
            self._guid = guid

        def getGUID(self):
            return self._guid

        def getTitle(self):
            return 'spy title'

    def _make_spy(name):
        def _spy(**kwargs):
            calls.append((name, kwargs))
            return _SpyCreated('spy-' + name)
        return _spy

    dfnb.publish_np_as_volume = _make_spy('publish_np_as_volume')
    dfnb.publish_np_as_roi = _make_spy('publish_np_as_roi')
    dfnb.publish_np_as_multiroi = _make_spy('publish_np_as_multiroi')
    try:
        duck = _RecordingProgress()
        labels = _PROPS_LABELS
        dfnb.BRIDGE_HANDLERS['publish_np_as_volume']({
            'array': np.zeros((2, 3, 4), np.float32),
            'title': 'spy-volume',
            'reference': {'guid': 'ref-guid', 'title': 'stale-title',
                          'class_name': 'Channel'},
            'spacing': [1.0, 2.0, 3.0]}, duck)
        dfnb.BRIDGE_HANDLERS['publish_np_as_roi']({
            'array': _MASK, 'title': 'spy-roi'}, duck)
        dfnb.BRIDGE_HANDLERS['publish_np_as_multiroi']({
            'array': labels, 'title': 'spy-mroi',
            'properties': {'3': 30.0, '1': 10.0}}, duck)

        assert [name for name, _kwargs in calls] == \
            ['publish_np_as_volume', 'publish_np_as_roi',
             'publish_np_as_multiroi'], \
            f"the three publish handlers must dispatch to their functions, got {calls!r}"
        for name, kwargs in calls:
            assert kwargs['progress'] is duck, \
                f"{name}: the handler must pass the bridge's DUCK progress " \
                f"(never True -- no native progress object is ever created " \
                f"on the notebook path), got {kwargs['progress']!r}"
            assert kwargs['pump'] is False, \
                f"{name}: the handler must pass pump=False (a bridge " \
                f"request never pumps Dragonfly's event loop), got {kwargs['pump']!r}"
        # the handle normalization: the reference handle contributes its
        # GUID (the precise handle, immune to a stale title)
        assert calls[0][1]['reference'] == 'ref-guid', \
            f"a reference handle must contribute its GUID, got {calls[0][1]['reference']!r}"
        # the label-keyed properties restore their int keys across the
        # JSON crossing (JSON object keys are always strings)
        assert calls[2][1]['properties'] == {1: 10.0, 3: 30.0}, \
            f"label-keyed properties must arrive with int keys restored, " \
            f"got {calls[2][1]['properties']!r}"
        # the duck can never BE the native Progress class, so the
        # native-dialog forwarding in _ProgressSession is structurally
        # unreachable from a notebook-originated request
        assert not isinstance(duck, dfnb.Progress), \
            "a bridge duck progress must never be a native ORS Progress"
    finally:
        for name, fn in real.items():
            setattr(dfnb, name, fn)

    # the handler table's shape: exactly the eleven methods, each a
    # handler(params, progress)
    expected = {'import_volume_as_np', 'import_roi_as_np',
                'import_multiroi_as_np', 'publish_np_as_volume',
                'publish_np_as_roi', 'publish_np_as_multiroi',
                'import_measured_properties', 'export_measured_properties',
                'list_measured_properties', 'list_objects', 'delete_object'}
    assert set(dfnb.BRIDGE_HANDLERS) == expected, \
        f"the handler table must cover exactly the eleven dfnb methods, got " \
        f"{sorted(dfnb.BRIDGE_HANDLERS)}"
    for name, handler in dfnb.BRIDGE_HANDLERS.items():
        parameters = list(inspect.signature(handler).parameters)
        assert parameters == ['params', 'progress'], \
            f"{name}: handler must take (params, progress), got {parameters}"
    print("  every publish handler passes a DUCK progress (never True) and "
          "pump=False; the reference handle contributes its GUID, label "
          "keys arrive restored, the duck is never the native Progress "
          "class; the table covers exactly the eleven methods with "
          "(params, progress) signatures")


def case_measured_properties_dataframe_and_columns():
    """list_measured_properties through the handler returns the slot
    descriptions in slot order; import_measured_properties returns a
    pandas.DataFrame with those columns and the fixture values — one row
    per label slot (row 0 is the background slot Dragonfly reserves, rows
    1..N are labels 1..N) —, honors a subset in the given order, and
    refuses unknown columns listing the available ones."""
    mroi = _multiroi_with_properties(
        _PROPS_LABELS, ("Volume", "Mean intensity"),
        ([0.0, 10.0, 20.0, 30.0], [0.0, 1.5, 2.5, 3.5]), "tp_props_mroi")
    guid = mroi.getGUID()

    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': guid}, None)['properties']
    assert listed == ["Volume", "Mean intensity"], \
        f"slot descriptions in slot order, got {listed!r}"

    frame = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': guid}, None)['table']
    assert isinstance(frame, pd.DataFrame), \
        f"expected a pandas.DataFrame, got {type(frame)}"
    assert [str(c) for c in frame.columns] == ["Volume", "Mean intensity"], \
        f"all columns in slot order, got {[str(c) for c in frame.columns]}"
    assert frame.shape == (4, 2), \
        f"one row per label slot (3 labels + background), got {frame.shape}"
    assert np.array_equal(frame["Volume"].to_numpy(), [0.0, 10.0, 20.0, 30.0]), \
        f"the Volume column must carry the fixture values, got {frame['Volume'].to_numpy()!r}"
    assert np.array_equal(frame["Mean intensity"].to_numpy(), [0.0, 1.5, 2.5, 3.5]), \
        "the Mean intensity column must carry the fixture values"

    subset = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': guid, 'properties': ["Mean intensity", "Volume"]},
        None)['table']
    assert [str(c) for c in subset.columns] == ["Mean intensity", "Volume"], \
        "a subset keeps the GIVEN order"
    assert list(subset["Volume"].to_numpy()) == [0.0, 10.0, 20.0, 30.0], \
        "the subset carries the same values"

    with _expect_value_error("not found. Available columns"):
        dfnb.BRIDGE_HANDLERS['import_measured_properties'](
            {'source': guid, 'properties': ["Volume", "Diameter"]}, None)
    with _expect_value_error("timestep 1 is out of range"):
        dfnb.BRIDGE_HANDLERS['list_measured_properties'](
            {'source': guid, 'timestep': 1}, None)
    print("  slot order, DataFrame columns and values (background row "
          "pinned), given-order subset and unknown-column refusal all pinned")


def case_export_measured_properties_write_then_readback():
    """export_measured_properties through the handler: custom slot names
    (plain, unicode, spaces) written from a dict AND a DataFrame round-trip
    through the READ path (list_measured_properties /
    import_measured_properties); a labelCount column is auto-padded with
    the 0.0 background row in front; a {label: value} dict column
    (unordered int keys) lands in label order; the ranges helper
    initializes each slot's min/max to the written values; duplicate names
    dedupe CSV-style ' (2)', ' (3)' — within a batch and against the
    MultiROI's existing slots."""
    mroi = _multiroi_from(_channel_from(np.zeros(_PROPS_LABELS.shape, np.uint16),
                                        "tp_export_mroi_template"),
                          _PROPS_LABELS, "tp_export_mroi", label_count=3)
    guid = mroi.getGUID()
    assert mroi.getScalarValuesSlotCount() == 0, \
        "the fixture starts with no property slots"

    def _export(properties):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': guid, 'properties': properties}, None)

    # dict write: a labelCount column (auto-padded) and a full
    # labelCount + 1 column; names with a space and unicode
    _export({
        'Mean intensity': [1.5, 2.5, 3.5],          # 3 entries -> padded
        'Ø Fläche (µm²)': [0.0, 10.0, 20.0, 30.0],  # 4 entries -> verbatim
    })
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': guid}, None)['properties']
    assert listed == ['Mean intensity', 'Ø Fläche (µm²)'], \
        f"the slot names must round-trip, got {listed!r}"
    frame = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': guid}, None)['table']
    assert [str(c) for c in frame.columns] == ['Mean intensity', 'Ø Fläche (µm²)']
    assert np.array_equal(frame['Mean intensity'].to_numpy(),
                          [0.0, 1.5, 2.5, 3.5]), \
        "the auto-padded column carries the 0.0 background row in front"
    assert np.array_equal(frame['Ø Fläche (µm²)'].to_numpy(),
                          [0.0, 10.0, 20.0, 30.0])

    # the ranges helper: every slot's min/max initialized from the values,
    # over the LABEL rows only (row 0 is the background slot, not an object
    # -- getScalarValuesDataRange reads rows 1..labelCount)
    assert mroi.getScalarValuesRangeMin(0, 0) == 1.5 and \
        mroi.getScalarValuesRangeMax(0, 0) == 3.5, \
        f"slot 0 range over the label rows must be [1.5, 3.5], got " \
        f"[{mroi.getScalarValuesRangeMin(0, 0)}, {mroi.getScalarValuesRangeMax(0, 0)}]"
    assert mroi.getScalarValuesRangeMin(1, 0) == 10.0 and \
        mroi.getScalarValuesRangeMax(1, 0) == 30.0, \
        "slot 1 range over the label rows must be [10.0, 30.0]"

    # dedupe AGAINST the existing slots: a second 'Mean intensity' becomes
    # 'Mean intensity (2)', a third 'Mean intensity (3)'
    _export({'Mean intensity': [9.0, 9.0, 9.0]})
    _export({'Mean intensity': [8.0, 8.0, 8.0]})
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': guid}, None)['properties']
    assert listed == ['Mean intensity', 'Ø Fläche (µm²)', 'Mean intensity (2)',
                      'Mean intensity (3)'], \
        f"CSV-style dedupe against existing slots, got {listed!r}"
    frame = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': guid, 'properties': ['Mean intensity (2)']}, None)['table']
    assert np.array_equal(frame['Mean intensity (2)'].to_numpy(),
                          [0.0, 9.0, 9.0, 9.0]), \
        "the deduped slot carries its own values (auto-padded)"

    # a DataFrame write on a FRESH MultiROI: duplicate COLUMN names are two
    # properties, deduped within the batch (positional, not by column label)
    template = _channel_from(np.zeros(_PROPS_LABELS.shape, np.uint16),
                             "tp_export_mroi_df_template")
    mroi_df = _multiroi_from(template, _PROPS_LABELS, "tp_export_mroi_df",
                             label_count=3)
    frame_in = pd.DataFrame({'Mean intensity': [4.0, 5.0, 6.0],
                             'Woho': [7.0, 8.0, 9.0]})
    dfnb.BRIDGE_HANDLERS['export_measured_properties'](
        {'source': mroi_df.getGUID(), 'properties': frame_in}, None)
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': mroi_df.getGUID()}, None)['properties']
    assert listed == ['Mean intensity', 'Woho'], \
        f"the DataFrame columns become the slot names, got {listed!r}"
    frame_out = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': mroi_df.getGUID()}, None)['table']
    assert np.array_equal(frame_out['Mean intensity'].to_numpy(),
                          [0.0, 4.0, 5.0, 6.0]) and \
        np.array_equal(frame_out['Woho'].to_numpy(), [0.0, 7.0, 8.0, 9.0]), \
        "the DataFrame rows round-trip (auto-padded background row)"

    # a DataFrame with TWO columns named alike: both survive positionally,
    # deduped to 'A' and 'A (2)' with their own values
    template_dup = _channel_from(np.zeros(_PROPS_LABELS.shape, np.uint16),
                                 "tp_export_mroi_dup_template")
    mroi_dup = _multiroi_from(template_dup, _PROPS_LABELS, "tp_export_mroi_dup",
                              label_count=3)
    frame_dup = pd.DataFrame(np.array([[1.0, 11.0], [2.0, 12.0], [3.0, 13.0]]),
                             columns=['A', 'A'])
    dfnb.BRIDGE_HANDLERS['export_measured_properties'](
        {'source': mroi_dup.getGUID(), 'properties': frame_dup}, None)
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': mroi_dup.getGUID()}, None)['properties']
    assert listed == ['A', 'A (2)'], \
        (f"duplicate DataFrame columns dedupe within the batch, got "
         f"{listed!r}")
    frame_dup_out = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': mroi_dup.getGUID()}, None)['table']
    assert np.array_equal(frame_dup_out['A'].to_numpy(),
                          [0.0, 1.0, 2.0, 3.0]) and \
        np.array_equal(frame_dup_out['A (2)'].to_numpy(),
                       [0.0, 11.0, 12.0, 13.0]), \
        "the two same-named columns keep their own values"
    # a {label: value} dict write on a FRESH MultiROI — the per-region
    # shape regionprops-driven cell code produces ({1: v1, ..., N: vN},
    # unordered keys, int keys): ordered into a column by label, the 0.0
    # background row auto-padded in front
    template_lk = _channel_from(np.zeros(_PROPS_LABELS.shape, np.uint16),
                                "tp_export_mroi_lk_template")
    mroi_lk = _multiroi_from(template_lk, _PROPS_LABELS, "tp_export_mroi_lk",
                             label_count=3)
    dfnb.BRIDGE_HANDLERS['export_measured_properties'](
        {'source': mroi_lk.getGUID(),
         'properties': {'Region volume (voxels)': {3: 30.0, 1: 10.0, 2: 20.0},
                        'Solidity': {i: float(i) + 0.5 for i in range(1, 4)}}},
        None)
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': mroi_lk.getGUID()}, None)['properties']
    assert listed == ['Region volume (voxels)', 'Solidity'], \
        (f"the label-keyed dict property names round-trip, got {listed!r}")
    frame_lk = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': mroi_lk.getGUID()}, None)['table']
    assert np.array_equal(frame_lk['Region volume (voxels)'].to_numpy(),
                          [0.0, 10.0, 20.0, 30.0]), \
        ("an UNORDERED {label: value} column lands in label order with the "
         "0.0 background row in front")
    assert np.array_equal(frame_lk['Solidity'].to_numpy(),
                          [0.0, 1.5, 2.5, 3.5]), \
        "a comprehension-built {label: value} column round-trips the same way"

    print("  dict + DataFrame writes round-trip through the read path "
          "(unicode/space names, auto-padding, {label: value} columns "
          "ordered by label), ranges pinned to min/max, "
          "dedupe ' (2)'/' (3)' against slots, within batches, and for "
          "duplicate DataFrame columns")


def case_export_measured_properties_preflight_refusals():
    """Every refusal happens PRE-FLIGHT: a bad length among otherwise valid
    columns, an empty name, 2-D values, a non-DataFrame/dict, a wrong {label:
    value} key set (0-based, partial, non-int), a wrong-class source and an
    unresolvable source raise ValueError with NOTHING written
    (slot count unchanged) and NOTHING created (MultiROI and Channel
    instance counts unchanged). Round 10: every shape is re-run with
    wipe=True — still refused, AND the existing slot count, descriptions
    and values untouched (the wipe reset runs only AFTER the pre-flight)."""
    mroi = _multiroi_from(_channel_from(np.zeros(_PROPS_LABELS.shape, np.uint16),
                                        "tp_preflight_mroi_template"),
                          _PROPS_LABELS, "tp_preflight_mroi", label_count=3)
    guid = mroi.getGUID()
    # the wrong-class fixture is built BEFORE the counts are snapshotted
    # (the test's own fixture channel is not a leak)
    wrong_class_volume = _channel_from(_VOL_F32, "tp_preflight_volume")
    slots_before = mroi.getScalarValuesSlotCount()
    mroi_count_before = len(list(MultiROI.getAllInstances()))
    channels_before = _channel_count()

    def _export(properties):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': guid, 'properties': properties}, None)

    def _nothing_changed(what):
        assert mroi.getScalarValuesSlotCount() == slots_before, \
            f"{what}: a refused call must not grow the slot count"
        assert len(list(MultiROI.getAllInstances())) == mroi_count_before, \
            f"{what}: a refused call must not create a MultiROI"
        assert _channel_count() == channels_before, \
            f"{what}: a refused call must not create a Channel"

    # a VALID column followed by an invalid one still writes nothing
    with _expect_value_error("must carry 4 entries"):
        _export({
            'Good': [0.0, 1.0, 2.0, 3.0],
            'Bad': [1.0, 2.0, 3.0, 4.0, 5.0]})
    _nothing_changed("bad length after a valid column")

    # {label: value} columns with a WRONG key set: 0-based keys, a partial
    # map and non-int keys all refuse pre-flight with the label-range
    # message (a numpy array column among them still writes nothing)
    with _expect_value_error("maps each label 1..3"):
        _export({
            'Good': [0.0, 1.0, 2.0, 3.0],
            '0-based': {0: 1.0, 1: 2.0, 2: 3.0}})
    _nothing_changed("0-based label keys after a valid column")
    with _expect_value_error("maps each label 1..3"):
        _export({'Partial': {1: 1.0, 3: 3.0}})
    _nothing_changed("partial label map")
    with _expect_value_error("maps each label 1..3"):
        _export({'Named': {'a': 1.0, 'b': 2.0, 'c': 3.0}})
    _nothing_changed("non-int label keys")
    # int-colliding keys ('1' and 1) must not slip through as label 1
    # twice — the exact-sequence key check is what catches them
    with _expect_value_error("maps each label 1..3"):
        _export({'Dup': {1: 1.0, '1': 2.0, 2: 3.0, 3: 4.0}})
    _nothing_changed("duplicate int-colliding label keys")

    # the other pre-flight refusals
    with _expect_value_error("must be non-empty"):
        _export({'   ': [0.0, 1.0, 2.0, 3.0]})
    _nothing_changed("empty name")
    with _expect_value_error("must be one-dimensional"):
        _export({'Bad': np.zeros((2, 2), np.float64)})
    _nothing_changed("2-D values")
    with _expect_value_error("must be a pandas.DataFrame"):
        _export([1.0, 2.0, 3.0, 4.0])
    _nothing_changed("list properties")
    with _expect_value_error("expected a MultiROI, got Channel"):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': wrong_class_volume.getGUID(),
             'properties': {'A': [0.0, 1.0, 2.0, 3.0]}}, None)
    _nothing_changed("wrong-class source")
    with _expect_value_error("No MultiROI with the title or GUID"):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': 'tp_no_such_mroi',
             'properties': {'A': [0.0, 1.0, 2.0, 3.0]}}, None)
    _nothing_changed("unresolvable source")

    # round 10 — the no-data-loss contract under wipe: every refusal shape
    # above is re-run with wipe=True and must STILL raise AND leave the
    # slot set untouched. The reset (setScalarValuesSlotCount(0)) runs only
    # AFTER the pre-flight passes, so a failing payload can never destroy
    # existing properties. The target first gets real properties so the
    # "nothing destroyed" assertion observes something that could have been
    # lost.
    _export({'Kept A': [0.0, 1.0, 2.0, 3.0],
             'Kept B': [0.0, 4.0, 5.0, 6.0]})
    wipe_slots_before = mroi.getScalarValuesSlotCount()
    wipe_descriptions_before = [
        str(mroi.getScalarValueDescription(slot, 0))
        for slot in range(wipe_slots_before)]

    def _export_wipe(properties):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': guid, 'properties': properties, 'wipe': True}, None)

    def _wipe_nothing_changed(what):
        assert mroi.getScalarValuesSlotCount() == wipe_slots_before, \
            f"{what}: a refused wipe must not change the slot count"
        assert [str(mroi.getScalarValueDescription(slot, 0))
                for slot in range(wipe_slots_before)] \
            == wipe_descriptions_before, \
            f"{what}: a refused wipe must not change the descriptions"
        assert len(list(MultiROI.getAllInstances())) == mroi_count_before, \
            f"{what}: a refused wipe must not create a MultiROI"
        assert _channel_count() == channels_before, \
            f"{what}: a refused wipe must not create a Channel"

    with _expect_value_error("must carry 4 entries"):
        _export_wipe({
            'Good': [0.0, 1.0, 2.0, 3.0],
            'Bad': [1.0, 2.0, 3.0, 4.0, 5.0]})
    _wipe_nothing_changed("bad length after a valid column (wipe)")
    with _expect_value_error("maps each label 1..3"):
        _export_wipe({
            'Good': [0.0, 1.0, 2.0, 3.0],
            '0-based': {0: 1.0, 1: 2.0, 2: 3.0}})
    _wipe_nothing_changed("0-based label keys after a valid column (wipe)")
    with _expect_value_error("maps each label 1..3"):
        _export_wipe({'Partial': {1: 1.0, 3: 3.0}})
    _wipe_nothing_changed("partial label map (wipe)")
    with _expect_value_error("maps each label 1..3"):
        _export_wipe({'Named': {'a': 1.0, 'b': 2.0, 'c': 3.0}})
    _wipe_nothing_changed("non-int label keys (wipe)")
    with _expect_value_error("maps each label 1..3"):
        _export_wipe({'Dup': {1: 1.0, '1': 2.0, 2: 3.0, 3: 4.0}})
    _wipe_nothing_changed("duplicate int-colliding label keys (wipe)")
    with _expect_value_error("must be non-empty"):
        _export_wipe({'   ': [0.0, 1.0, 2.0, 3.0]})
    _wipe_nothing_changed("empty name (wipe)")
    with _expect_value_error("must be one-dimensional"):
        _export_wipe({'Bad': np.zeros((2, 2), np.float64)})
    _wipe_nothing_changed("2-D values (wipe)")
    with _expect_value_error("must be a pandas.DataFrame"):
        _export_wipe([1.0, 2.0, 3.0, 4.0])
    _wipe_nothing_changed("list properties (wipe)")
    with _expect_value_error("expected a MultiROI, got Channel"):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': wrong_class_volume.getGUID(),
             'properties': {'A': [0.0, 1.0, 2.0, 3.0]}, 'wipe': True}, None)
    _wipe_nothing_changed("wrong-class source (wipe)")
    with _expect_value_error("No MultiROI with the title or GUID"):
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](
            {'source': 'tp_no_such_mroi',
             'properties': {'A': [0.0, 1.0, 2.0, 3.0]}, 'wipe': True}, None)
    _wipe_nothing_changed("unresolvable source (wipe)")
    # and the kept data really still reads back with its values
    frame_kept = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': guid}, None)['table']
    assert np.array_equal(frame_kept['Kept A'].to_numpy(),
                          [0.0, 1.0, 2.0, 3.0]) and \
        np.array_equal(frame_kept['Kept B'].to_numpy(), [0.0, 4.0, 5.0, 6.0]), \
        "after every refused wipe the kept properties still read back"
    print("  bad length / empty name / 2-D values / list properties / "
          "0-based-partial-non-int label keys / wrong class / unresolvable "
          "source all refused pre-flight: zero slots added, zero MultiROI "
          "or Channel objects created")
    print("  every refusal shape re-run with wipe=True: still refused AND "
          "the existing slot count + descriptions + values untouched (the "
          "reset only ever runs after the pre-flight passes)")


def case_export_measured_properties_wipe():
    """wipe=True replaces ALL existing measured properties (the round-10
    probe-confirmed ``setScalarValuesSlotCount(0)`` reset): after the wipe
    write the slot list carries ONLY the new columns, the old values are
    gone, and the readback equals the replacement. wipe=False (default, the
    key omitted) is unchanged add-with-dedupe: a later write appends and
    dedupes against the wiped-in slots."""
    template = _channel_from(np.zeros(_PROPS_LABELS.shape, np.uint16),
                             "tp_wipe_mroi_template")
    mroi = _multiroi_from(template, _PROPS_LABELS, "tp_wipe_mroi",
                          label_count=3)
    guid = mroi.getGUID()

    def _export(properties, wipe=None):
        # wipe=None omits the key entirely: the DEFAULT path is pinned as
        # the byte-identical pre-wipe payload shape
        params = {'source': guid, 'properties': properties}
        if wipe is not None:
            params['wipe'] = wipe
        dfnb.BRIDGE_HANDLERS['export_measured_properties'](params, None)

    def _listed():
        return dfnb.BRIDGE_HANDLERS['list_measured_properties'](
            {'source': guid}, None)['properties']

    def _frame():
        return dfnb.BRIDGE_HANDLERS['import_measured_properties'](
            {'source': guid}, None)['table']

    # baseline: A (two columns) lands as two slots
    _export({'Area': [1.0, 2.0, 3.0], 'Volume': [10.0, 20.0, 30.0]})
    assert _listed() == ['Area', 'Volume'], \
        f"the add-mode baseline writes both columns, got {_listed()!r}"
    assert mroi.getScalarValuesSlotCount() == 2

    # B under wipe=True: ONLY B survives — the old slot count, names and
    # values are gone (the probe-confirmed clean reset)
    _export({'Perimeter': [7.0, 8.0, 9.0]}, wipe=True)
    listed = _listed()
    assert listed == ['Perimeter'], \
        f"the wiped write must leave ONLY the new column, got {listed!r}"
    assert mroi.getScalarValuesSlotCount() == 1, \
        ("the wipe resets the slot count before the new write "
         "(got {})".format(mroi.getScalarValuesSlotCount()))
    frame = _frame()
    assert list(frame.columns) == ['Perimeter'], \
        f"the readback carries ONLY the new columns, got {list(frame.columns)!r}"
    assert np.array_equal(frame['Perimeter'].to_numpy(),
                          [0.0, 7.0, 8.0, 9.0]), \
        "the replacement column round-trips with the auto-padded background"
    assert 'Area' not in frame.columns and 'Volume' not in frame.columns, \
        "the wiped-away columns must not appear in the readback"

    # the default (no wipe key) still ADDS: C appended alongside B, and a
    # repeat of B's name still dedupes CSV-style against the wiped-in slot
    _export({'Sphericity': [2.0, 2.0, 2.0]})
    listed = _listed()
    assert listed == ['Perimeter', 'Sphericity'], \
        f"the default write must append alongside the wiped-in slot, got {listed!r}"
    assert mroi.getScalarValuesSlotCount() == 2
    _export({'Perimeter': [5.0, 5.0, 5.0]})
    listed = _listed()
    assert listed == ['Perimeter', 'Sphericity', 'Perimeter (2)'], \
        f"add-with-dedupe stays intact after a wipe, got {listed!r}"
    frame = _frame()
    assert np.array_equal(frame['Perimeter (2)'].to_numpy(),
                          [0.0, 5.0, 5.0, 5.0]), \
        "the deduped slot carries its own values"
    print("  wipe=True replaced the two-slot set with the single new column "
          "(slot count 1, readback equals B, old values gone); the default "
          "write then appends and dedupes alongside it (add mode intact)")


def case_publish_np_as_multiroi_with_properties_end_to_end():
    """publish_np_as_multiroi(properties=...) through the handler: the
    fresh MultiROI carries the properties readable through the standard
    read path (dict AND DataFrame), and a publish whose properties are
    REJECTED leaves no MultiROI and no scratch channel behind."""
    mroi = _publish_via_bridge('publish_np_as_multiroi', {
        'array': _PROPS_LABELS, 'title': "tp_pub_mroi_props",
        'properties': {'Volume': [10.0, 20.0, 30.0],
                       'Mean intensity': [1.0, 2.0, 3.0]}})
    assert isinstance(mroi, MultiROI)
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': mroi.getGUID()}, None)['properties']
    assert listed == ['Volume', 'Mean intensity']
    frame = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': mroi.getGUID()}, None)['table']
    assert np.array_equal(frame['Volume'].to_numpy(), [0.0, 10.0, 20.0, 30.0])
    assert np.array_equal(frame['Mean intensity'].to_numpy(), [0.0, 1.0, 2.0, 3.0])

    frame_props = pd.DataFrame({'Sphericity': [0.5, 0.6, 0.7],
                                'Label': [10.0, 20.0, 30.0]})
    mroi_df = _publish_via_bridge('publish_np_as_multiroi', {
        'array': _PROPS_LABELS, 'title': "tp_pub_mroi_props_df",
        'properties': frame_props})
    listed = dfnb.BRIDGE_HANDLERS['list_measured_properties'](
        {'source': mroi_df.getGUID()}, None)['properties']
    assert listed == ['Sphericity', 'Label']
    frame_out = dfnb.BRIDGE_HANDLERS['import_measured_properties'](
        {'source': mroi_df.getGUID()}, None)['table']
    assert np.array_equal(frame_out['Sphericity'].to_numpy(),
                          [0.0, 0.5, 0.6, 0.7])

    # the failure path: a rejected properties argument deletes the fresh
    # MultiROI again (and the scratch channel never survives)
    mroi_before = len(list(MultiROI.getAllInstances()))
    channels_before = _channel_count()
    with _expect_value_error("must carry 4 entries"):
        _publish_via_bridge('publish_np_as_multiroi', {
            'array': _PROPS_LABELS, 'title': "tp_pub_mroi_bad",
            'properties': {'Bad': [1.0, 2.0]}})
    assert len(list(MultiROI.getAllInstances())) == mroi_before, \
        "a rejected publish must leave no MultiROI behind"
    assert _channel_count() == channels_before, \
        "a rejected publish must leave no scratch channel behind"
    print("  publish-with-properties round-trips the columns end to end "
          "(dict + DataFrame); a rejected properties argument leaves no "
          "MultiROI and no scratch channel behind")


def case_list_objects_lists_sorted_dicts_and_filters_representable():
    """list_objects through the handler returns {'guid', 'title',
    'class_name'} dicts sorted by title; a temporary object is hidden by
    representable_only=True (the default) and listed by
    representable_only=False; unknown class names are refused."""
    first = _channel_from(_VOL_F32, "tp_list_a_first")
    second = _channel_from(_VOL_F32, "tp_list_b_second")

    entries = dfnb.BRIDGE_HANDLERS['list_objects'](
        {'class_name': 'Channel', 'representable_only': True}, None)['objects']
    titles = [entry["title"] for entry in entries]
    assert titles == sorted(titles), f"entries must be sorted by title, got {titles!r}"
    by_guid = {entry["guid"]: entry for entry in entries}
    for obj in (first, second):
        entry = by_guid.get(obj.getGUID())
        assert entry is not None, \
            f"the representable fixture channel {obj.getTitle()!r} must be listed"
        assert entry["title"] == obj.getTitle() and entry["class_name"] == "Channel", \
            f"the dict must carry title and class name, got {entry!r}"

    temp = Channel()
    _track(temp)
    temp.setAsTemporaryObject()
    assert temp.getIsRepresentable() is False, \
        "a temporary object must not be representable (fixture sanity)"
    listed_guids = {entry["guid"] for entry in
                    dfnb.BRIDGE_HANDLERS['list_objects'](
                        {'class_name': 'Channel'}, None)['objects']}
    assert temp.getGUID() not in listed_guids, \
        "representable_only=True must hide the temporary channel"
    listed_all = {entry["guid"] for entry in
                  dfnb.BRIDGE_HANDLERS['list_objects'](
                      {'class_name': 'Channel', 'representable_only': False},
                      None)['objects']}
    assert temp.getGUID() in listed_all, \
        "representable_only=False must still list the temporary channel"

    with _expect_value_error("Unknown ORS class"):
        dfnb.BRIDGE_HANDLERS['list_objects']({'class_name': 'NoSuchClass'},
                                             None)
    with _expect_value_error("class_name must be an ORS class name"):
        dfnb.BRIDGE_HANDLERS['list_objects']({'class_name': ''}, None)
    print("  sorted {'guid', 'title', 'class_name'} dicts; temporary "
          "objects hidden by default and listed with "
          "representable_only=False; unknown class names refused")


# ---- the pipe loopback (real QLocalServer + real DfnbClient) ---------------

def _ensure_qt_app():
    """The loopback needs a QCoreApplication (QLocalServer/QLocalSocket
    want one); reuses the process's instance when ORS made one. The
    CALLER must bind the returned wrapper: PyQt gives Python ownership of
    a parentless core application, and an unbound wrapper is destroyed by
    the next GC — taking the event dispatcher (timers, queued signal
    delivery, the accept path) with it. Probed this window: the identical
    handshake passes with the wrapper bound and stalls within the first
    ``waitForReadyRead`` cycle when it is dropped."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(['DragonflyNotebook-datapath'])
    return app


def _pump_once():
    """One bounded event-loop pass — the loopback's service side needs
    loop turns to accept, read, execute and flush."""
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)


def case_loopback_real_service_and_client_ship_arrays_and_tables():
    """End to end over the REAL pipe: a real ``NotebookBridgeService``
    (QLocalServer + the real ``BRIDGE_HANDLERS``) and a real ``DfnbClient``
    in this same headless process — the exact framing, version handshake,
    chunked transfer, error and progress envelopes the two processes use.
    Real arrays cross as npy bytes, a DataFrame crosses as column/index
    arrays and comes back a real DataFrame, a published handle comes back
    and re-resolves into ORS, the dtype-trap rule holds across the pipe, an
    export with INT label keys lands after the JSON string-key crossing,
    ValueError semantics cross the error envelope unchanged, and progress
    events arrive as frames on the client callback.

    The ``QCoreApplication`` MUST be bound to a local: PyQt gives Python
    ownership of a parentless core application, so discarding the wrapper
    lets the next GC destroy the C++ app and its event dispatcher — the
    probed failure mode this window (the service's ``newConnection``
    queued delivery and every timer go silent and the handshake stalls).
    Binding it here keeps the dispatcher alive for the whole case."""
    app = _ensure_qt_app()
    vol = _channel_from(_VOL_F32, "tp_loopback_volume")
    roi = _roi_from(_MASK, "tp_loopback_roi")
    mroi = _multiroi_with_properties(
        _PROPS_LABELS, ("Volume", "Mean intensity"),
        ([0.0, 10.0, 20.0, 30.0], [0.0, 1.5, 2.5, 3.5]), "tp_loopback_mroi")

    service = NotebookBridgeService(
        dict(dfnb.BRIDGE_HANDLERS),
        pipe_name='dfnb_bridge_datapath_loopback')
    assert service.is_listening, 'the loopback service must listen'
    client = DfnbClient(pipe_name=service.pipe_name)
    try:
        assert client.connect(service.pipe_name), 'the client must handshake'
        assert service.connection_count == 1, 'one connection arrived'

        # list_objects through the pipe
        entries = client.list_objects('Channel')
        by_guid = {entry['guid']: entry for entry in entries}
        vol_entry = by_guid.get(vol.getGUID())
        assert vol_entry is not None and \
            vol_entry['title'] == 'tp_loopback_volume' and \
            vol_entry['class_name'] == 'Channel', \
            f'the fixture channel must be listed through the pipe, got {vol_entry!r}'

        # imports through the pipe: real arrays cross as npy bytes
        got = client.import_volume_as_np('tp_loopback_volume')
        assert got.dtype == np.float32 and np.array_equal(got, _VOL_F32), \
            'the volume must cross bitwise'
        mask = client.import_roi_as_np({'guid': roi.getGUID()})
        assert mask.dtype == np.uint8 and np.array_equal(mask, _MASK), \
            'the ROI mask must cross bitwise'
        labels = client.import_multiroi_as_np(mroi.getGUID())
        assert labels.dtype == np.uint8 and np.array_equal(labels, _PROPS_LABELS), \
            'the labels must cross bitwise'

        # publishes through the pipe: the handle comes back and re-resolves
        arr = np.arange(24, dtype=np.float32).reshape(2, 3, 4) + 0.25
        handle = client.publish_np_as_volume(
            arr, title='tp_loopback_pub', spacing=(2.0, 1.0, 0.5))
        assert set(handle.keys()) >= {'guid', 'title', 'class_name'}, \
            f'the publish must return the handle dict, got {dict(handle)!r}'
        assert handle['class_name'] == 'Channel' and handle['title'] == 'tp_loopback_pub'
        pub = orsObj(handle['guid'])
        assert pub is not None and isinstance(pub, Channel), \
            'the handle must re-resolve into a live ORS Channel'
        _track(pub)
        assert (pub.getZSize(), pub.getYSize(), pub.getXSize()) == (2, 3, 4), \
            'the shape must survive the npy crossing'
        assert (pub.getXSpacing(), pub.getYSpacing(), pub.getZSpacing()) == \
            (2.0, 1.0, 0.5), 'the spacing must arrive as a 3-sequence'
        assert np.array_equal(_readback_volume(pub), arr), \
            'the values must cross bitwise'

        # the handle re-enters as a reference: the dtype-trap rule across
        # the pipe (the array's dtype + shape, the REFERENCE's spacing —
        # here the handle carries tp_loopback_pub's spacing)
        trap = (np.arange(60, dtype=np.uint8).reshape(3, 4, 5) % 7) * 30
        trap_handle = client.publish_np_as_volume(
            trap, title='tp_loopback_trap', reference=handle)
        pub2 = orsObj(trap_handle['guid'])
        assert pub2 is not None
        _track(pub2)
        assert pub2.dtype == np.uint8 and \
            (pub2.getZSize(), pub2.getYSize(), pub2.getXSize()) == (3, 4, 5) and \
            (pub2.getXSpacing(), pub2.getYSpacing(), pub2.getZSpacing()) == \
            (2.0, 1.0, 0.5), 'the dtype-trap rule must hold across the pipe'
        assert np.array_equal(_readback_volume(pub2), trap)

        # the GUID-STRING reference form crosses the same way: a fresh
        # reference channel's spacing must win while the array's dtype and
        # shape still hold
        reference = _channel_from(np.zeros((2, 3, 4), np.float32),
                                  'tp_loopback_ref')
        reference.setXSpacing(1.5)
        reference.setYSpacing(2.0)
        reference.setZSpacing(2.5)
        trap_handle2 = client.publish_np_as_volume(
            trap, title='tp_loopback_trap2', reference=reference.getGUID())
        pub3 = orsObj(trap_handle2['guid'])
        assert pub3 is not None
        _track(pub3)
        assert pub3.dtype == np.uint8 and \
            (pub3.getZSize(), pub3.getYSize(), pub3.getXSize()) == (3, 4, 5) and \
            (pub3.getXSpacing(), pub3.getYSpacing(), pub3.getZSpacing()) == \
            (1.5, 2.0, 2.5), \
            'the GUID-string reference must rule the spacing across the pipe'
        assert np.array_equal(_readback_volume(pub3), trap)

        # measured properties cross as column/index arrays and come back a
        # real DataFrame
        frame = client.import_measured_properties(mroi.getGUID())
        assert isinstance(frame, pd.DataFrame), \
            f'a DataFrame must come back across the pipe, got {type(frame)}'
        assert [str(c) for c in frame.columns] == ['Volume', 'Mean intensity']
        assert np.array_equal(frame['Volume'].to_numpy(), [0.0, 10.0, 20.0, 30.0]), \
            f'the Volume column must survive the crossing, got {frame["Volume"].to_numpy()!r}'
        assert np.array_equal(frame['Mean intensity'].to_numpy(), [0.0, 1.5, 2.5, 3.5])

        # an export with INT label keys: the JSON crossing turns dict keys
        # into strings; the service restores them (the write must land)
        client.export_measured_properties(
            mroi.getGUID(), {'Extra': {3: 30.0, 1: 10.0, 2: 20.0}})
        assert client.list_measured_properties(mroi.getGUID()) == \
            ['Volume', 'Mean intensity', 'Extra'], \
            'the label-keyed export must cross and land'
        frame2 = client.import_measured_properties(
            mroi.getGUID(), properties=['Extra'])
        assert np.array_equal(frame2['Extra'].to_numpy(), [0.0, 10.0, 20.0, 30.0]), \
            'the label-keyed values must land in label order'

        # ValueError semantics cross the error envelope unchanged
        with _expect_value_error('No Channel with the title or GUID'):
            client.import_volume_as_np('tp_no_such_loopback_volume')

        # progress events arrive as frames on the client callback
        frames = []
        client.progress_callback = \
            lambda caption, step, of: frames.append((caption, step, of))
        try:
            client.publish_np_as_volume(arr, title='tp_loopback_progress')
        finally:
            client.progress_callback = None
        captions = {f[0] for f in frames}
        steps = [f[1] for f in frames]
        assert captions == {'Publishing numpy array as volume'}, \
            f'the progress frames must carry the caption, got {captions!r}'
        assert steps == [0, 1, 2, 3], \
            (f'the bridge duck progress drives the SAME 3 steps (the start '
             f'plus 1..3), got {steps!r}')
        assert all(f[2] == 3 for f in frames), 'every frame carries of=3'
    finally:
        client.close()
        service.close()
        _pump_once()
    print('  over the REAL pipe: arrays cross bitwise, a DataFrame returns, '
          'handles re-resolve, the dtype-trap rule holds, int label keys '
          'survive the JSON crossing, ValueError semantics cross, progress '
          'frames arrive (steps 0..3 of 3)')


def case_loopback_cancel_frame_aborts_a_running_handler():
    """The cancel path across the REAL pipe, without the app client (its
    queued-signal mechanics are tier-1-pinned): a raw protocol connection
    sends a request whose handler loops on the duck progress's
    getIsCancelled() — sleeping and polling ONLY, no event loop (the
    round-6 rule) — then sends a cancel frame mid-handler. The checkpoint
    pulls the pending bytes with a timed-out waitForReadyRead (probed on
    the shipped Qt: pending bytes move without the event loop and without
    delivering queued slots) and the handler aborts with the exact
    RuntimeError, which crosses back as the error envelope. The
    ``QCoreApplication`` is bound to a local for the same reason the other
    loopback case documents: an unbound parentless wrapper is GC-destroyed
    and takes the dispatcher (and the accept path) with it."""
    app = _ensure_qt_app()
    started = threading.Event()
    result = {}

    def _wait_for_cancel_handler(params, progress):
        started.set()
        deadline = time.monotonic() + 10.0
        while not progress.getIsCancelled():
            if time.monotonic() > deadline:
                return {'aborted': False}
            time.sleep(0.01)
        raise RuntimeError('Operation cancelled by user.')

    def _raw_client():
        """A minimal protocol client on its OWN thread (each Qt socket is
        used only from its creating thread): handshake, request, cancel
        mid-handler, await the response."""
        socket = QLocalSocket()
        socket.connectToServer(service.pipe_name)
        if not socket.waitForConnected(3000):
            result['error'] = 'no connection: ' + socket.errorString()
            return
        reader = protocol.FrameReader()
        try:
            socket.write(protocol.encode_frame(
                {'type': protocol.HELLO,
                 'version': protocol.PROTOCOL_VERSION}))
            socket.waitForBytesWritten(2000)
            deadline = time.monotonic() + 5.0
            welcomed = False
            while time.monotonic() < deadline and not welcomed:
                if socket.waitForReadyRead(100):
                    for envelope, _parts in reader.feed(
                            bytes(socket.readAll().data())):
                        if envelope.get('type') == protocol.WELCOME:
                            welcomed = True
            if not welcomed:
                result['error'] = 'no welcome'
                return
            socket.write(protocol.encode_frame(
                {'type': protocol.REQUEST, 'id': 77,
                 'method': 'wait_for_cancel', 'params': {}}))
            socket.waitForBytesWritten(2000)
            if not started.wait(5.0):
                result['error'] = 'the handler never started'
                return
            time.sleep(0.05)  # at least one checkpoint saw no cancel yet
            socket.write(protocol.encode_frame(
                {'type': protocol.CANCEL, 'id': 77}))
            socket.waitForBytesWritten(2000)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if socket.waitForReadyRead(100):
                    for envelope, _parts in reader.feed(
                            bytes(socket.readAll().data())):
                        if envelope.get('type') == protocol.RESPONSE:
                            result['response'] = envelope
                            return
            result['error'] = 'no response within 10 s'
        finally:
            socket.disconnectFromServer()

    service = NotebookBridgeService(
        {'wait_for_cancel': _wait_for_cancel_handler},
        pipe_name='dfnb_bridge_datapath_cancel')
    try:
        assert service.is_listening
        thread = threading.Thread(target=_raw_client)
        thread.start()
        # the service lives on THIS thread: pump until the exchange settles
        deadline = time.monotonic() + 30.0
        while thread.is_alive() and time.monotonic() < deadline:
            _pump_once()
            time.sleep(0.01)
        thread.join(5.0)
        assert not thread.is_alive(), 'the raw client must finish'
        assert 'error' not in result, \
            f'raw client error: {result.get("error")}'
        response = result['response']
        assert response.get('ok') is False and \
            response.get('error_class') == 'RuntimeError' and \
            response.get('message') == 'Operation cancelled by user.', \
            (f'the cancel must abort the handler with the exact RuntimeError, '
             f'got {response!r}')
    finally:
        service.close()
        _pump_once()
    print('  a cancel frame sent mid-handler aborts the handler at its '
          'checkpoint (no event loop, no queued slots) with the exact '
          'RuntimeError crossing back as the error envelope')


# ---- tiny assertion helpers -------------------------------------------------

class _ExpectContext(object):
    """The ``with _expect_value_error(fragment)`` context: asserts the call
    raises ValueError carrying ``fragment`` and exposes the exception."""

    def __init__(self, fragment, expected_type):
        self.fragment = fragment
        self.expected_type = expected_type
        self.exception = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.exception = exc_value
        assert exc_type is not None, \
            f"expected {self.expected_type.__name__}({self.fragment!r}) but nothing was raised"
        assert exc_type is self.expected_type, \
            f"expected {self.expected_type.__name__}, got {exc_type.__name__}: {exc_value}"
        assert self.fragment in str(exc_value), \
            f"expected {self.fragment!r} in the message, got {str(exc_value)!r}"
        return True  # the exception was expected: swallow it


def _expect_value_error(fragment):
    return _ExpectContext(fragment, ValueError)


def _expect_runtime_error(fragment):
    return _ExpectContext(fragment, RuntimeError)


# ---- shared fixtures --------------------------------------------------------

# (4, 5, 6) float32 with distinct, exactly-representable values
_VOL_F32 = (np.arange(120, dtype=np.float32).reshape(4, 5, 6) / 4.0)

# a 0/1 mask on the same geometry: an L-shaped island, a 2-voxel pair along
# z and one singleton
_MASK = np.zeros((4, 5, 6), np.uint8)
_MASK[0, 1, 1] = _MASK[0, 1, 2] = _MASK[0, 2, 2] = 1
_MASK[2, 3, 4] = _MASK[3, 3, 4] = 1
_MASK[1, 0, 5] = 1

# a labels fixture (max label 9, all values < 256)
_LABELS = np.zeros((4, 5, 6), np.uint16)
_LABELS[0, 1, 1] = _LABELS[0, 1, 2] = _LABELS[0, 2, 2] = 9
_LABELS[2, 3, 4] = _LABELS[3, 3, 4] = 4
_LABELS[1, 0, 5] = 7

# a small labels fixture for the measured-properties MultiROI (labels 1..3,
# so the scalar slots hold labelCount + 1 == 4 values: row 0 is background)
_PROPS_LABELS = np.zeros((2, 3, 4), np.uint8)
_PROPS_LABELS[0, 0, 0] = 1
_PROPS_LABELS[0, 1, 1] = 2
_PROPS_LABELS[1, 0, 2] = 3

_MULTIROI_FIXTURE = []  # lazily built wrong-class object for the refusal cases


def _resolve_multiroi_fixture():
    """A MultiROI built once (and tracked) for the wrong-class refusal
    cases; built on demand so most cases stay channel-only."""
    if not _MULTIROI_FIXTURE:
        template = _channel_from(np.zeros(_LABELS.shape, np.uint16),
                                 "tp_wrongclass_template")
        _MULTIROI_FIXTURE.append(
            _multiroi_from(template, _LABELS, "tp_wrongclass_labels"))
        template.deleteObject()
    return _MULTIROI_FIXTURE[0]


CASES = (
    ("resolve: GUID/title/handle agree; unknown, empty, non-string, "
     "empty-handle, wrong-class and ambiguous refused with clear errors",
     case_bridge_source_resolution_pins_guid_title_and_handle_forms),
    ("resolve: a title carried only by another class names the carriers "
     "(the round-12 cross-class error)",
     case_cross_class_title_raises_the_carrying_classes),
    ("delete_object: title/GUID deletion, cross-class ambiguity grouping, "
     "unknown-title refusal",
     case_delete_object_removes_by_title_and_guid_and_refuses_unknown),
    ("import_volume_as_np: (Z, Y, X) independent copy, bitwise values, "
     "timestep validated",
     case_import_volume_as_np_returns_independent_z_y_x_copy),
    ("import_roi_as_np: bitwise uint8 0/1 mask",
     case_import_roi_as_np_pins_binary_uint8_readout),
    ("import_multiroi_as_np: label-count dtype routing (uint8 / uint16)",
     case_import_multiroi_as_np_pins_label_dtype_routing),
    ("publish_np_as_volume: shape/T from the array, spacing pinned, "
     "default title",
     case_publish_np_as_volume_without_reference_pins_shape_dtype_spacing),
    ("publish_np_as_volume with reference: the dtype-trap rule (array's "
     "dtype + shape, reference's spacing)",
     case_publish_np_as_volume_uint8_with_reference_pins_dtype_trap_rule),
    ("publish_np_as_volume: the dtype policy matrix",
     case_publish_np_as_volume_dtype_policy_matrix),
    ("publish_np_as_roi: bitwise mask round-trip, empty mask refused",
     case_publish_np_as_roi_pins_voxel_mask_roundtrip),
    ("publish_np_as_multiroi: BounTI recipe, label count + dtype routing, "
     "bad labels refused",
     case_publish_np_as_multiroi_pins_labels_and_label_count),
    ("a raise INSIDE publish leaves nothing behind, one handler layer up: "
     "the fresh object and the scratch channel are deleted, the exception "
     "propagates out of the handler",
     case_raise_inside_publish_leaves_nothing_behind),
    ("progress seam through the handler: start/update/cancel contract and "
     "caller ownership",
     case_progress_seam_pins_start_update_cancel_and_ownership),
    ("round 8 structural pin: bridge requests carry a DUCK progress (never "
     "True) and pump=False; handler table shape pinned",
     case_bridge_requests_carry_duck_progress_and_pump_false),
    ("measured properties: slot order, DataFrame values, subset, unknown "
     "column refused",
     case_measured_properties_dataframe_and_columns),
    ("export_measured_properties: write-then-readback, auto-padding, "
     "label-keyed dict columns, ranges, dedupe (dict + DataFrame)",
     case_export_measured_properties_write_then_readback),
    ("export_measured_properties: pre-flight refusals (lengths, names, "
     "shapes, label-key sets, classes) mutate nothing and create nothing; "
     "under wipe=True the refusals never destroy the existing properties "
     "either",
     case_export_measured_properties_preflight_refusals),
    ("export_measured_properties wipe=True: the slot set is replaced (only "
     "the new columns read back); the default write still appends and "
     "dedupes",
     case_export_measured_properties_wipe),
    ("publish_np_as_multiroi with properties: end-to-end readback; the "
     "rejected-properties failure leaves nothing behind",
     case_publish_np_as_multiroi_with_properties_end_to_end),
    ("list_objects: sorted dicts, representable filter, unknown class",
     case_list_objects_lists_sorted_dicts_and_filters_representable),
    ("PIPE LOOPBACK: real QLocalServer + real DfnbClient ship arrays, "
     "tables, handles, errors and progress frames end to end",
     case_loopback_real_service_and_client_ship_arrays_and_tables),
    ("PIPE LOOPBACK cancel: a cancel frame mid-handler aborts the handler "
     "at its checkpoint with the exact RuntimeError",
     case_loopback_cancel_frame_aborts_a_running_handler),
)


def run_all():
    """The suite loop minus sys.exit -- the single entry point the gate
    command runs. Runs every case and tears every tracked ORS object down
    in a finally (a cleanup failure must never flip the suite by itself).
    Returns True only when every case passed."""
    ok = True
    print("=== Dragonfly Notebook dfnb bridge data path (real ORS objects "
          "+ pipe loopback) ===", flush=True)
    try:
        for name, fn in CASES:
            print(f"\n[{name}]", flush=True)
            try:
                fn()
                print(f"[{name}] PASSED", flush=True)
            except Exception:
                traceback.print_exc()
                print(f"[{name}] FAILED", flush=True)
                ok = False
    finally:
        deleted = 0
        for obj in reversed(_CREATED):
            try:
                obj.deleteObject()
                deleted += 1
            except Exception as exc:  # best-effort cleanup -- a cleanup
                # failure must never flip the suite by itself
                print(f"cleanup warning: {obj} {exc}")
        print(f"deleted {deleted} of {len(_CREATED)} ORS objects")
        # no leftovers: nothing carrying a suite title survives
        leftover = []
        for managed_class in (Channel, ROI, MultiROI):
            try:
                instances = managed_class.getAllInstances()
            except Exception as exc:
                print(f"cleanup warning: {managed_class}.getAllInstances failed: {exc}")
                continue
            for instance in instances:
                try:
                    title = instance.getTitle()
                except Exception:
                    continue
                if title in _CREATED_TITLES:
                    leftover.append(title)
        if leftover:
            ok = False
            print(f"CLEANUP FAILURE: leftover suite objects: {leftover}")
    return ok


def main():
    # === FIRST: the tier-1 core suite, in-process (gate 1) ==============
    print("=== Dragonfly Notebook core suite (first, in-process) ===", flush=True)
    try:
        core_ok = bool(tier1.run_all())
    except Exception:
        traceback.print_exc()
        core_ok = False
    if not core_ok:
        # BEFORE any ORS work: failing here still exits 1 through the same
        # gate command, which therefore covers gates 1 + 2.
        print("SUITE FAILED")
        sys.exit(1)

    # === THEN: the dfnb bridge data path on real ORS objects (gate 2) ===
    ok = run_all()
    print("\n" + ("SUITE PASSED" if ok else "SUITE FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
