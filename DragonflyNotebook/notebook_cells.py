r"""Cell widgets: the code editor, its pygments highlighting, and the
output renderer.

The editor is a QPlainTextEdit subclass (the M4 drag-and-drop extends its
``dropEvent`` in place); the highlighter drives QTextSyntax highlighting
from pygments (pygments 2.19.1 is installed in Dragonfly's embedded
environment); the renderer turns nbformat-shaped outputs into the HTML the
cell's QTextBrowser shows.

Highlighting note: QSyntaxHighlighter works block by block, but Python
state (an unterminated triple-quoted string, a backslash continuation)
crosses lines -- so the WHOLE cell text is re-lexed on every change into a
``blockNumber -> [(column, length, format)]`` map and ``rehighlight()``
paints it block by block. Notebook cells are small; a pygments pass is
milliseconds.
"""

import os

# Same multiple-OpenMP-runtime workaround as every module in this project:
# this module itself imports no numpy, but the convention is uniform and
# the guard costs nothing.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import ast
import base64
import html
import math

from PyQt6.QtCore import QEvent, QObject, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QPainter,
                         QSyntaxHighlighter, QTextCharFormat)
from PyQt6.QtWidgets import QPlainTextEdit, QWidget
from PyQt6.QtXml import QDomDocument

import pygments.lexers
from pygments.token import Token

from .dragonflynotebook_core import SNIPPET_MIME
from .notebook_drop_policy import ipynb_drop_decision


def code_font():
    """The system fixed-width font (the console look for cell editors and
    outputs)."""
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


class _LineNumberArea(QWidget):
    """The gutter of one cell editor: the standard Qt line-number pattern
    (the 'Code Editor' example) -- a plain child widget the editor paints
    right-aligned digits into and reserves viewport margin for."""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor._gutter_width(), 0)

    def paintEvent(self, event):
        self._editor._paint_gutter(event)


class NotebookCellEditor(QPlainTextEdit):
    """One cell's editor.

    Ctrl+Enter runs the cell, Shift+Enter runs it and advances (Jupyter's
    muscle memory), Alt+Enter runs it and inserts a new cell below; the
    form owns what those mean. ``focusGained`` keeps the form's current
    cell tracking honest -- the toolbar's move/delete/run actions always
    act on the cell that has the keyboard, not on a stale index.
    """

    runRequested = pyqtSignal()
    runAdvanceRequested = pyqtSignal()
    runInsertRequested = pyqtSignal()
    focusGained = pyqtSignal()
    # A dfnb snippet drag landed on THIS editor. Round 8: the editor only
    # announces the drop -- the form owns the bridge client the picker
    # dialogs read object lists through, so IT runs the picker and inserts
    # the generated line at this editor's cursor (the drop target, not the
    # focused cell).
    snippetDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('cell_editor')
        self.setFont(code_font())
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setTabChangesFocus(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        # Round 9: a cell NEVER scrolls internally -- the notebook's scroll
        # area is the one scrollbar (the whole-notebook panel scrolls). Both
        # policies are pinned AlwaysOff here: the vertical default
        # 'AsNeeded' is what flashed an OS scrollbar over the dark cell
        # (and would clip-fight resizeToContent), and with the
        # grow-with-content sizing below an internal scrollbar is dead
        # weight anyway.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Scoped to this objectName only: child dialogs inherit ancestor
        # stylesheets, so a bare type selector here would restyle them.
        # Round 9 adds the text color: the application process sets no
        # global palette/QSS (the round-7 in-process form inherited ORS's
        # app-global stylesheet for free), so an unstyled editor rendered
        # the palette's black text on the dark background -- unreadable.
        # rgb(230, 230, 230) is ORS's base text color
        # (resource/stylesheet/main.txt, the QWidget rule).
        self.setStyleSheet(
            'QPlainTextEdit#cell_editor { background: #1E1E1E; '
            'color: rgb(230, 230, 230); '
            'border: none; selection-background-color: #264F78; }')
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_viewport)
        self._update_gutter_width(self.blockCount())

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if modifiers == Qt.KeyboardModifier.ControlModifier:
                self.runRequested.emit()
                return
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self.runAdvanceRequested.emit()
                return
            if modifiers == Qt.KeyboardModifier.AltModifier:
                self.runInsertRequested.emit()
                return
        super().keyPressEvent(event)

    # ---- drag and drop ----------------------------------------------------

    def _is_notebook_file_drag(self, mime):
        """True when the drag is the shared drop policy's 'consume' case:
        ONLY local .ipynb file URLs. That drag belongs to the notebook
        WINDOW (dropping a notebook onto the app opens it there); this
        editor ignores it so it propagates up the parent chain. Probed
        round 9 (offscreen): the base QPlainTextEdit claims the drag and
        its drop INSERTS the URL as text (Qt synthesizes text/plain from
        the URL list), so without this ignore a dropped .ipynb would land
        as garbage characters inside a cell."""
        return ipynb_drop_decision(mime) == 'consume'

    def dragEnterEvent(self, event):
        """Accepts the dfnb snippet drag and ORS object drags (the console's
        mime pair, ``text/orsxml`` / ``text/xml``); a notebook-file drag
        (.ipynb URLs only) is IGNORED so the window above this editor gets
        it; everything else goes to the plain-text drop path."""
        mime = event.mimeData()
        if self._is_notebook_file_drag(mime):
            event.ignore()
            return
        if (mime.hasFormat(SNIPPET_MIME) or mime.hasFormat('text/xml')
                or mime.hasFormat('text/orsxml')):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Keeps the claimed drags alive (snippets, ORS objects, plain
        text); a notebook-file drag is released here too -- the enter/
        move/drop triple must agree or the drop never reaches the
        window."""
        if self._is_notebook_file_drag(event.mimeData()):
            event.ignore()
            return
        event.accept()

    def dropEvent(self, event):
        """A dfnb snippet drag opens its picker dialog and inserts the
        generated ``dfnb.<function>(...)`` line at the cursor (nothing is
        inserted when the dialog is cancelled); an ORS object drag inserts
        ``orsObj('<guid>')`` (the console's payload shape); a notebook-file
        drag is IGNORED (it belongs to the window); anything else falls
        through to the normal plain-text drop."""
        mime = event.mimeData()
        if self._is_notebook_file_drag(mime):
            event.ignore()
            return
        if mime.hasFormat(SNIPPET_MIME):
            event.acceptProposedAction()
            self._drop_snippet(
                bytes(mime.data(SNIPPET_MIME)).decode('utf-8', 'replace'))
            return
        if mime.hasFormat('text/xml') or mime.hasFormat('text/orsxml'):
            encoded = mime.data('text/xml')
            if len(encoded) == 0:
                encoded = mime.data('text/orsxml')
            guids = guids_from_ors_xml(bytes(encoded))
            if not guids:
                event.ignore()  # an ORS-flavoured drag without guids
                return
            event.acceptProposedAction()
            if len(guids) == 1:
                self.insert_at_cursor("orsObj('{}')".format(guids[0]))
            else:
                self.insert_at_cursor(
                    '[' + ', '.join("orsObj('{}')".format(guid)
                                    for guid in guids) + ']')
            return
        super().dropEvent(event)

    def _drop_snippet(self, kind):
        # Round 8: the picker dialogs moved into the form module (they read
        # their object lists THROUGH the bridge client, which the form
        # owns) and the functions dock that originated most snippet drags
        # is gone. This editor only announces the drop; the form's handler
        # runs the picker and inserts the generated line at THIS editor's
        # cursor -- the round-4 drop-target semantics, kept.
        self.snippetDropped.emit(kind)

    def insert_at_cursor(self, text):
        """Drops text at the cursor (snippets and ORS object references
        land here) and keeps the focus."""
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.setFocus()

    def focusInEvent(self, event):
        self.focusGained.emit()
        super().focusInEvent(event)

    def resizeToContent(self):
        """Grows the editor with its text: a cell never scrolls internally
        (the notebook's scroll area scrolls, not the cell).

        Round 9 hardening -- with BOTH scrollbar policies AlwaysOff an
        underestimated height silently CLIPS the cell's last lines, so the
        height is the LARGEST of two independent estimates:

          1. the block-based wrap estimate: the wrapped visual lines are
             estimated per block from the font metrics against the
             viewport width (clamped to >= 80 px pre-layout) and carried
             at the font's line spacing -- this one LEADS on a fresh
             edit, while the layout still holds the pre-edit geometry;
          2. the document layout's own visual-line count,
             ``documentLayout().documentSize().height()``. Probed round
             9 (40 short lines -> 40.0; 20 long wrapped lines -> 360.0,
             matching QPlainTextEdit's own scrollbar arithmetic,
             maximum 20 + pageStep 340): the count is in VISUAL LINES,
             not pixels, so it is converted here with the font's line
             spacing plus the document margin. Once the layout has caught
             up it is the widget's own authority -- it accounts for tabs,
             real glyph advances and every wrap the metrics estimate can
             only approximate, and in the probe it EXCEEDED the metrics
             estimate by 19 visual lines on hard-wrapped text (the exact
             clip this hardening closes).

        Both carry the contents margins, the document margin and a small
        allowance; the floor stays 52 px (one empty line plus padding).
        The four callers (documentSizeChanged, text/size changes,
        resizeEvent, the startup singleShot -- mainform) converge the
        height within one or two event-loop turns."""
        metrics = self.fontMetrics()
        viewport_width = self.viewport().width()
        if viewport_width < 80:
            viewport_width = 80
        lines = 0
        block = self.document().firstBlock()
        while block.isValid():
            advance = metrics.horizontalAdvance(block.text())
            lines += max(1, (advance + viewport_width - 1) // viewport_width)
            block = block.next()
        if lines < 1:
            lines = 1
        margins = self.contentsMargins()
        extra = 8
        # the layout's own count carries the document margin on both sides;
        # the wrap estimate does not, so both add it explicitly here
        document_margin = 2 * int(self.document().documentMargin() or 0)
        vertical = margins.top() + margins.bottom()
        height_estimate = (int(lines * metrics.lineSpacing())
                           + document_margin + vertical + extra)
        # estimate 2: the layout reports VISUAL LINES -- scale to pixels
        # with the line spacing and add the same margins/allowance
        layout_lines = self.document().documentLayout().documentSize().height()
        layout_estimate = (int(math.ceil(layout_lines * metrics.lineSpacing()))
                           + document_margin + vertical + extra)
        self.setFixedHeight(max(52, height_estimate, layout_estimate))

    # ---- line-number gutter -----------------------------------------------

    def _gutter_width(self):
        """The gutter's pixel width: room for the widest line number plus
        padding, recomputed whenever the block count gains a digit."""
        digits = max(1, len(str(max(1, self.blockCount()))))
        return 8 + self.fontMetrics().horizontalAdvance('9') * digits

    def _update_gutter_width(self, _block_count=0):
        self.setViewportMargins(self._gutter_width(), 0, 0, 0)

    def _update_gutter_viewport(self, rect, dy):
        """``updateRequest``: scrolls the gutter with the viewport, or
        repaints just the scrolled region (the standard Qt pattern)."""
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(),
                                rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def _paint_gutter(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor('#1E1E1E'))
        painter.setFont(self.font())
        # Round 9: raised from #6A6A6A -- the same low-contrast complaint
        # as the timing labels and status bar; clearly legible, still one
        # step dimmer than the code text.
        painter.setPen(QColor('#8A8A8A'))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._gutter.width() - 4,
                                 self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight,
                                 str(block_number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        contents = self.contentsRect()
        self._gutter.setGeometry(contents.left(), contents.top(),
                                 self._gutter_width(), contents.height())
        self.resizeToContent()


class _RenderedMarkdownKeyFilter(QObject):
    """Eats the run chords on a RENDERED markdown cell's browser (round
    12): Key_Enter/Key_Return presses carrying Control, Shift or Alt are
    CONSUMED -- returned True, never dispatched, never passed to the
    watched widget -- so a stray chord while the rendered view holds the
    keyboard does nothing at all (no run, no advance, no insert; the only
    way back to a runnable cell is the form's ✎ Edit flip). Every other
    event passes through untouched. The form installs the filter on the
    markdown cells' browsers only (code/raw cells keep today's behavior);
    it is parented to the browser, so a rebuild's deleteLater takes the
    filter down with the widget it watches.

    Round 13: Shift+Enter is the ONE chord that acts on a rendered cell
    -- the filter emits ``skipForward`` (the chord is still consumed; the
    browser never sees the key) and the FORM advances to the next
    editable cell, skipping rendered markdowns; Ctrl/Alt+Enter remain
    silent no-ops."""

    skipForward = pyqtSignal()

    _RUN_CHORD_MODIFIERS = (Qt.KeyboardModifier.ControlModifier
                            | Qt.KeyboardModifier.ShiftModifier
                            | Qt.KeyboardModifier.AltModifier)

    def eventFilter(self, obj, event):  # noqa: ARG002 -- Qt's filter signature
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                event.modifiers() & self._RUN_CHORD_MODIFIERS):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.skipForward.emit()
            return True
        return super().eventFilter(obj, event)


def guids_from_ors_xml(payload):
    """The GUIDs in an ORS object drag payload -- the Python console's
    extraction (``mainform_python_console.py`` ``dropEvent``): the
    'guids' XML element holds a repr'd list, read with ``ast.literal_eval``
    instead of the console's ``eval``."""
    document = QDomDocument()
    if not document.setContent(payload):
        return []
    nodes = document.elementsByTagName('guids')
    if nodes.length() == 0:
        return []
    text = nodes.at(0).toElement().text()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    if isinstance(value, (list, tuple)):
        return [str(guid) for guid in value if guid]
    if isinstance(value, str) and value:
        return [value]
    return []


class PythonCellHighlighter(QSyntaxHighlighter):
    """Pygments-driven syntax highlighting for one cell editor."""

    # (pygments token, color, bold, italic) -- a dark-background palette
    # (the app theme is dark-only); first match on the token's parent chain
    # wins.
    # Round 9 adds the three families the round-8 palette left unstyled:
    # unstyled tokens render the application palette's BLACK text on the
    # dark editor -- probed over numpy snippets, Token.Name (identifiers),
    # Token.Name.Namespace and above all Token.Punctuation (the brackets,
    # parens and commas -- the 'worst inside bracketed/parenthesized code
    # segments' of the user's contrast report) were exactly that. The
    # parent-chain resolution means Token.Name also covers Name.Namespace,
    # Name.Attribute, Name.Exception, ...; Token.Text covers the
    # whitespace tokens (no visible glyphs, styled for completeness).
    # #ABB2BF is the palette's own base foreground (the Operator color --
    # One Dark's default text), keeping plain code one uniform light tone
    # under the keyword/string/number accents.
    _TOKEN_STYLES = (
        (Token.Keyword, '#C678DD', True, False),
        (Token.String, '#98C379', False, False),
        (Token.String.Doc, '#98C379', False, True),
        (Token.Comment, '#5C6370', False, True),
        (Token.Number, '#D19A66', False, False),
        (Token.Operator, '#ABB2BF', False, False),
        (Token.Name, '#ABB2BF', False, False),
        (Token.Name.Builtin, '#56B6C2', False, False),
        (Token.Name.Function, '#61AFEF', True, False),
        (Token.Name.Class, '#E5C07B', True, False),
        (Token.Name.Decorator, '#E5C07B', False, False),
        (Token.Punctuation, '#ABB2BF', False, False),
        (Token.Text, '#ABB2BF', False, False),
        (Token.Error, '#F14C4C', False, True),
    )

    def __init__(self, document):
        super().__init__(document)
        self._lexer = pygments.lexers.PythonLexer()
        self._formats = {}
        self._format_cache = {}
        for token_type, color, bold, italic in self._TOKEN_STYLES:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            if italic:
                fmt.setFontItalic(True)
            self._formats[token_type] = fmt
        self._spans = {}
        self._pending_text = None
        self._repaint_scheduled = False
        # Round 9: True while _relex_now's rehighlight() runs. PyQt6 6.7.1
        # emits QPlainTextEdit.textChanged from a bare rehighlight() --
        # probed, even for an empty document with no spans to apply -- so
        # the form must be able to tell a relex-induced signal (no text
        # change: no fresh relex, no dirty flag) from a real edit.
        self._rehighlighting = False

    def is_rehighlighting(self):
        """True while the deferred relex's ``rehighlight()`` is running --
        the ``textChanged`` emitted in that window carries NO text change."""
        return self._rehighlighting

    def _format_for(self, token_type):
        fmt = self._format_cache.get(token_type)
        if fmt is not None or token_type in self._format_cache:
            return fmt
        walk = token_type
        resolved = None
        while walk is not None:
            resolved = self._formats.get(walk)
            if resolved is not None:
                break
            walk = walk.parent
        self._format_cache[token_type] = resolved
        return resolved

    def relex(self, text):
        """Re-lexes the whole cell text into the per-block span map.

        The repaint is DEFERRED to the next event-loop turn (coalesced):
        ``rehighlight()`` inside the textChanged signal would re-enter the
        document layout mid-mutation, which crashes Qt natively with the
        plain-text layout. The spans computed here are pure Python and
        immediately current; only the paint waits.

        Round 9: the deferred turn itself must also run guarded -- a bare
        ``rehighlight()`` emits ``textChanged`` in PyQt6 6.7.1, and an
        unguarded one re-scheduled itself from its own signal (an endless
        single-shot chain that also flagged the document dirty on launch);
        ``_on_editor_text_changed`` checks ``is_rehighlighting()`` and
        ignores the signal while this runs."""
        self._pending_text = text
        if self._repaint_scheduled:
            return
        self._repaint_scheduled = True
        QTimer.singleShot(0, self._relex_now)

    def _relex_now(self):
        self._repaint_scheduled = False
        spans = {}
        text = self._pending_text or ''
        block_index = 0
        line_start = 0
        position = 0
        for token_type, value in self._lexer.get_tokens(text):
            if not value:
                continue
            segments = value.split('\n')
            last_segment = len(segments) - 1
            for segment_index, segment in enumerate(segments):
                if segment_index > 0:
                    block_index += 1
                    line_start = position
                if segment:
                    fmt = self._format_for(token_type)
                    if fmt is not None:
                        spans.setdefault(block_index, []).append(
                            (position - line_start, len(segment), fmt))
                position += len(segment)
                if segment_index < last_segment:
                    position += 1  # the '\n' itself
        self._pending_text = None
        self._spans = spans
        # guarded: rehighlight() emits textChanged on PyQt6 6.7.1 (probed,
        # even for an empty document) -- see the relex() docstring
        self._rehighlighting = True
        try:
            self.rehighlight()
        finally:
            self._rehighlighting = False

    def highlightBlock(self, text):  # noqa: ARG002 -- Qt calls it per block
        spans = self._spans.get(self.currentBlock().blockNumber())
        if spans:
            for start, length, fmt in spans:
                self.setFormat(start, length, fmt)


# ---------------------------------------------------------------------------
# outputs -> HTML (the QTextBrowser rendering)
# ---------------------------------------------------------------------------

_ERROR_COLOR = '#F14C4C'

_CODE_STYLE = ('white-space: pre-wrap; font-family: monospace; '
               'font-size: 9pt; margin: 2px 0px;')


def _wrap_code(text, color):
    return '<div style="{}; color: {};">{}</div>'.format(
        _CODE_STYLE, color, html.escape(text or ''))


def _embed_png(payload):
    """A data URL for an image/png payload (base64 text; whitespace inside
    a base64 payload is tolerated by decoders but not by data URLs, so it
    is stripped)."""
    payload = ''.join(str(payload).split())
    try:
        base64.b64decode(payload, validate=False)
    except Exception:
        return None  # a corrupt payload must not break the whole rendering
    return 'data:image/png;base64,' + payload


def render_stream_chunk(name, text):
    """The HTML for ONE live stream chunk (the kernel engine forwards
    stdout/stderr chunks while the cell runs; the authoritative final render
    comes from the stored outputs)."""
    color = _ERROR_COLOR if name == 'stderr' else '#D4D4D4'
    return _wrap_code(text, color)


def render_outputs_html(outputs):
    """The HTML for one cell's stored outputs (nbformat-shaped dicts).

    Every text payload is HTML-escaped; a figure (image/png) embeds as a
    data URL and suppresses its '<Figure ...>' text/plain twin;
    ``execute_result`` renders its text/plain (text/html payloads from
    foreign notebooks are intentionally NOT rendered -- escaping unknown
    HTML beats a broken/injected page).
    """
    parts = []
    for out in outputs or []:
        output_type = out.get('output_type')
        if output_type == 'stream':
            color = _ERROR_COLOR if out.get('name') == 'stderr' else '#D4D4D4'
            parts.append(_wrap_code(out.get('text', ''), color))
        elif output_type in ('display_data', 'execute_result'):
            data = out.get('data') or {}
            if 'image/png' in data:
                url = _embed_png(data['image/png'])
                if url is not None:
                    parts.append('<img src="{}">'.format(url))
                    continue
            text = data.get('text/plain')
            if text:
                parts.append(_wrap_code(text, '#D4D4D4'))
        elif output_type == 'error':
            traceback_lines = out.get('traceback') or []
            if isinstance(traceback_lines, str):
                traceback_lines = [traceback_lines]
            body = '{}: {}'.format(out.get('ename', 'Error'),
                                   out.get('evalue', ''))
            if traceback_lines:
                body += '\n' + '\n'.join(str(line) for line in traceback_lines)
            parts.append(_wrap_code(body, _ERROR_COLOR))
    return ''.join(parts)
