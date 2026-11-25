"""Graphics items for the diagram canvas: blocks, ports and wires."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QStyleOptionGraphicsItem, QWidget

from nervus.core import Block, Connection
from nervus.core.layout import ORIENTATIONS, Direction, orientation_of
from nervus.gui.theme import current_theme
from nervus.gui.views import routing
from nervus.gui.views.glyphs import glyph

if TYPE_CHECKING:
    from nervus.gui.views.canvas import DiagramScene

GRID = 10.0
PORT_SIZE = 8.0
MIN_W, MIN_H = 80.0, 40.0


def snap(v: float) -> float:
    return round(v / GRID) * GRID


def snap_up2(v: float) -> float:
    """Up to a multiple of two grid steps: a block's middle (where a single port sits) is then on the grid."""
    return math.ceil(v / (2 * GRID) - 1e-9) * 2 * GRID


PORT_PITCH = 2 * GRID
"""The smallest distance between two ports on a side."""


def port_offset(length: float, k: int, n: int) -> float:
    """Where port ``k`` of ``n`` sits along a side of ``length`` (a multiple of two grid steps): centred, a whole
    number of :data:`PORT_PITCH` apart, so every port is on the grid. A single port, or the middle one of an odd
    number, is in the middle; a two-input Sum's output is then level with the input of a one-port block next to it,
    and the wire between them is straight."""
    step = PORT_PITCH * max(1, math.floor(length / n / PORT_PITCH))
    return length / 2 + (k - (n - 1) / 2) * step


class PortItem(QGraphicsItem):
    """A port on the side of a block. Inputs are on the left, outputs on the right (swapped when flipped; on the
    top and bottom when the block is rotated)."""

    def __init__(self, block_item: BlockItem, is_input: bool, index: int) -> None:
        super().__init__(block_item)
        self.block_item = block_item
        self.is_input = is_input
        self.index = index
        self.hover = False
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setZValue(2)

    @property
    def block(self) -> Block:
        return self.block_item.block

    @property
    def direction(self) -> Direction:
        """Direction the signal flows through this port, e.g. (1, 0) to the right or (0, 1) downwards."""
        return ORIENTATIONS[self.block_item.port_orientation]

    def boundingRect(self) -> QRectF:
        s = PORT_SIZE + 6  # generous hit area
        return QRectF(-s / 2, -s / 2, s, s)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        t = current_theme()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = PORT_SIZE / 2
        # A small triangle pointing in the signal direction.
        if self.is_input:
            pts = [QPointF(-h, -h), QPointF(h * 0.6, 0), QPointF(-h, h)]
        else:
            pts = [QPointF(-h * 0.6, -h), QPointF(h, 0), QPointF(-h * 0.6, h)]
        dx, dy = self.direction
        pts = [QPointF(p.x() * dx - p.y() * dy, p.x() * dy + p.y() * dx) for p in pts]  # rotate
        color = t.color("accent") if self.hover else t.color("port")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF(pts))

    def hoverEnterEvent(self, event: Any) -> None:
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, event: Any) -> None:
        self.hover = False
        self.update()

    def scene_anchor(self) -> QPointF:
        """Where wires attach, in scene coordinates."""
        sign = -1 if self.is_input else 1
        dx, dy = self.direction
        return self.mapToScene(QPointF(sign * dx * PORT_SIZE / 2, sign * dy * PORT_SIZE / 2))


def block_width(block: Block) -> float:
    """The width a block is drawn with on the canvas (for auto layout)."""
    return BlockItem(block).size[0]


class BlockItem(QGraphicsItem):
    def __init__(self, block: Block) -> None:
        super().__init__()
        self.block = block
        self.error = False
        self.rate_color: QColor | None = None
        """Border colour for View → Sample Time Colours (``None``: normal border)."""
        self.in_ports: list[PortItem] = []
        self.out_ports: list[PortItem] = []
        self.phys_ports: list[PhysicalPortItem] = []
        self._w, self._h = MIN_W, MIN_H
        self.preview_size: list[float] | None = None
        """The size shown while the user drags the resize grip; committed through the controller on release."""
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self.refresh()

    # ------------------------------------------------------------------ geometry

    @property
    def size(self) -> tuple[float, float]:
        return self._w, self._h

    def refresh(self) -> None:
        """Rebuild ports and size after the block's parameters changed."""
        self.prepareGeometryChange()
        text = glyph(self.block)
        lines = text.split("\n")
        n = max(self.block.num_inputs(), self.block.num_outputs(), 1)
        if getattr(self.block, "system", None) is not None:
            names = [b.name for b in self.block.inports() + self.block.outports()]  # type: ignore[attr-defined]
            names += ["⏻ enable "] * bool(getattr(self.block, "has_enable", False))
            names += ["↑ trigger "] * bool(getattr(self.block, "has_trigger", False))
            icon_lines = str(getattr(self.block, "mask_icon", "")).replace("\\n", "\n").split("\n")
            width = 2 * max((len(x) for x in names), default=4) + max((len(x) for x in icon_lines), default=0)
            lines = [" " * width] * max(1, len(icon_lines))
        text_w = snap(max(len(line) for line in lines) * 8 + 24)
        phys = self.block.physical_ports()
        if self.is_physical:  # physical ports on the sides, signal ports on the top and bottom
            per_side = max(1, (len(phys) + 1) // 2)
            self._w = max(MIN_W, text_w, snap(n * 24 + 10))
            self._h = max(MIN_H, snap(max(per_side * PORT_PITCH, len(lines) * 16 + 16)))
        elif self.vertical:  # ports along the top and bottom edges
            self._w = max(MIN_W, text_w, snap(n * 24 + 10))
            self._h = max(MIN_H, snap(len(lines) * 16 + 16))
        else:
            self._w = max(MIN_W, text_w)
            self._h = max(MIN_H, snap(max(n * PORT_PITCH, len(lines) * 16 + 16)))
        size = self.preview_size if self.preview_size is not None else self.block.meta.get("size")
        if isinstance(size, list | tuple) and len(size) == 2:  # resized by the user: at least the minimum
            self._w, self._h = max(self._w, snap(float(size[0]))), max(self._h, snap(float(size[1])))
        self._w, self._h = snap_up2(self._w), snap_up2(self._h)
        for p in self.in_ports + self.out_ports:
            p.setParentItem(None)  # type: ignore[arg-type]
            if p.scene():
                p.scene().removeItem(p)
        self.in_ports = [PortItem(self, True, i) for i in range(self.block.num_inputs())]
        names = self.block.input_names()
        for i, p in enumerate(self.in_ports):
            label = names[i] if i < len(names) and names[i] else ""
            p.setToolTip(f"input {i + 1}" + (f": {label}" if label else ""))
        self.out_ports = [PortItem(self, False, i) for i in range(self.block.num_outputs())]
        out_names = self.block.output_names()
        for i, p in enumerate(self.out_ports):
            label = out_names[i] if i < len(out_names) and out_names[i] else ""
            p.setToolTip(f"output {i + 1}" + (f": {label}" if label else ""))
        if phys and getattr(self.block, "system", None) is not None:
            self._w = max(self._w, snap(len(phys) * 30 + 20))
        self._layout_physical(phys)
        dx, dy = ORIENTATIONS[self.port_orientation]
        for ports, sign in ((self.in_ports, -1), (self.out_ports, 1)):
            for i, p in enumerate(ports):
                if dy == 0:
                    p.setPos(self._w / 2 + sign * dx * (self._w + PORT_SIZE) / 2, port_offset(self._h, i, len(ports)))
                else:
                    p.setPos(port_offset(self._w, i, len(ports)), self._h / 2 + sign * dy * (self._h + PORT_SIZE) / 2)
        self.setToolTip(f"{self.block.label} ({self.block.type_id})\nDouble-click to edit · F1 for help")
        self.update()

    def boundingRect(self) -> QRectF:
        if self.vertical:  # ports above and below, the name label on the right
            return QRectF(-2, -PORT_SIZE, self._w + 130, self._h + 2 * PORT_SIZE)
        # Room for the name label under the block.
        return QRectF(-PORT_SIZE, -2, self._w + 2 * PORT_SIZE, self._h + 22)

    @property
    def flipped(self) -> bool:
        """Flipped blocks have their inputs on the right and outputs on the left (for feedback paths)."""
        return bool(self.block.meta.get("flipped", False))

    @property
    def vertical(self) -> bool:
        """Rotated a quarter turn: inputs on the top (bottom when flipped), signal flowing down (up)."""
        return bool(self.block.meta.get("vertical", False))

    @property
    def orientation(self) -> str:
        return orientation_of(self.block)

    @property
    def is_physical(self) -> bool:
        """A physical component (not a subsystem): physical ports left and right, signals top and bottom."""
        return bool(self.block.physical_ports()) and getattr(self.block, "system", None) is None

    @property
    def port_orientation(self) -> str:
        """Where the signal ports are: physical components have them on the top (inputs) and bottom (outputs)."""
        return "down" if self.is_physical else self.orientation

    def physical_port(self, name: str) -> PhysicalPortItem | None:
        return next((p for p in self.phys_ports if p.name == name), None)

    def _layout_physical(self, phys: list[tuple[str, str]]) -> None:
        for p in self.phys_ports:
            p.setParentItem(None)  # type: ignore[arg-type]
            if p.scene():
                p.scene().removeItem(p)
        self.phys_ports = []
        if not phys:
            return
        if getattr(self.block, "system", None) is not None:  # a subsystem: along the bottom edge
            for k, (name, dom) in enumerate(phys):
                item = PhysicalPortItem(self, name, dom, (0, 1))
                item.setPos(port_offset(self._w, k, len(phys)), self._h + PORT_SIZE / 2)
                self.phys_ports.append(item)
            return
        half = (len(phys) + 1) // 2
        sides = [(phys[:half], -1), (phys[half:], 1)]
        for ports, side in sides:
            if self.flipped:
                side = -side
            for k, (name, dom) in enumerate(ports):
                item = PhysicalPortItem(self, name, dom, (side, 0))
                item.setPos(self._w / 2 + side * (self._w + PORT_SIZE) / 2, port_offset(self._h, k, len(ports)))
                self.phys_ports.append(item)

    def resize_handle(self) -> QRectF:
        """The corner to drag to resize the block (shown when selected)."""
        return QRectF(self._w - 9, self._h - 9, 9, 9)

    def body_rect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def center(self) -> QPointF:
        return self.mapToScene(self.body_rect().center())

    # ------------------------------------------------------------------ painting

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        t = current_theme()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.body_rect()
        border = (
            t.color("error")
            if self.error
            else t.color("selection")
            if self.isSelected()
            else self.rate_color or t.color("block_border")
        )
        # soft shadow
        shadow = QColor(0, 0, 0, 35 if t.name == "light" else 90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(rect.translated(0, 2), 7, 7)
        painter.setBrush(t.color("block_fill"))
        painter.setPen(QPen(border, 2.0 if (self.isSelected() or self.error or self.rate_color) else 1.2))
        painter.drawRoundedRect(rect, 7, 7)

        font = QFont(painter.font())
        font.setPointSizeF(11)
        painter.setFont(font)
        painter.setPen(t.color("block_text"))
        system = getattr(self.block, "system", None)
        if system is not None:
            self._paint_subsystem(painter, rect)
        else:
            painter.drawText(rect.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignCenter, glyph(self.block))

        name_font = QFont(font)
        name_font.setPointSizeF(10)
        painter.setFont(name_font)
        painter.setPen(t.color("text_secondary"))
        if self.vertical:
            painter.drawText(
                QRectF(self._w + 6, self._h / 2 - 9, 124, 18), Qt.AlignmentFlag.AlignVCenter, self.block.name
            )
        else:
            painter.drawText(QRectF(-40, self._h + 2, self._w + 80, 18), Qt.AlignmentFlag.AlignHCenter, self.block.name)
        if self.isSelected():  # resize grip
            painter.setPen(QPen(t.color("selection"), 1.2))
            r = self.resize_handle()
            for k in (3, 6):
                painter.drawLine(QPointF(r.right() - k, r.bottom() - 1), QPointF(r.right() - 1, r.bottom() - k))

    def _paint_subsystem(self, painter: QPainter, rect: QRectF) -> None:
        """Port names inside the block, like Simulink's subsystem icon."""
        t = current_theme()
        small = QFont(painter.font())
        small.setPointSizeF(9)
        painter.setFont(small)
        painter.setPen(t.color("text_secondary"))
        sub = self.block
        if self.vertical:  # names next to the ports on the top and bottom edges
            for ports, items in ((sub.inports(), self.in_ports), (sub.outports(), self.out_ports)):  # type: ignore[attr-defined]
                for port_block, item in zip(ports, items, strict=False):
                    top = item.pos().y() < rect.height() / 2
                    box = QRectF(item.pos().x() - 40, 3 if top else rect.height() - 17, 80, 14)
                    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, port_block.name)
            ports_done = True
        else:
            ports_done = False
        left, right = Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignRight
        if self.flipped:
            left, right = right, left
        for ports, items, align, dx in (
            ()
            if ports_done
            else (
                (sub.inports(), self.in_ports, left, 6 if not self.flipped else -6),  # type: ignore[attr-defined]
                (sub.outports(), self.out_ports, right, -6 if not self.flipped else 6),  # type: ignore[attr-defined]
            )
        ):
            for port_block, item in zip(ports, items, strict=False):
                y = item.pos().y()
                box = QRectF(6 if dx > 0 else rect.width() / 2, y - 8, rect.width() / 2 - 8, 16)
                painter.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, port_block.name)
        edge_marks = {"rising": "↑", "falling": "↓", "either": "↕", "function_call": "ƒ()"}
        for index, text in (
            (getattr(sub, "enable_index", None), "⏻ enable"),
            (getattr(sub, "trigger_index", None), f"{edge_marks.get(sub.params.get('trigger', ''), '')} trigger"),
        ):
            if index is None or index >= len(self.in_ports):
                continue
            y = self.in_ports[index].pos().y()
            painter.drawText(
                QRectF(6, y - 8, rect.width() / 2, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        if getattr(sub, "library_link", None) is not None:
            pen = QPen(t.color("block_text"), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 4, 4)
            painter.setPen(t.color("block_text"))
        st = sub.params.get("sample_time", -1.0)
        if isinstance(st, int | float) and st > 0:
            period = f"{st * 1000:g} ms" if st < 1 else f"{st:g} s"
            small = QFont(painter.font())
            small.setPointSizeF(max(6.0, small.pointSizeF() - 1))
            painter.setFont(small)
            painter.drawText(
                rect.adjusted(6, 0, -18, -2), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, f"⏱ {period}"
            )
        if getattr(sub, "variants", None):
            small = QFont(painter.font())
            small.setPointSizeF(max(6.0, small.pointSizeF() - 1))
            painter.setFont(small)
            painter.drawText(
                rect.adjusted(6, 0, -18, -2),
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                f"◆ {getattr(sub, 'active_variant', '')}",
            )
        icon = getattr(sub, "mask_icon", "")
        if icon:
            big = QFont(painter.font())
            big.setPointSizeF(11)
            big.setBold(True)
            painter.setFont(big)
            painter.setPen(t.color("block_text"))
            painter.drawText(rect.adjusted(4, 4, -4, -4), Qt.AlignmentFlag.AlignCenter, icon.replace("\\n", "\n"))
        elif not sub.inports() and not sub.outports():  # type: ignore[attr-defined]
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "subsystem")
        # a small "open" hint in the corner
        painter.setPen(QPen(t.color("text_muted"), 1))
        painter.drawRect(QRectF(rect.right() - 13, rect.bottom() - 11, 8, 6))

    # ------------------------------------------------------------------ behaviour

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any) -> Any:
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(snap(value.x()), snap(value.y()))
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None:
                scene.block_item_moved(self)  # type: ignore[attr-defined]
        return super().itemChange(change, value)

    def port(self, is_input: bool, index: int) -> PortItem | None:
        ports = self.in_ports if is_input else self.out_ports
        return ports[index] if 0 <= index < len(ports) else None


class WireItem(QGraphicsPathItem):
    """A signal line drawn with orthogonal segments."""

    def __init__(self, connection: Connection, src: PortItem, dst: PortItem) -> None:
        super().__init__()
        self.connection = connection
        self.src, self.dst = src, dst
        self.hover = False
        self.type_label = ""
        """Data type and/or width shown next to the wire (empty: not shown, e.g. for a scalar double)."""
        self.wide = False
        """A vector signal: drawn with a thicker line (View → Signal Widths)."""
        self.rate_color: QColor | None = None
        """Colour of the source block's sample time (View → Sample Time Colours)."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self.update_path()

    def update_path(self) -> None:
        a, b = self.src.scene_anchor(), self.dst.scene_anchor()
        corners = self.connection.meta.get("points")
        scene: DiagramScene | None = self.scene()  # type: ignore[assignment]
        obstacles = scene.obstacles_near(a, b) if scene is not None and not corners else None
        self.setPath(route(a, b, self.src.direction, self.dst.direction, obstacles, corners))

    def corners(self) -> list[routing.Point]:
        """The points of the drawn route, ends included."""
        path = self.path()
        return [(path.elementAt(k).x, path.elementAt(k).y) for k in range(path.elementCount())]

    def segment_at(self, pos: QPointF, tolerance: float = 6.0) -> int | None:
        """Index k of the draggable segment (points k to k+1) near ``pos``. The segments at the ports cannot be
        dragged; they follow the blocks."""
        pts = self.corners()
        for k in range(1, len(pts) - 2):
            (x0, y0), (x1, y1) = pts[k], pts[k + 1]
            if (
                x0 == x1
                and abs(pos.x() - x0) <= tolerance
                and min(y0, y1) - tolerance <= pos.y() <= max(y0, y1) + tolerance
            ):
                return k
            if (
                y0 == y1
                and abs(pos.y() - y0) <= tolerance
                and min(x0, x1) - tolerance <= pos.x() <= max(x0, x1) + tolerance
            ):
                return k
        return None

    def signal_name(self) -> str:
        """The data-dictionary name of the source output, if it has one."""
        from nervus.core.dictionary import signal_names

        return signal_names(self.connection.src).get(self.connection.src_port, "")

    def boundingRect(self) -> QRectF:
        r = super().boundingRect()
        return r.adjusted(0, -16, 140, 16) if self.type_label or self.signal_name() else r

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(10)  # easy to click
        return stroker.createStroke(self.path())

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        t = current_theme()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = t.color("selection") if (self.isSelected() or self.hover) else self.rate_color or t.color("wire")
        painter.setPen(
            QPen(
                color,
                (2.0 if self.isSelected() else 1.5) + (1.5 if self.wide else 0.0),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())
        # Arrow head at the destination.
        end = self.dst.scene_anchor()
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([end, end + QPointF(-7, -4), end + QPointF(-7, 4)]))
        if self.type_label:
            start = self.src.scene_anchor()
            f = QFont(painter.font())
            f.setPointSizeF(8)
            painter.setFont(f)
            painter.setPen(t.color("accent"))
            painter.drawText(
                QRectF(start.x() + 4, start.y() - 15, 140, 12), Qt.AlignmentFlag.AlignLeft, self.type_label
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
        name = self.signal_name()
        if name:  # the signal's name, under the wire next to the source
            start = self.src.scene_anchor()
            f = QFont(painter.font())
            f.setPointSizeF(8)
            f.setItalic(True)
            painter.setFont(f)
            painter.setPen(t.color("text") if not self.isSelected() else color)
            painter.drawText(QRectF(start.x() + 6, start.y() + 4, 140, 12), Qt.AlignmentFlag.AlignLeft, name)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
        # Branch dot where an output fans out to several inputs.
        scene: DiagramScene | None = self.scene()  # type: ignore[assignment]
        if scene is not None and scene.fanout(self.connection) > 1:
            painter.drawEllipse(self.src.scene_anchor() + QPointF(10, 0), 3, 3)

    def hoverEnterEvent(self, event: Any) -> None:
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, event: Any) -> None:
        self.hover = False
        self.update()


DOMAIN_COLORS = {"electrical": 0, "rotational": 2, "thermal": 1, "hydraulic": 5}
"""Index into the theme's categorical colours for the ports and links of each physical domain."""


def domain_color(domain: str) -> QColor:
    t = current_theme()
    return QColor(t.series[DOMAIN_COLORS.get(domain, 6) % len(t.series)])


class PhysicalPortItem(QGraphicsItem):
    """A physical port (an electrical pin, a shaft end, a heat path): a small square in the domain's colour."""

    def __init__(self, block_item: BlockItem, name: str, domain: str, outward: Direction) -> None:
        super().__init__(block_item)
        self.block_item, self.name, self.domain, self.outward = block_item, name, domain, outward
        self.hover = False
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setZValue(2)
        self.setToolTip(f"{name} ({domain})")

    @property
    def block(self) -> Block:
        return self.block_item.block

    def boundingRect(self) -> QRectF:
        s = PORT_SIZE + 6
        return QRectF(-s / 2, -s / 2, s, s)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        t = current_theme()
        h = PORT_SIZE / 2
        painter.setPen(QPen(t.color("accent") if self.hover else domain_color(self.domain), 1.5))
        painter.setBrush(t.color("block_fill"))
        painter.drawRect(QRectF(-h, -h, PORT_SIZE, PORT_SIZE))

    def hoverEnterEvent(self, event: Any) -> None:
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, event: Any) -> None:
        self.hover = False
        self.update()

    def scene_anchor(self) -> QPointF:
        dx, dy = self.outward
        return self.mapToScene(QPointF(dx * PORT_SIZE / 2, dy * PORT_SIZE / 2))


class LinkItem(QGraphicsPathItem):
    """A physical link: a plain line (no arrow, no direction) in the colour of its domain."""

    def __init__(self, link: Any, a: PhysicalPortItem, b: PhysicalPortItem) -> None:
        super().__init__()
        self.link, self.a, self.b = link, a, b
        self.hover = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self.update_path()

    def update_path(self) -> None:
        pa, pb = self.a.scene_anchor(), self.b.scene_anchor()
        out_b = self.b.outward
        scene: DiagramScene | None = self.scene()  # type: ignore[assignment]
        obstacles = scene.obstacles_near(pa, pb) if scene is not None else None
        self.setPath(route(pa, pb, self.a.outward, (-out_b[0], -out_b[1]), obstacles))

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        t = current_theme()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = t.color("selection") if (self.isSelected() or self.hover) else domain_color(self.a.domain)
        painter.setPen(QPen(color, 3.0 if self.isSelected() else 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

    def hoverEnterEvent(self, event: Any) -> None:
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, event: Any) -> None:
        self.hover = False
        self.update()


def _vec(d: int | Direction) -> Direction:
    return (d, 0) if isinstance(d, int) else d


def route(
    a: QPointF,
    b: QPointF,
    a_dir: int | Direction = 1,
    b_dir: int | Direction = 1,
    obstacles: list[routing.Rect] | None = None,
    corners: list[routing.Point] | None = None,
) -> QPainterPath:
    """Orthogonal route from an output anchor ``a`` to an input anchor ``b``.

    ``a_dir``/``b_dir`` are the signal directions at the ports: (1, 0) to the right, (-1, 0) to the left (flipped
    block), (0, 1) down and (0, -1) up (rotated blocks). An int means a horizontal direction (+1 or -1).
    ``obstacles`` (block rectangles) are avoided if possible; ``corners`` are manually placed corners.
    """
    pa, pb, da, db = (a.x(), a.y()), (b.x(), b.y()), _vec(a_dir), _vec(b_dir)
    pts = routing.through(pa, pb, da, db, corners) if corners else routing.route(pa, pb, da, db, obstacles or ())
    path = QPainterPath(a)
    for x, y in pts[1:]:
        path.lineTo(x, y)
    return path
