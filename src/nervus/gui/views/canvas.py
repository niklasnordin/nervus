"""The diagram canvas: a scene that mirrors the DiagramModel, and a zoomable view.

The scene never changes the model itself. User gestures (dragging blocks, drawing wires, dropping blocks from
the library) are turned into calls on the :class:`EditorController`; the model then emits signals that update
the scene.
"""

from __future__ import annotations

import contextlib
from typing import Any

from PySide6.QtCore import QMimeData, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QNativeGestureEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSceneContextMenuEvent,
    QGraphicsSceneDragDropEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QWidget,
)

from nervus.core import Block, Connection, Link
from nervus.gui.controllers.editor_controller import EditorController
from nervus.gui.models.diagram_model import DiagramModel, block_position
from nervus.gui.theme import ThemeManager, current_theme
from nervus.gui.views.items import (
    GRID,
    BlockItem,
    LinkItem,
    PhysicalPortItem,
    PortItem,
    WireItem,
    block_width,
    domain_color,
    route,
    snap,
)
from nervus.gui.views.routing import Point, Rect

BLOCK_MIME = "application/x-nervus-block-type"


def block_mime(type_id: str) -> QMimeData:
    mime = QMimeData()
    mime.setData(BLOCK_MIME, type_id.encode())
    mime.setText(type_id)
    return mime


class DiagramScene(QGraphicsScene):
    selectedBlocksChanged = Signal(list)  # list[Block]
    blockActivated = Signal(object)  # Block (double-click)
    contextMenuRequested = Signal(object, object)  # Block | Connection | None, QPoint (screen position)

    def __init__(self, model: DiagramModel, controller: EditorController, parent: Any = None) -> None:
        super().__init__(parent)
        self.model = model
        self.controller = controller
        model.width_of = block_width  # the model places new blocks with the widths they are drawn with
        self.blocks: dict[int, BlockItem] = {}
        self.wires: dict[int, WireItem] = {}
        self.links: dict[int, LinkItem] = {}
        self._link_from: PhysicalPortItem | None = None
        self._temp_wire: QGraphicsPathItem | None = None
        self._wire_from: PortItem | None = None
        self._press_positions: dict[int, QPointF] = {}
        self._resizing: tuple[BlockItem, list[float] | None] | None = None  # item, size before the drag
        # Wire segment drag: (wires with their corners before the drag and the dragged segment), press position
        self._segment_drag: tuple[list[tuple[WireItem, list[Point], int]], QPointF] | None = None
        self.setSceneRect(QRectF(-5000, -5000, 10000, 10000))

        model.blockAdded.connect(self._on_block_added)
        model.blockRemoved.connect(self._on_block_removed)
        model.blockMoved.connect(self._on_block_moved)
        model.blockRenamed.connect(lambda b, _old: self._item(b) and self._item(b).update())  # type: ignore[union-attr]
        model.paramsChanged.connect(self._on_params_changed)
        model.connectionAdded.connect(self._on_connection_added)
        model.connectionRemoved.connect(self._on_connection_removed)
        model.connectionChanged.connect(self._on_connection_changed)
        model.linkAdded.connect(self._on_link_added)
        model.linkRemoved.connect(self._on_link_removed)
        model.diagramReset.connect(self.rebuild)
        model.currentChanged.connect(self._on_current_changed)
        controller.editingEnabledChanged.connect(self._on_editing_enabled)
        self.selectionChanged.connect(self._emit_selection)
        ThemeManager.instance().themeChanged.connect(self._on_theme)
        self.rebuild()

    def _on_theme(self, _theme: object) -> None:
        self.update()

    def _on_current_changed(self, _diagram: object) -> None:
        self.rebuild()

    # ------------------------------------------------------------------ model -> scene

    def rebuild(self) -> None:
        self.clear()
        self.blocks.clear()
        self.wires.clear()
        self.links.clear()
        self._link_from = None
        self._temp_wire = None
        for b in self.model.diagram.blocks:
            self._on_block_added(b)
        for c in self.model.diagram.connections:
            self._on_connection_added(c)
        for link in self.model.diagram.links:
            self._on_link_added(link)

    def _item(self, block: Block) -> BlockItem | None:
        return self.blocks.get(id(block))

    def _on_block_added(self, block: Block) -> None:
        if block.diagram is not self.model.diagram:
            return
        item = BlockItem(block)
        item.setPos(block_position(block))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self.controller.editing_enabled)
        self.addItem(item)
        self.blocks[id(block)] = item

    def _on_block_removed(self, block: Block) -> None:
        item = self.blocks.pop(id(block), None)
        if item is not None:
            self.removeItem(item)

    def _on_block_moved(self, block: Block) -> None:
        item = self._item(block)
        if item is not None and item.pos() != block_position(block):
            item.setPos(block_position(block))

    def _on_params_changed(self, block: Block) -> None:
        item = self._item(block)
        if item is None:
            return
        item.refresh()
        self._refresh_links_of(block)
        for w in self.wires.values():
            c = w.connection
            if c.src is block:
                port = item.port(False, c.src_port)
                if port:
                    w.src = port
            if c.dst is block:
                port = item.port(True, c.dst_port)
                if port:
                    w.dst = port
            w.update_path()

    def _on_connection_added(self, conn: Connection) -> None:
        if conn.dst.diagram is not self.model.diagram:
            return
        src_item, dst_item = self._item(conn.src), self._item(conn.dst)
        if src_item is None or dst_item is None:
            return
        sp, dp = src_item.port(False, conn.src_port), dst_item.port(True, conn.dst_port)
        if sp is None or dp is None:
            return
        wire = WireItem(conn, sp, dp)
        self.addItem(wire)
        wire.update_path()  # now with the blocks of the scene as obstacles
        self.wires[id(conn)] = wire
        self._refresh_fanout(conn)

    def _on_connection_removed(self, conn: Connection) -> None:
        wire = self.wires.pop(id(conn), None)
        if wire is not None:
            self.removeItem(wire)
        self._refresh_fanout(conn)

    def _refresh_fanout(self, conn: Connection) -> None:
        for w in self.wires.values():
            if w.connection.src is conn.src and w.connection.src_port == conn.src_port:
                w.update()

    def fanout(self, conn: Connection) -> int:
        return sum(
            1 for w in self.wires.values() if w.connection.src is conn.src and w.connection.src_port == conn.src_port
        )

    def block_item_moved(self, item: BlockItem) -> None:
        for w in self.wires.values():
            if w.connection.src is item.block or w.connection.dst is item.block:
                w.update_path()
        for k in self.links.values():
            if k.link.a is item.block or k.link.b is item.block:
                k.update_path()

    # ------------------------------------------------------------------ physical links

    def _on_link_added(self, link: Link) -> None:
        if link.a.diagram is not self.model.diagram:
            return
        ia, ib = self._item(link.a), self._item(link.b)
        pa = ia.physical_port(link.a_port) if ia else None
        pb = ib.physical_port(link.b_port) if ib else None
        if pa is None or pb is None:
            return
        item = LinkItem(link, pa, pb)
        self.addItem(item)
        item.update_path()
        self.links[id(link)] = item

    def _on_link_removed(self, link: Link) -> None:
        item = self.links.pop(id(link), None)
        if item is not None:
            self.removeItem(item)

    def _refresh_links_of(self, block: Block) -> None:
        item = self._item(block)
        for k in self.links.values():
            if item is None:
                continue
            if k.link.a is block:
                k.a = item.physical_port(k.link.a_port) or k.a
            if k.link.b is block:
                k.b = item.physical_port(k.link.b_port) or k.b
            k.update_path()

    def _physical_port_at(self, pos: QPointF) -> PhysicalPortItem | None:
        for item in self.items(pos):
            if isinstance(item, PhysicalPortItem):
                return item
        return None

    def selected_links(self) -> list[Link]:
        return [i.link for i in self.selectedItems() if isinstance(i, LinkItem)]

    def _on_editing_enabled(self, enabled: bool) -> None:
        for item in self.blocks.values():
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, enabled)

    # ------------------------------------------------------------------ selection / highlighting

    def selected_blocks(self) -> list[Block]:
        return [i.block for i in self.selectedItems() if isinstance(i, BlockItem)]

    def selected_connections(self) -> list[Connection]:
        return [i.connection for i in self.selectedItems() if isinstance(i, WireItem)]

    def _emit_selection(self) -> None:
        with contextlib.suppress(RuntimeError):  # scene being destroyed
            self.selectedBlocksChanged.emit(self.selected_blocks())

    def select_blocks(self, names: list[str] | tuple[str, ...]) -> None:
        """Select blocks of the shown diagram by name or by full path."""
        self.clearSelection()
        for item in self.blocks.values():
            if item.block.name in names or self.model.path_of(item.block) in names:
                item.setSelected(True)

    def set_type_labels(
        self,
        labels: dict[tuple[str, int], str],
        wide: set[tuple[str, int]] | None = None,
        rate_colors: dict[str, QColor] | None = None,
    ) -> None:
        """Annotate wires and blocks: ``labels[(source block path, port)] = "int16 [3]"``; ``wide``: vector
        signals (thicker lines); ``rate_colors[block path]``: sample-time colours of blocks and their wires."""
        wide, rate_colors = wide or set(), rate_colors or {}
        for w in self.wires.values():
            key = (self.model.path_of(w.connection.src), w.connection.src_port)
            text = labels.get(key, "")
            color = rate_colors.get(key[0])
            if text != w.type_label or (key in wide) != w.wide or color != w.rate_color:
                w.prepareGeometryChange()
                w.type_label, w.wide, w.rate_color = text, key in wide, color
                w.update()
        for item in self.blocks.values():
            color = rate_colors.get(self.model.path_of(item.block))
            if color != item.rate_color:
                item.rate_color = color
                item.update()

    def mark_errors(self, names: list[str] | tuple[str, ...]) -> None:
        """Outline blocks with errors (full paths). A subsystem is outlined if a block inside it has an error."""
        for item in self.blocks.values():
            path = self.model.path_of(item.block)
            err = any(n == path or n.startswith(path + "/") for n in names)
            if item.error != err:
                item.error = err
                item.update()

    def items_rect(self) -> QRectF:
        rect = QRectF()
        for item in self.blocks.values():
            rect = rect.united(item.sceneBoundingRect())
        return rect

    # ------------------------------------------------------------------ mouse: wires and moves

    def _port_at(self, pos: QPointF) -> PortItem | None:
        for item in self.items(pos):
            if isinstance(item, PortItem):
                return item
        return None

    def _block_at(self, pos: QPointF) -> BlockItem | None:
        for item in self.items(pos):
            if isinstance(item, BlockItem):
                return item
        return None

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.controller.editing_enabled:
            grip = self._block_at(event.scenePos())
            if (
                grip is not None
                and grip.isSelected()
                and grip.resize_handle().contains(grip.mapFromScene(event.scenePos()))
            ):
                size = grip.block.meta.get("size")
                self._resizing = (grip, list(size) if isinstance(size, list | tuple) else None)
                event.accept()
                return
            phys = self._physical_port_at(event.scenePos())
            if phys is not None:
                self._link_from = phys
                self._temp_wire = QGraphicsPathItem()
                self._temp_wire.setPen(QPen(domain_color(phys.domain), 1.5, Qt.PenStyle.DashLine))
                self._temp_wire.setZValue(3)
                self.addItem(self._temp_wire)
                event.accept()
                return
            port = self._port_at(event.scenePos())
            wire = self._wire_at(event.scenePos()) if port is None else None
            if wire is not None:
                k = wire.segment_at(event.scenePos())
                if k is not None:
                    self.clearSelection()
                    wire.setSelected(True)
                    self._segment_drag = (self._segment_group(wire, k), event.scenePos())
                    event.accept()
                    return
            if port is not None:
                self._wire_from = port
                self._temp_wire = QGraphicsPathItem()
                t = current_theme()
                self._temp_wire.setPen(QPen(t.color("accent"), 1.5, Qt.PenStyle.DashLine))
                self._temp_wire.setZValue(3)
                self.addItem(self._temp_wire)
                event.accept()
                return
        super().mousePressEvent(event)
        self._press_positions = {id(i.block): i.pos() for i in self.selectedItems() if isinstance(i, BlockItem)}

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._segment_drag is not None:
            group, pressed_at = self._segment_drag
            d = event.scenePos() - pressed_at
            for w, pts, k in group:
                w.setPath(_path(_moved(pts, k, snap(d.x()), snap(d.y()))))
            event.accept()
            return
        if self._resizing is not None:
            item = self._resizing[0]
            corner = item.mapFromScene(event.scenePos())
            item.preview_size = [snap(max(corner.x(), 10.0)), snap(max(corner.y(), 10.0))]
            item.refresh()
            self._update_wires_of(item)
            event.accept()
            return
        if self._temp_wire is not None and self._link_from is not None:
            self._temp_wire.setPath(route(self._link_from.scene_anchor(), event.scenePos(), self._link_from.outward, 1))
            event.accept()
            return
        if self._temp_wire is not None and self._wire_from is not None:
            a = self._wire_from.scene_anchor()
            b = event.scenePos()
            way = self._wire_from.direction
            path = route(a, b, way, 1) if not self._wire_from.is_input else route(b, a, 1, way)
            self._temp_wire.setPath(path)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._segment_drag is not None:
            group, pressed_at = self._segment_drag
            self._segment_drag = None
            d = event.scenePos() - pressed_at
            changes = []
            for w, pts, k in group:
                moved = _moved(pts, k, snap(d.x()), snap(d.y()))
                changes.append((w.connection, w.connection.meta.get("points"), [list(p) for p in moved[1:-1]]))
            if not (snap(d.x()) or snap(d.y())) or not self.controller.set_wire_points(changes):
                for w, _pts, _k in group:
                    w.update_path()
            event.accept()
            return
        if self._resizing is not None:
            item, old = self._resizing
            self._resizing = None
            new = [item.size[0], item.size[1]]
            item.preview_size = None  # the model keeps the old size unless the controller accepts the new one
            self.controller.resize_block(item.block, old, new)
            item.refresh()
            self._update_wires_of(item)
            event.accept()
            return
        if self._temp_wire is not None and self._link_from is not None:
            self.removeItem(self._temp_wire)
            self._temp_wire = None
            start_port, self._link_from = self._link_from, None
            target = self._physical_port_at(event.scenePos())
            if target is not None and target is not start_port:
                self.controller.link_ports(start_port.block, start_port.name, target.block, target.name)
            event.accept()
            return
        if self._temp_wire is not None and self._wire_from is not None:
            self.removeItem(self._temp_wire)
            self._temp_wire = None
            start, self._wire_from = self._wire_from, None
            self._finish_wire(start, event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)
        moves = []
        for i in self.selectedItems():
            if isinstance(i, BlockItem):
                was = self._press_positions.get(id(i.block))
                if was is not None and was != i.pos():
                    moves.append((i.block, was, i.pos()))
        self._press_positions = {}
        if moves:
            self.controller.move_blocks(moves)
            self.reroute_all()

    def _wire_at(self, pos: QPointF) -> WireItem | None:
        for item in self.items(pos):
            if isinstance(item, WireItem):
                return item
        return None

    def _segment_group(self, wire: WireItem, k: int) -> list[tuple[WireItem, list[Point], int]]:
        """The dragged segment of ``wire``, and the same segment of other wires from the same output (the
        shared trunk of a branch), so a branch point moves as one."""
        pts = wire.corners()
        seg = (pts[k], pts[k + 1])
        group = [(wire, pts, k)]
        c = wire.connection
        for other in self.wires.values():
            oc = other.connection
            if other is wire or oc.src is not c.src or oc.src_port != c.src_port:
                continue
            opts = other.corners()
            for j in range(1, len(opts) - 2):
                if _same_line(seg, (opts[j], opts[j + 1])):
                    group.append((other, opts, j))
                    break
        return group

    def obstacles_near(self, a: QPointF, b: QPointF, margin: float = 200.0) -> list[Rect]:
        """Block rectangles near a wire from ``a`` to ``b``, for routing around them."""
        left, right = min(a.x(), b.x()) - margin, max(a.x(), b.x()) + margin
        top, bottom = min(a.y(), b.y()) - margin, max(a.y(), b.y()) + margin
        out = []
        for item in self.blocks.values():
            r = item.mapRectToScene(item.body_rect())
            if r.right() >= left and r.left() <= right and r.bottom() >= top and r.top() <= bottom:
                out.append((r.left(), r.top(), r.right(), r.bottom()))
        return out

    def _on_connection_changed(self, conn: Connection) -> None:
        wire = self.wires.get(id(conn))
        if wire is not None:
            wire.update_path()

    def reroute_all(self) -> None:
        """Route every wire again (after blocks moved, a route that was clear may now pass through a block)."""
        for w in self.wires.values():
            w.update_path()
        for k in self.links.values():
            k.update_path()

    def _update_wires_of(self, item: BlockItem) -> None:
        for w in self.wires.values():
            if w.src.block_item is item or w.dst.block_item is item:
                w.update_path()
        self._refresh_links_of(item.block)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Arrow keys nudge the selected blocks by one grid step (Shift: five)."""
        steps = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        selected = [i for i in self.selectedItems() if isinstance(i, BlockItem)]
        if event.key() in steps and selected and self.controller.editing_enabled and self.focusItem() is None:
            dx, dy = steps[Qt.Key(event.key())]
            k = GRID * (5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
            self.controller.move_blocks([(i.block, i.pos(), i.pos() + QPointF(dx * k, dy * k)) for i in selected])
            event.accept()
            return
        super().keyPressEvent(event)

    def _finish_wire(self, start: PortItem, pos: QPointF) -> None:
        target = self._port_at(pos)
        if target is None:
            # Dropped on a block body: use its first free port of the right kind.
            block_item = self._block_at(pos)
            if block_item is not None and block_item is not start.block_item:
                target = self._free_port(block_item, want_input=not start.is_input)
        if target is None or target.is_input == start.is_input:
            return
        src, dst = (target, start) if start.is_input else (start, target)
        self.controller.connect_ports(src.block, src.index, dst.block, dst.index)

    def _free_port(self, item: BlockItem, want_input: bool) -> PortItem | None:
        ports = item.in_ports if want_input else item.out_ports
        if not want_input:
            return ports[0] if ports else None
        for p in ports:
            if self.model.diagram.driver(item.block, p.index) is None:
                return p
        return ports[0] if ports else None

    def contextMenuEvent(self, event: QGraphicsSceneContextMenuEvent) -> None:
        """Right-click: select what is under the mouse (keeping a multi-selection) and ask for its menu."""
        target: object = None
        block_item = self._block_at(event.scenePos())
        if block_item is not None:
            if not block_item.isSelected():
                self.clearSelection()
                block_item.setSelected(True)
            target = block_item.block
        else:
            wire = next((i for i in self.items(event.scenePos()) if isinstance(i, WireItem)), None)
            if wire is not None:
                self.clearSelection()
                wire.setSelected(True)
                target = wire.connection
            link = next((i for i in self.items(event.scenePos()) if isinstance(i, LinkItem)), None)
            if wire is None and link is not None:
                self.clearSelection()
                link.setSelected(True)
                target = link.link
        self.contextMenuRequested.emit(target, event.screenPos())
        event.accept()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        item = self._block_at(event.scenePos())
        if item is not None:
            self.blockActivated.emit(item.block)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------ drag & drop from the library

    def dragEnterEvent(self, event: QGraphicsSceneDragDropEvent) -> None:
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QGraphicsSceneDragDropEvent) -> None:
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QGraphicsSceneDragDropEvent) -> None:
        if event.mimeData().hasFormat(BLOCK_MIME):
            type_id = bytes(event.mimeData().data(BLOCK_MIME).data()).decode()
            pos = event.scenePos() - QPointF(35, 23)
            self.controller.add_block(type_id, QPointF(snap(pos.x()), snap(pos.y())))
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class DiagramView(QGraphicsView):
    """Zoom with Ctrl/⌘ + wheel or pinch; pan with the trackpad, middle mouse button or Space + drag."""

    zoomChanged = Signal(float)

    def __init__(self, scene: DiagramScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAcceptDrops(True)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._space = False
        self._panning = False
        self._pan_start = QPointF()
        self.centerOn(0, 0)
        ThemeManager.instance().themeChanged.connect(self._on_theme)

    def _on_theme(self, _theme: object) -> None:
        self.viewport().update()

    @property
    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, factor: float) -> None:
        factor = max(0.2, min(4.0, factor))
        self.scale(factor / self.zoom, factor / self.zoom)
        self.zoomChanged.emit(self.zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / 1.25)

    def fit(self) -> None:
        scene: DiagramScene = self.scene()  # type: ignore[assignment]
        rect = scene.items_rect()
        if rect.isEmpty():
            self.resetTransform()
            self.centerOn(0, 0)
        else:
            self.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.AspectRatioMode.KeepAspectRatio)
            if self.zoom > 1.5:
                self.set_zoom(1.5)
        self.zoomChanged.emit(self.zoom)

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        rect = QRectF(rect)
        t = current_theme()
        painter.fillRect(rect, t.color("canvas"))
        if self.zoom < 0.45:
            return
        step = GRID * 2
        left = float(rect.left() // step * step)
        top = float(rect.top() // step * step)
        painter.setPen(QPen(t.color("grid"), 1.6 / self.zoom))
        path = QPainterPath()
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                path.moveTo(x, y)
                path.lineTo(x + 0.01, y)
                y += step
            x += step
        painter.drawPath(path)

    def wheelEvent(self, event: QWheelEvent) -> None:
        mods = event.modifiers()
        zoom_mod = mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        if zoom_mod or event.pixelDelta().isNull():
            factor = 1.0015 ** event.angleDelta().y()
            self.set_zoom(self.zoom * factor)
        else:  # trackpad scroll pans
            d = event.pixelDelta()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
        event.accept()

    def event(self, event: Any) -> bool:
        if isinstance(event, QNativeGestureEvent) and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.set_zoom(self.zoom * (1 + event.value()))
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            self.unsetCursor()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.MiddleButton or (
            self._space and event.button() == Qt.MouseButton.LeftButton
        ):
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._panning:
            d = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._space else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def view_center_scene(self) -> QPointF:
        return self.mapToScene(self.viewport().rect().center())


def _moved(pts: list[Point], k: int, dx: float, dy: float) -> list[Point]:
    """``pts`` with segment k (points k and k+1) shifted across its direction."""
    out = list(pts)
    (x0, y0), (x1, y1) = pts[k], pts[k + 1]
    if x0 == x1:  # vertical segment: moves sideways
        out[k], out[k + 1] = (x0 + dx, y0), (x1 + dx, y1)
    else:
        out[k], out[k + 1] = (x0, y0 + dy), (x1, y1 + dy)
    return out


def _same_line(s1: tuple[Point, Point], s2: tuple[Point, Point]) -> bool:
    """Two segments on the same vertical or horizontal line that overlap or touch (a shared trunk, or the two
    arms of a branch point)."""
    (a0, a1), (b0, b1) = s1, s2
    if a0[0] == a1[0] == b0[0] == b1[0]:
        return min(a0[1], a1[1]) <= max(b0[1], b1[1]) and min(b0[1], b1[1]) <= max(a0[1], a1[1])
    if a0[1] == a1[1] == b0[1] == b1[1]:
        return min(a0[0], a1[0]) <= max(b0[0], b1[0]) and min(b0[0], b1[0]) <= max(a0[0], a1[0])
    return False


def _path(pts: list[Point]) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    for x, y in pts[1:]:
        path.lineTo(x, y)
    return path
