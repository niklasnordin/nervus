"""Physical networks: electrical circuits, rotating shafts and heat paths, solved with modified nodal analysis.

Physical blocks (a resistor, a motor, an inertia) have **ports** that are joined by **links** instead of signal
lines. Every group of linked ports is a **node** with one *across* value (voltage, angular velocity,
temperature); every component lets a *through* value flow between its nodes (current, torque, heat flow).
Kirchhoff's current law at every node gives the equations, assembled automatically:

========== ================== ================== =================================
domain     across             through            capacitor-like / inductor-like
========== ================== ================== =================================
electrical voltage [V]        current [A]        capacitor / inductor
rotational ang. velocity      torque [N·m]       inertia / spring
           [rad/s]
thermal    temperature [°C]   heat flow [W]      thermal mass / -
========== ================== ================== =================================

Each dynamic element contributes a state (a capacitor's voltage, an inductor's current, an inertia's speed). At
every evaluation the states are known, so a capacitor acts as a voltage source and an inductor as a current
source, and the network is one linear system ``A·z = b`` (modified nodal analysis: node across values plus the
through values of voltage-source-like branches). Its solution gives every derivative, e.g. ``C dv/dt = i``.
Components with modes (diodes, switches) are iterated until their modes are consistent.

In the compiled model each network becomes two nodes: a **core** that owns the states (its outputs depend on
the states only) and an **algebraic** part for the sensor outputs that react instantly to input signals. So a
controller that reads a motor's speed and sets its voltage is not an algebraic loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np

from nervus.core.block import Block, Width
from nervus.core.errors import ModelError, SimulationError
from nervus.core.sample_time import CONTINUOUS, SampleTime
from nervus.core.types import Array

GROUND = -1
"""Index of the reference node (0 V, standstill, 0 °C) in stamps."""
GMIN = 1e-12
"""A tiny conductance from every node to the reference, so a floating node does not make the system singular."""
MAX_MODE_ITERATIONS = 25
MAX_NEWTON_ITERATIONS = 150
NEWTON_RELTOL, NEWTON_VNTOL = 1e-6, 1e-9
SPARSE_ABOVE = 80
"""Use a sparse solver for networks with more unknowns than this."""

DOMAINS = {
    "electrical": ("voltage", "V", "current", "A"),
    "rotational": ("angular velocity", "rad/s", "torque", "N·m"),
    "thermal": ("temperature", "°C", "heat flow", "W"),
    "hydraulic": ("pressure", "bar", "volume flow", "L/min"),
}


class Stamper:
    """Collects the contributions of the components to ``A·z = b``."""

    def __init__(self, n_nodes: int, n_branches: int) -> None:
        self.n = n_nodes
        self.size = size = n_nodes + n_branches
        # Plain lists: components add their contributions one number at a time, which is several times faster in
        # a list than in a numpy array; :meth:`matrix` makes the arrays for the solve.
        self._a = [0.0] * (size * size)
        self.b = [0.0] * size
        for k in range(n_nodes):
            self._a[k * size + k] = GMIN
        self.guess: Solution | None = None
        """The previous Newton iterate: nonlinear components linearize around it (``None``: first iteration)."""
        self.limited = False
        """A component limited its step this iteration (SPICE's "noncon"): the iteration has not converged."""

    def limit(self, requested: float, limited: float) -> float:
        """Record a step limit (if ``limited`` differs from ``requested``) and return the limited value."""
        if abs(limited - requested) > 1e-12:
            self.limited = True
        return limited

    def across_guess(self, node: int) -> float:
        return 0.0 if self.guess is None else self.guess.across(node)

    def add(self, row: int, col: int, value: float) -> None:
        """Add ``value`` at (row, col); rows/cols of nodes are node indices, of branches ``n + k`` (see below)."""
        if row >= 0 and col >= 0:
            self._a[row * self.size + col] += value

    def matrix(self) -> Array:
        """The matrix A (an array, for the solve)."""
        return np.array(self._a).reshape(self.size, self.size)

    def conductance(self, a: int, b: int, g: float) -> None:
        """A through value g·(across_a - across_b) from a to b (a resistor, a damper, a thermal resistance)."""
        m, n = self._a, self.size
        if a >= 0:
            m[a * n + a] += g
            if b >= 0:
                m[a * n + b] -= g
                m[b * n + a] -= g
        if b >= 0:
            m[b * n + b] += g

    def flow(self, a: int, b: int, value: float) -> None:
        """A known through value from node a, through the component, to node b (a current source)."""
        if a >= 0:
            self.b[a] -= value
        if b >= 0:
            self.b[b] += value

    def branch(self, k: int) -> int:
        """Row/column index of branch k (its through value is an unknown)."""
        return self.n + k

    def effort(self, k: int, a: int, b: int, value: float) -> None:
        """Branch k fixes across_a - across_b = value; its through value (from a to b) is unknown."""
        r, m, n = self.n + k, self._a, self.size
        if a >= 0:
            m[a * n + r] += 1.0
            m[r * n + a] += 1.0
        if b >= 0:
            m[b * n + r] -= 1.0
            m[r * n + b] -= 1.0
        self.b[r] = value


@dataclass
class Solution:
    """The solved network: across values of the nodes and through values of the branches."""

    z: Array
    n_nodes: int

    def across(self, node: int) -> float:
        return 0.0 if node < 0 else float(self.z[node])

    def through(self, k: int) -> float:
        return float(self.z[self.n_nodes + k])


class PhysicalBlock(Block):
    """Base class of physical components. Subclasses declare ``PORTS`` and stamp their equations.

    ``nodes`` (one index per port, ``GROUND`` for the reference), ``branches`` (indices of the component's own
    branches) and ``x`` (its own states) are handed in by the network.
    """

    PORTS: ClassVar[tuple[tuple[str, str], ...]] = ()
    NUM_INPUTS = 0
    NUM_OUTPUTS = 0
    N_BRANCHES: ClassVar[int] = 0
    REFERENCE: ClassVar[bool] = False
    """A reference (ground) component: its port is the network's reference node."""
    NONLINEAR: ClassVar[bool] = False
    """Its stamp depends on ``Stamper.guess``: the network iterates (Newton) until the solution converges."""

    def physical_ports(self) -> list[tuple[str, str]]:
        return list(self.PORTS)

    def n_branches(self) -> int:
        return self.N_BRANCHES

    def physical_states(self) -> Array:
        """Initial values of the component's states."""
        return np.zeros(0)

    def state_names(self) -> list[str]:
        return [f"x{k}" for k in range(self.physical_states().size)]

    def stamp(self, s: Stamper, nodes: list[int], branches: list[int], x: Array, u: Sequence[float]) -> None:
        """Add the component's equations, for its states ``x`` and input signals ``u``."""

    def rates(self, sol: Solution, nodes: list[int], branches: list[int], x: Array, u: Sequence[float]) -> Array:
        """Derivatives of the component's states."""
        return np.zeros(0)

    def measure(
        self, sol: Solution, nodes: list[int], branches: list[int], x: Array, u: Sequence[float]
    ) -> list[float]:
        """The component's output signals (sensors)."""
        return []

    def update_mode(self, sol: Solution, nodes: list[int], branches: list[int]) -> bool:
        """For components with modes (diodes): adjust the mode to the solution; return True if it changed."""
        return False

    def mode_events(self, sol: Solution, nodes: list[int], branches: list[int], u: Sequence[float]) -> list[float]:
        """Signals that cross zero where the component changes mode (for exact event location)."""
        return []

    # A physical block is never simulated by itself: the network evaluates it.
    def sample_time(self) -> SampleTime:
        return CONTINUOUS

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [1] * self.num_outputs()

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        raise SimulationError(f"{self.name}: a physical block is evaluated by its network")


@dataclass
class _Member:
    block: PhysicalBlock
    path: str
    nodes: list[int]
    branches: list[int]
    states: slice
    inputs: slice  # in the network's input vector
    outputs: slice  # in the network's output vector


@dataclass
class PhysicalNetwork:
    """One connected network of physical components, assembled at compile time."""

    name: str
    members: list[_Member] = field(default_factory=list)
    n_nodes: int = 0
    n_branches: int = 0
    node_names: list[str] = field(default_factory=list)
    port_nodes: dict[str, int] = field(default_factory=dict)
    """Node of every port, by ``"block.port"`` (``GROUND`` for the reference)."""
    x0: Array = field(default_factory=lambda: np.zeros(0))
    n_inputs: int = 0
    n_outputs: int = 0
    instant_outputs: list[int] = field(default_factory=list)
    """Outputs that depend directly on input signals (computed by the algebraic node)."""
    _last: Solution | None = None
    """The last solution of a nonlinear network: the starting point of the next Newton iteration."""
    _memo: tuple[bytes, bytes, Solution] | None = None
    """The last solution of a network whose solution depends only on x and u (no component with modes; a nonlinear
    one converges to the same solution within the Newton tolerance): the outputs and the derivatives at one time
    point use the same solution, solved once."""

    last_u: Array | None = None
    """The inputs of the last solve with real inputs: the outputs that depend on the states only are solved with
    them, so that the solve for the other outputs at the same time point (same states, same inputs, which are
    mostly held between controller samples) is the same solution, solved once."""

    def _pure(self) -> bool:
        if not hasattr(self, "_is_pure"):
            self._is_pure = not any(type(m.block).update_mode is not PhysicalBlock.update_mode for m in self.members)
        return self._is_pure

    def solve(self, x: Array, u: Array) -> Solution:
        if self._pure():
            kx, ku = x.tobytes(), u.tobytes()
            memo = self._memo
            if memo is not None and memo[0] == kx and memo[1] == ku:
                return memo[2]
            sol = self._solve(x, u)
            self._memo = (kx, ku, sol)
            return sol
        return self._solve(x, u)

    def _solve(self, x: Array, u: Array) -> Solution:
        """Solve the network for states ``x`` and inputs ``u``. Nonlinear components (transistors, diodes)
        are linearized around the previous iterate (Newton-Raphson), starting from the last solution; components
        with modes are iterated until their modes are consistent."""
        nonlinear = any(m.block.NONLINEAR for m in self.members)
        size = self.n_nodes + self.n_branches
        guess = self._last if (nonlinear and self._last is not None and self._last.z.size == size) else None
        sol: Solution | None = None
        limit = MAX_NEWTON_ITERATIONS if nonlinear else MAX_MODE_ITERATIONS
        ulist = u.tolist()
        inputs = [ulist[m.inputs] for m in self.members]  # the same in every iteration
        for _ in range(limit):
            s = Stamper(self.n_nodes, self.n_branches)
            s.guess = guess
            for m, um in zip(self.members, inputs, strict=True):
                m.block.stamp(s, m.nodes, m.branches, x[m.states], um)
            z = self._linear_solve(s)
            sol = Solution(z, self.n_nodes)
            changed = False
            for m in self.members:
                changed = m.block.update_mode(sol, m.nodes, m.branches) or changed
            converged = True
            if nonlinear:
                prev = guess.z if guess is not None else np.zeros(size)
                converged = not s.limited and bool(np.all(np.abs(z - prev) <= NEWTON_RELTOL * np.abs(z) + NEWTON_VNTOL))
                guess = sol
            if converged and not changed:
                break
        else:
            if nonlinear:
                raise SimulationError(
                    f"the circuit {self.name} did not converge (Newton iteration); check for a missing resistor or "
                    "a source that drives a semiconductor directly",
                    tuple(m.path for m in self.members),
                )
        assert sol is not None
        if nonlinear:
            self._last = sol
        return sol

    def _linear_solve(self, s: Stamper) -> Array:
        try:
            a, b = s.matrix(), np.array(s.b)
            if a.shape[0] > SPARSE_ABOVE:
                from scipy.sparse import csc_matrix
                from scipy.sparse.linalg import spsolve

                z = spsolve(csc_matrix(a), b)
                if not np.all(np.isfinite(z)):
                    raise np.linalg.LinAlgError("singular")
            else:
                z = np.linalg.solve(a, b)
        except (np.linalg.LinAlgError, RuntimeError) as exc:
            raise SimulationError(self._singular_message(), tuple(m.path for m in self.members)) from exc
        return np.asarray(z, dtype=float)

    def _singular_message(self) -> str:
        return (
            f"physical network {self.name} cannot be solved: two voltage-source-like elements fix the same "
            "across value (e.g. a capacitor directly across a voltage source, two inertias on one shaft without "
            "a spring), or a current-source-like element is in series with nothing that can take its current"
        )

    def state_outputs(self, x: Array) -> Array:
        """The outputs at states ``x``; only those that do not depend on the inputs are valid."""
        u = self.last_u if self.last_u is not None else np.zeros(self.n_inputs)
        return self._outputs(x, u)

    def derivatives(self, x: Array, u: Array) -> Array:
        self.last_u = u
        sol = self.solve(x, u)
        dx = np.zeros_like(x)
        for m in self.members:
            if m.states.stop > m.states.start:
                dx[m.states] = m.block.rates(sol, m.nodes, m.branches, x[m.states], u[m.inputs].tolist())
        return dx

    def outputs(self, x: Array, u: Array) -> Array:
        self.last_u = u
        return self._outputs(x, u)

    def _outputs(self, x: Array, u: Array) -> Array:
        sol = self.solve(x, u)
        y = np.zeros(self.n_outputs)
        for m in self.members:
            if m.outputs.stop > m.outputs.start:
                y[m.outputs] = m.block.measure(sol, m.nodes, m.branches, x[m.states], u[m.inputs].tolist())
        return y

    def events(self, x: Array, u: Array) -> Array:
        sol = self.solve(x, u)
        out: list[float] = []
        for m in self.members:
            out += m.block.mode_events(sol, m.nodes, m.branches, u[m.inputs].tolist())
        return np.asarray(out, dtype=float)

    def classify_outputs(self) -> None:
        """Find the outputs that change when only the inputs change (states held): they feed through."""
        if self.n_inputs == 0 or self.n_outputs == 0:
            self.instant_outputs = []
            return
        # Many states and inputs over a wide range: in a circuit with transistors or switches an input may only
        # matter once it is large enough to turn a device on.
        rng = np.random.default_rng(0)
        changed = np.zeros(self.n_outputs, dtype=bool)
        for scale in (0.5, 5.0, 50.0):
            for _ in range(4):
                x = self.x0 + rng.normal(0.0, scale / 5, self.x0.size)
                try:
                    base = self.outputs(x, np.zeros(self.n_inputs))
                    for amp in (1.0, scale):
                        y = self.outputs(x, rng.uniform(-amp, amp, self.n_inputs))
                        changed |= ~np.isclose(y, base, rtol=1e-9, atol=1e-12)
                except SimulationError:
                    continue  # an extreme probe that does not converge says nothing about the structure
        self._last = None
        self.last_u = None
        self.instant_outputs = [k for k in range(self.n_outputs) if changed[k]]


class NetworkCore(Block):
    """The states of a physical network. Output 0: the state vector; then the outputs that depend on the states
    only (e.g. the speed of an inertia), in the order of ``state_outputs``."""

    type_id = ""
    DIRECT_FEEDTHROUGH = False

    def __init__(self, network: PhysicalNetwork, state_outputs: list[int]) -> None:
        super().__init__(network.name)
        self.network = network
        self.state_outputs = state_outputs

    def num_inputs(self) -> int:
        return self.network.n_inputs

    def num_outputs(self) -> int:
        return 1 + len(self.state_outputs)

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        widths: list[Width] = [max(1, self.network.x0.size)]
        return widths + [1] * len(self.state_outputs)

    def sample_time(self) -> SampleTime:
        return CONTINUOUS

    def initial_continuous_state(self) -> Array:
        return self.network.x0.copy()

    def _u(self, u: Sequence[Array]) -> Array:
        return np.array([float(np.ravel(v)[0]) for v in u]) if u else np.zeros(0)

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        states = xc.copy() if xc.size else np.zeros(1)
        if not self.state_outputs:
            return [states]
        y = self.network.state_outputs(xc)  # these outputs do not depend on u
        return [states, *[np.array([y[k]]) for k in self.state_outputs]]

    def derivatives(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> Array:
        return self.network.derivatives(xc, self._u(u))

    def zero_crossings(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> Array | None:
        ev = self.network.events(xc, self._u(u))
        return ev if ev.size else None


class NetworkAlgebraic(Block):
    """The outputs of a physical network that depend directly on input signals. Input 0: the state vector from
    the core; then the network's input signals."""

    type_id = ""

    def __init__(self, network: PhysicalNetwork) -> None:
        super().__init__(network.name + ":instant")
        self.network = network

    def num_inputs(self) -> int:
        return 1 + self.network.n_inputs

    def num_outputs(self) -> int:
        return len(self.network.instant_outputs)

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        widths: list[Width] = [1] * self.num_outputs()
        return widths

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        n = self.network.x0.size
        x = np.asarray(u[0], dtype=float)[:n]
        sig = np.array([float(np.ravel(v)[0]) for v in u[1:]]) if len(u) > 1 else np.zeros(0)
        y = self.network.outputs(x, sig)
        return [np.array([y[k]]) for k in self.network.instant_outputs]


class NetworkTap(Block):
    """Stands in for a physical block in the compiled model: passes on its sensor outputs, under its name."""

    type_id = ""

    def __init__(self, name: str, n: int) -> None:
        super().__init__(name)
        self.n = n

    def num_inputs(self) -> int:
        return self.n

    def num_outputs(self) -> int:
        return self.n

    def output_widths(self, in_widths: list[Width]) -> list[Width]:
        return [1] * self.n

    def outputs(self, t: float, u: Sequence[Array], xc: Array, xd: Array) -> list[Array]:
        return [np.asarray(v, dtype=float).copy() for v in u]


def build_networks(
    blocks: list[Block], names: list[str], links: list[tuple[int, str, int, str]]
) -> list[tuple[PhysicalNetwork, list[int]]]:
    """Group the physical blocks (graph node indices) into connected networks; returns each network with the
    graph nodes of its members. ``links`` are (node, port, node, port)."""
    phys = [i for i, b in enumerate(blocks) if isinstance(b, PhysicalBlock)]
    parent: dict[tuple[int, str], tuple[int, str]] = {}

    def find(k: tuple[int, str]) -> tuple[int, str]:
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a: tuple[int, str], b: tuple[int, str]) -> None:
        parent[find(a)] = find(b)

    for i in phys:
        for port, _dom in blocks[i].physical_ports():
            find((i, port))
    for na, pa, nb, pb in links:
        union((na, pa), (nb, pb))
    # Components that share any node belong to one network.
    comp_parent: dict[int, int] = {i: i for i in phys}

    def cfind(i: int) -> int:
        while comp_parent[i] != i:
            comp_parent[i] = comp_parent[comp_parent[i]]
            i = comp_parent[i]
        return i

    root_owner: dict[tuple[int, str], int] = {}
    for i in phys:
        for port, _dom in blocks[i].physical_ports():
            r = find((i, port))
            if r in root_owner:
                comp_parent[cfind(i)] = cfind(root_owner[r])
            else:
                root_owner[r] = i
    groups: dict[int, list[int]] = {}
    for i in phys:
        groups.setdefault(cfind(i), []).append(i)
    networks = []
    for k, members in enumerate(groups.values()):
        net = PhysicalNetwork(f"network{k + 1}" if len(groups) > 1 else "network")
        node_of: dict[tuple[int, str], int] = {}
        ground_roots = {
            find((i, port))
            for i in members
            if getattr(blocks[i], "REFERENCE", False)
            for port, _dom in blocks[i].physical_ports()
        }
        x_parts: list[Array] = []
        n_states = n_in = n_out = n_br = 0
        for i in members:
            b = blocks[i]
            assert isinstance(b, PhysicalBlock)
            nodes = []
            for port, _dom in b.physical_ports():
                r = find((i, port))
                if r in ground_roots:
                    nodes.append(GROUND)
                elif r in node_of:
                    nodes.append(node_of[r])
                else:
                    node_of[r] = len(net.node_names)
                    net.node_names.append(f"{names[r[0]]}.{r[1]}")
                    nodes.append(node_of[r])
                net.port_nodes[f"{names[i]}.{port}"] = nodes[-1]
            x = b.physical_states()
            ni, no, nb = b.num_inputs(), b.num_outputs(), b.n_branches()
            net.members.append(
                _Member(
                    b,
                    names[i],
                    nodes,
                    list(range(n_br, n_br + nb)),
                    slice(n_states, n_states + x.size),
                    slice(n_in, n_in + ni),
                    slice(n_out, n_out + no),
                )
            )
            x_parts.append(x)
            n_states += x.size
            n_in += ni
            n_out += no
            n_br += nb
        net.n_nodes = len(net.node_names)
        net.n_branches = n_br
        net.x0 = np.concatenate(x_parts) if x_parts else np.zeros(0)
        net.n_inputs, net.n_outputs = n_in, n_out
        domains = {dom for i in members for _p, dom in blocks[i].physical_ports()}
        if (
            "electrical" in domains
            and not ground_roots
            and any(dom == "electrical" for i in members for _p, dom in blocks[i].physical_ports())
        ):
            names_list = ", ".join(names[i] for i in members[:4])
            raise ModelError(
                f"the circuit with {names_list} has no Electrical Reference (ground): add one",
                tuple(names[i] for i in members),
            )
        networks.append((net, members))
    return networks


def _describe(net: PhysicalNetwork) -> dict[str, Any]:
    """For debugging: the members and nodes of a network."""
    return {"nodes": net.node_names, "members": [(m.path, m.nodes) for m in net.members]}


VT = 0.025852
"""Thermal voltage kT/q at 27 °C [V], as in SPICE."""


def junction_limit(v_new: float, v_old: float, nvt: float, vcrit: float) -> float:
    """SPICE's pnjlim: limit a junction voltage step so the exponential does not overshoot during Newton."""
    if v_new > vcrit and abs(v_new - v_old) > 2 * nvt:
        if v_old > 0:
            arg = 1 + (v_new - v_old) / nvt
            return v_old + nvt * np.log(arg) if arg > 0 else vcrit
        return nvt * np.log(v_new / nvt)
    return v_new


def exp_junction(v: float, i_s: float, nvt: float) -> tuple[float, float]:
    """Current and conductance of an ideal junction, i = Is (e^(v/nVt) - 1), with the exponent capped."""
    arg = min(v / nvt, 80.0)
    e = np.exp(arg)
    return i_s * (e - 1.0), i_s * e / nvt
