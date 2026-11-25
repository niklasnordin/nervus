"""Importing Simulink .slx models."""

import zipfile
from pathlib import Path

import numpy as np
import pytest

from nervus import simulate
from nervus.simulink import matlab_expr, read_slx

ROOT_SYSTEM = """<?xml version="1.0" encoding="utf-8"?>
<System>
  <P Name="Location">[-1, -8, 1921, 1033]</P>
  <Block BlockType="Step" Name="Step" SID="1">
    <P Name="Position">[30, 85, 60, 115]</P>
    <P Name="Time">0</P>
  </Block>
  <Block BlockType="Sum" Name="Sum" SID="2">
    <P Name="Ports">[2, 1]</P>
    <P Name="Position">[100, 90, 120, 110]</P>
    <P Name="IconShape">round</P>
    <P Name="Inputs">|+-</P>
  </Block>
  <Block BlockType="Gain" Name="Controller Gain" SID="3">
    <P Name="Position">[160, 85, 190, 115]</P>
    <P Name="Gain">Kp</P>
  </Block>
  <Block BlockType="TransferFcn" Name="Plant" SID="4">
    <P Name="Position">[230, 82, 290, 118]</P>
    <P Name="Denominator">[tau 1]</P>
  </Block>
  <Block BlockType="Outport" Name="y" SID="5">
    <P Name="Position">[360, 93, 390, 107]</P>
  </Block>
  <Block BlockType="SubSystem" Name="Scaled" SID="6">
    <P Name="Ports">[1, 1]</P>
    <P Name="Position">[230, 160, 290, 200]</P>
    <Mask>
      <MaskParameter Name="K" Type="edit"><Prompt>Gain</Prompt><Value>2^2</Value></MaskParameter>
    </Mask>
    <System Ref="system_6"/>
  </Block>
  <Block BlockType="Outport" Name="y4" SID="9">
    <P Name="Position">[360, 173, 390, 187]</P>
    <P Name="Port">2</P>
  </Block>
  <Block BlockType="S-Function" Name="Legacy driver" SID="10">
    <P Name="Ports">[1, 1]</P>
    <P Name="Position">[160, 240, 220, 270]</P>
  </Block>
  <Block BlockType="Terminator" Name="Terminator" SID="11">
    <P Name="Position">[260, 245, 280, 265]</P>
  </Block>
  <Line>
    <P Name="Src">1#out:1</P>
    <P Name="Dst">2#in:1</P>
  </Line>
  <Line>
    <P Name="Src">2#out:1</P>
    <P Name="Dst">3#in:1</P>
  </Line>
  <Line>
    <P Name="Src">3#out:1</P>
    <P Name="Dst">4#in:1</P>
  </Line>
  <Line>
    <P Name="Src">4#out:1</P>
    <Branch><P Name="Dst">5#in:1</P></Branch>
    <Branch><P Name="Dst">2#in:2</P></Branch>
    <Branch><P Name="Dst">6#in:1</P></Branch>
    <Branch><P Name="Dst">10#in:1</P></Branch>
  </Line>
  <Line>
    <P Name="Src">6#out:1</P>
    <P Name="Dst">9#in:1</P>
  </Line>
  <Line>
    <P Name="Src">10#out:1</P>
    <P Name="Dst">11#in:1</P>
  </Line>
</System>
"""

SUB_SYSTEM = """<?xml version="1.0" encoding="utf-8"?>
<System>
  <Block BlockType="Inport" Name="u" SID="7"><P Name="Position">[20, 40, 50, 55]</P></Block>
  <Block BlockType="Gain" Name="Gain" SID="8"><P Name="Position">[90, 35, 120, 65]</P><P Name="Gain">K</P></Block>
  <Block BlockType="Outport" Name="v" SID="12"><P Name="Position">[160, 40, 190, 55]</P></Block>
  <Line><P Name="Src">7#out:1</P><P Name="Dst">8#in:1</P></Line>
  <Line><P Name="Src">8#out:1</P><P Name="Dst">12#in:1</P></Line>
</System>
"""


def _slx(tmp_path: Path) -> Path:
    path = tmp_path / "loop.slx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("simulink/blockdiagram.xml", "<ModelInformation/>")
        z.writestr("simulink/systems/system_root.xml", ROOT_SYSTEM)
        z.writestr("simulink/systems/system_6.xml", SUB_SYSTEM)
    return path


def test_import_and_simulate(tmp_path: Path) -> None:
    d = read_slx(_slx(tmp_path))
    names = {b.name for b in d.blocks}
    assert {"Step", "Sum", "Controller_Gain", "Plant", "y", "Scaled", "Legacy_driver"} <= names
    assert d.block("Sum").params["signs"] == "+-" and d.block("Controller_Gain").param_source("gain") == "Kp"
    assert d.block("Plant").param_source("denominator") == "[tau 1]"
    assert d.block("Step").meta["position"] == [30.0, 90.0]  # snapped to the grid
    report = d.meta["simulink_report"]
    assert "Legacy driver (S-Function)" in report and "Kp" in report and "tau" in report
    assert d.block("Scaled").mask["K"] == 4.0  # 2^2 in the mask
    # Give the workspace variables their values: a first-order loop, y(t) = Kp/(1+Kp) (1 - exp(-(1+Kp) t / tau)).
    d.workspace["Kp"] = 3.0
    d.workspace["tau"] = 0.5
    res = simulate(d, 2.0)
    y = np.ravel(res["y"])
    expected = 0.75 * (1 - np.exp(-4 * res.t / 0.5))
    assert np.max(np.abs(y - expected)) < 1e-3
    assert np.ravel(res["y4"])[-1] == pytest.approx(4 * 0.75, abs=1e-3)  # the masked subsystem: K = 4


@pytest.mark.parametrize(("matlab", "nervus"), [
    ("2^3", "2**3"), ("a.*b./c", "a*b/c"), ("x.^2", "x**2"), ("[0:0.5:2]", "[0 0.5 1 1.5 2]"),
    ("[1:3]", "[1 2 3]"), ("[1 2; 3 4]", "[1 2; 3 4]"), ("K*pi", "K*pi"),
])  # fmt: skip
def test_matlab_expressions(matlab: str, nervus: str) -> None:
    assert matlab_expr(matlab) == nervus


def test_not_an_slx(tmp_path: Path) -> None:
    from nervus.core.errors import NervusError

    bad = tmp_path / "model.slx"
    bad.write_text('Model { Name "x" }')
    with pytest.raises(NervusError, match=r"not an \.slx file"):
        read_slx(bad)


MDL = """Model {
  Name                    "loop"
  Version                 7.9
  System {
    Name                    "loop"
    Location                [-1, -8, 1921, 1033]
    Block {
      BlockType               Step
      Name                    "Step"
      SID                     "1"
      Position                [30, 85, 60, 115]
      Time                    "0"
    }
    Block {
      BlockType               Sum
      Name                    "Sum"
      SID                     "2"
      Ports                   [2, 1]
      Position                [100, 90, 120, 110]
      Inputs                  "|+-"
    }
    Block {
      BlockType               Gain
      Name                    "Controller Gain"
      SID                     "3"
      Position                [160, 85, 190, 115]
      Gain                    "K"
      "p"
    }
    Block {
      BlockType               TransferFcn
      Name                    "Plant"
      SID                     "4"
      Position                [230, 82, 290, 118]
      Denominator             "[tau 1]"
    }
    Block {
      BlockType               Outport
      Name                    "y"
      SID                     "5"
      Position                [360, 93, 390, 107]
    }
    Block {
      BlockType               SubSystem
      Name                    "Scaled"
      SID                     "6"
      Ports                   [1, 1]
      Position                [230, 160, 290, 200]
      MaskNames               "K"
      MaskValueString         "2^2"
      System {
        Name            "Scaled"
        Block {
          BlockType       Inport
          Name            "u"
          SID             "7"
        }
        Block {
          BlockType       Gain
          Name            "Gain"
          SID             "8"
          Gain            "K"
        }
        Block {
          BlockType       Outport
          Name            "v"
          SID             "12"
        }
        Line {
          SrcBlock        "u"
          SrcPort         1
          DstBlock        "Gain"
          DstPort         1
        }
        Line {
          SrcBlock        "Gain"
          SrcPort         1
          DstBlock        "v"
          DstPort         1
        }
      }
    }
    Block {
      BlockType               Outport
      Name                    "y4"
      SID                     "9"
      Port                    "2"
    }
    Line {
      SrcBlock                "Step"
      SrcPort                 1
      DstBlock                "Sum"
      DstPort                 1
    }
    Line {
      SrcBlock                "Sum"
      SrcPort                 1
      DstBlock                "Controller Gain"
      DstPort                 1
    }
    Line {
      SrcBlock                "Controller Gain"
      SrcPort                 1
      DstBlock                "Plant"
      DstPort                 1
    }
    Line {
      SrcBlock                "Plant"
      SrcPort                 1
      Branch {
        DstBlock                "y"
        DstPort                 1
      }
      Branch {
        DstBlock                "Sum"
        DstPort                 2
      }
      Branch {
        DstBlock                "Scaled"
        DstPort                 1
      }
    }
    Line {
      SrcBlock                "Scaled"
      SrcPort                 1
      DstBlock                "y4"
      DstPort                 1
    }
  }
}
"""


@pytest.mark.parametrize("separator", [" ", "\t\t"])
def test_mdl_import(tmp_path: Path, separator: str) -> None:
    """Simulink writes tabs between the keys and the values; both are read."""
    import re

    from nervus.simulink import read_model

    path = tmp_path / "loop.mdl"
    path.write_text(re.sub(r"(?m)^(\s*\w+) +", lambda m: m.group(1) + separator, MDL))
    d = read_model(path)
    assert d.block("Controller_Gain").param_source("gain") == "Kp"  # a string continued on the next line
    assert d.block("Scaled").mask["K"] == 4.0
    d.workspace["Kp"] = 3.0
    d.workspace["tau"] = 0.5
    res = simulate(d, 2.0)
    expected = 0.75 * (1 - np.exp(-4 * res.t / 0.5))
    assert np.max(np.abs(np.ravel(res["y"]) - expected)) < 1e-3
    assert np.ravel(res["y4"])[-1] == pytest.approx(3.0, abs=1e-3)


def test_not_an_mdl(tmp_path: Path) -> None:
    from nervus.core.errors import NervusError
    from nervus.simulink import read_model

    bad = tmp_path / "x.mdl"
    bad.write_text("hello")
    with pytest.raises(NervusError, match=r"not a Simulink \.mdl file"):
        read_model(bad)


def _archive(tmp_path: Path, systems: dict[str, str]) -> Path:
    path = tmp_path / "model.slx"
    with zipfile.ZipFile(path, "w") as z:
        for name, xml in systems.items():
            z.writestr(f"simulink/systems/{name}.xml", xml)
    return path


@pytest.mark.parametrize(("systems", "message"), [
    ({"system_root": "<System><Block BlockType='Gain' Name='g' SID='1'>"}, "cannot read"),
    ({"system_root": "<System><Block BlockType='SubSystem' Name='s' SID='1'><System Ref='system_1'/></Block></System>",
      "system_1": "<System><Block BlockType='SubSystem' Name='t' SID='2'><System Ref='system_1'/></Block></System>"},
     "contains itself"),
    ({"system_root": "<System><Block BlockType='Inport' Name='u' SID='1'><P Name='Port'>[1]</P></Block></System>"},
     "port number"),
])  # fmt: skip
def test_damaged_slx_files(systems: dict[str, str], message: str, tmp_path: Path) -> None:
    """Regression: bad XML, a subsystem that contains itself and a bad port number raised ParseError,
    RecursionError and ValueError."""
    from nervus.core.errors import NervusError

    with pytest.raises(NervusError, match=message):
        read_slx(_archive(tmp_path, systems))


def test_same_subsystem_contents_twice(tmp_path: Path) -> None:
    """Two subsystems may refer to the same system file (only a system inside itself is refused)."""
    sub = "<Block BlockType='SubSystem' Name='{}' SID='{}'><System Ref='system_9'/></Block>"
    root = f"<System>{sub.format('a', 1)}{sub.format('b', 2)}</System>"
    d = read_slx(_archive(tmp_path, {"system_root": root, "system_9": "<System/>"}))
    assert {b.name for b in d.blocks} == {"a", "b"}


def test_ranges_keep_their_digits() -> None:
    assert matlab_expr("10000:0.25:10001") == "10000 10000.25 10000.5 10000.75 10001"
    assert matlab_expr("1000000:1:1000002") == "1000000 1000001 1000002"
    assert matlab_expr("0:0.1:0.3") == "0 0.1 0.2 0.3"


def test_settings_that_are_not_imported_are_reported(tmp_path: Path) -> None:
    root = """<System>
      <Block BlockType="DiscreteIntegrator" Name="acc" SID="1">
        <P Name="IntegratorMethod">Accumulation: Forward Euler</P><P Name="LimitOutput">on</P>
      </Block>
      <Block BlockType="Lookup_n-D" Name="map" SID="2"><P Name="NumberOfTableDimensions">1</P></Block>
      <Block BlockType="Lookup_n-D" Name="clipped" SID="3">
        <P Name="NumberOfTableDimensions">1</P><P Name="ExtrapMethod">Clip</P>
      </Block>
    </System>"""
    report = read_slx(_archive(tmp_path, {"system_root": root})).meta["simulink_report"]
    assert "acc: integrator method 'Accumulation: Forward Euler' not imported" in report
    assert "acc: output limits not imported" in report
    assert "map: extrapolation '(Simulink default)' not imported" in report
    assert "clipped:" not in report
