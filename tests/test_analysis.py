import numpy as np
import pytest

from nervus import Diagram
from nervus.analysis import linearize, trim
from nervus.blocks import Gain, Inport, Integrator, Outport, PythonFunction, Sum, TransferFunction


def io(block) -> Diagram:  # type: ignore[no-untyped-def]
    d = Diagram()
    u = d.add(Inport(port=1), name="u")
    b = d.add(block, name="g")
    d.chain(u, b, d.add(Outport(port=1), name="y"))
    return d


def test_first_order_tf() -> None:
    lin = linearize(io(TransferFunction(numerator=[2.0], denominator=[0.5, 1.0])))
    num, den = lin.transfer_function()
    np.testing.assert_allclose(num / den[0], [4.0], rtol=1e-6)
    np.testing.assert_allclose(den / den[0], [1.0, 2.0], rtol=1e-6)
    assert lin.dc_gain() == pytest.approx(2.0, rel=1e-6)
    np.testing.assert_allclose(lin.poles(), [-2.0], rtol=1e-6)
    assert lin.is_stable()
    _t, y = lin.step_response(np.linspace(0, 5, 200))
    assert y[-1] == pytest.approx(2.0, rel=1e-3)


def test_bode_and_margins() -> None:
    # Open loop L(s) = 10 / (s (s+1) (0.1 s + 1)): classic textbook example.
    lin = linearize(
        io(TransferFunction(numerator=[10.0], denominator=np.polymul([1, 0], np.polymul([1, 1], [0.1, 1]))))
    )
    _w, mag, phase = lin.bode(np.array([1e-3, 1.0, 100.0]))
    assert mag[0] == pytest.approx(20 * np.log10(10 / 1e-3), abs=0.01)
    assert phase[0] == pytest.approx(-90.0, abs=0.5)
    m = lin.margins()
    # Phase crossover at w = sqrt(10) rad/s, |L| there = 10/(sqrt10*sqrt11*sqrt1.1) -> GM = 20 log10(1.1) dB.
    assert m.phase_crossover == pytest.approx(np.sqrt(10.0), rel=1e-3)
    assert m.gain_margin_db == pytest.approx(20 * np.log10(1.1), abs=0.05)
    assert 0 < m.phase_margin_deg < 10


def nonlinear_tank() -> Diagram:
    """Tank level h: A dh/dt = q_in - k sqrt(h)."""
    d = Diagram()
    q = d.add(Inport(port=1, value=2.0), name="q_in")
    out = d.add(PythonFunction(code="def f(t, h):\n    return 0.5 * math.sqrt(max(h, 0.0))\n"), name="outflow")
    s = d.add(Sum(signs="+-"), name="net")
    a = d.add(Gain(gain=1 / 3.0), name="1/A")
    h = d.add(Integrator(initial=1.0), name="h")
    d.connect(q, (s, 0))
    d.connect(out, (s, 1))
    d.chain(s, a, h, out)
    d.connect(h, d.add(Outport(port=1), name="level"))
    return d


def test_trim_and_linearize_nonlinear() -> None:
    d = nonlinear_tank()
    op = trim(d, {"q_in": 2.0})
    assert op.x[0] == pytest.approx(16.0, rel=1e-6)  # 0.5 sqrt(h) = 2
    assert op.residual < 1e-9
    lin = linearize(d, x0=op.x, u0=op.u)
    # d(dh/dt)/dh = -(0.5 / (2 sqrt 16)) / 3 = -1/48
    np.testing.assert_allclose(lin.A, [[-1 / 48]], rtol=1e-5)
    np.testing.assert_allclose(lin.B, [[1 / 3]], rtol=1e-5)
    assert lin.states == ["h"]
    assert lin.inputs == ["q_in"] and lin.outputs == ["level"]
    assert d.block("q_in").signal is None  # the model is left unchanged


def test_mass_spring_damper_poles() -> None:
    d = Diagram()
    f = d.add(Inport(port=1), name="F")
    s = d.add(Sum(signs="+--"), name="sum")
    v = d.add(Integrator(), name="v")
    x = d.add(Integrator(), name="x")
    c = d.add(Gain(gain=0.4), name="c")
    k = d.add(Gain(gain=4.0), name="k")
    d.connect(f, (s, 0))
    d.connect(c, (s, 1))
    d.connect(k, (s, 2))
    d.chain(s, v, x)
    d.connect(v, c)
    d.connect(x, k)
    d.connect(x, d.add(Outport(port=1), name="pos"))
    lin = linearize(d)
    poles = sorted(lin.poles(), key=lambda z: z.imag)
    np.testing.assert_allclose(poles, np.roots([1, 0.4, 4.0])[np.argsort(np.roots([1, 0.4, 4.0]).imag)], rtol=1e-5)


def _third_order():  # type: ignore[no-untyped-def]
    from scipy import signal as sps

    from nervus.analysis import LinearModel

    a, b, c, d = sps.tf2ss([1.0], [1.0, 3.0, 3.0, 1.0])  # 1 / (s + 1)^3
    return LinearModel(a, b, c, d, ["u"], ["y"])


def test_root_locus_and_nyquist() -> None:
    from scipy import signal as sps

    from nervus.analysis import LinearModel

    a, b, c, d = sps.tf2ss([1.0], [1.0, 2.0, 0.0])  # 1 / (s (s + 2))
    g = LinearModel(a, b, c, d, ["u"], ["y"])
    _gains, poles = g.root_locus(np.array([0.0, 1.0, 5.0]))
    np.testing.assert_allclose(sorted(poles[0].real), [-2.0, 0.0], atol=1e-9)  # open-loop poles at k = 0
    np.testing.assert_allclose(poles[1], [-1.0, -1.0], atol=1e-6)  # s^2 + 2s + 1: double pole
    np.testing.assert_allclose(sorted(poles[2].imag), [-2.0, 2.0], atol=1e-9)  # s^2 + 2s + 5 = (s+1)^2 + 4
    re, im = g.nyquist(np.array([0.5, 1.0, 2.0]))
    assert re.size == 6 and np.allclose(im[:3], -im[3:][::-1])  # mirrored at negative frequencies
    _num, den = g.closed_loop()
    np.testing.assert_allclose(np.roots(den), [-1.0, -1.0], atol=1e-6)


def test_plant_seen_by_a_controller() -> None:
    from nervus.analysis import plant_at
    from nervus.blocks import PID, Constant, Gain, Scope, Sum, TransferFunction

    d = Diagram()
    err = d.add(Sum(signs="+-"), name="error")
    pid = d.add(PID(P=1.0, I=0.5), name="pid")
    plant = d.add(TransferFunction(numerator=[1.0], denominator=[1.0, 1.0]), name="plant")
    d.connect(d.add(Constant(value=1.0), name="ref"), (err, 0))
    d.chain(err, pid, d.add(Gain(gain=2.0), name="actuator"), plant)
    d.connect(plant, (err, 1))
    d.connect(plant, d.add(Scope()))
    g = plant_at(d, "pid")
    num, den = g.transfer_function()
    assert np.polyval(num, 0.0) / np.polyval(den, 0.0) == pytest.approx(2.0, rel=1e-6)  # 2 / (s + 1)
    np.testing.assert_allclose(g.poles(), [-1.0], atol=1e-6)
    assert "pid" not in " ".join(g.states)  # the controller's own states are not part of the plant


def test_tune_pid_meets_the_margins() -> None:
    from scipy import signal as sps

    from nervus.analysis import feedback, pid_transfer, tf_margins, tune_pid

    g = _third_order()
    for form in ("PI", "PID"):
        gains = tune_pid(g, form, phase_margin=60.0)
        cnum, cden = pid_transfer(gains.P, gains.I, gains.D, gains.N)
        lnum, lden = np.polymul(cnum, [1.0]), np.polymul(cden, [1.0, 3.0, 3.0, 1.0])
        m = tf_margins(lnum, lden)
        assert m.phase_margin_deg >= 59.9 and m.gain_margin_db >= 6.0
        assert 0.1 * gains.P * gains.crossover < gains.I  # real integral action (Ti = 4/wc)
        _t, y = sps.step(feedback(lnum, lden), T=np.linspace(0, 40, 4000))
        assert y[-1] == pytest.approx(1.0, abs=0.01) and y.max() < 1.1
    pid, pi = tune_pid(g, "PID"), tune_pid(g, "PI")
    assert pid.crossover > pi.crossover  # the derivative's phase lead allows a faster loop
    with pytest.raises(ValueError, match="phase margin"):
        tune_pid(g, "PI", phase_margin=170.0)  # a PI always lags 14° at crossover


@pytest.mark.parametrize(
    ("num", "den", "pm", "gm_db"),
    [
        ([1.0, 2.0], [1.0, 1.0, 0.0, 0.0], -19.27, np.inf),  # (s+2)/(s²(s+1)): unstable, phase below -180°
        ([1.0, 2.0, 1.0], [1.0, 0.0, 0.0, 0.0], 21.39, -6.02),  # (s+1)²/s³: conditionally stable
        ([-2.0], [1.0, 1.0], -60.0, np.inf),  # negative gain: closed-loop pole at +1
        ([10.0], [0.1, 1.1, 1.0, 0.0], 1.58, 0.83),  # 10/(s(s+1)(0.1s+1)): unchanged, as in docs/analysis.md
    ],
)
def test_margins_with_poles_at_the_origin_and_negative_gain(num, den, pm, gm_db) -> None:  # type: ignore[no-untyped-def]
    # The phase used to start on np.angle's branch (+180° for a double integrator), which gave margins of 300°+.
    from nervus.analysis import tf_margins

    m = tf_margins(np.array(num), np.array(den))
    assert m.phase_margin_deg == pytest.approx(pm, abs=0.02)
    assert -180.0 < m.phase_margin_deg <= 180.0
    if np.isfinite(gm_db):
        assert m.gain_margin_db == pytest.approx(gm_db, abs=0.02)
    else:
        assert m.gain_margin_db == np.inf


def test_bode_phase_of_a_double_integrator_starts_at_minus_180() -> None:
    from scipy import signal as sps

    from nervus.analysis import LinearModel

    a, b, c, d = sps.tf2ss([1.0], [1.0, 1.0, 0.0, 0.0])  # 1/(s²(s+1))
    _w, _mag, phase = LinearModel(a, b, c, d, ["u"], ["y"]).bode(np.array([1e-3, 1.0, 100.0]))
    np.testing.assert_allclose(phase, [-180.06, -225.0, -269.43], atol=0.01)
