# Development boards

**Code → Board Project…** turns the generated code of a model (usually its `ECU` subsystem) into a complete
project for a development board with CAN. The board then runs the code like an ECU, and the
[Calibration window](calibration.md) measures and calibrates it over XCP on CAN, as it would a production ECU.
What to buy: [Hardware](https://ikjbi.github.io/nervus/latest/hardware/).

![](figures/board-project.png)

| Board | CAN pins | Tools to build and flash |
|---|---|---|
| **STM32 Nucleo-G474RE** | FDCAN1: PA12 (TX), PA11 (RX) | arm-none-eabi-gcc, STM32CubeG4, STM32CubeProgrammer (flashes over the Nucleo's USB cable) |
| **ESP32** (ESP-IDF) | TWAI: GPIO 5 (TX), GPIO 4 (RX) | ESP-IDF (`idf.py build flash`) |
| **This computer** | none: a test drives the CAN frames | your C compiler and `make` (for trying a project without hardware) |

Every board needs a 3.3 V **CAN transceiver** between its CAN pins and the bus.

> **Not tested on hardware yet:** The board layers follow ST's and Espressif's published APIs, and the portable parts (the main loop, the XCP slave, the generated code) are tested on this computer. They have not run on a real board yet. If you try one, please report what happens ([Help wanted](https://ikjbi.github.io/nervus/latest/developer/help-wanted/)).

## What a project contains

| File | What it does |
|---|---|
| `ECU.c`, `ECU.h` | the generated code (as in [C code generation](codegen.md)) |
| `main.c` | initialises the board, CAN and the model; calls the step function every base period; answers XCP |
| `io.c` | **yours to edit**: reads the sensors into the model's inputs and drives the actuators from its outputs. Nervus never overwrites it. |
| `board.c`, `nervus_target.h` | the board's hardware layer: the clock, CAN send and receive |
| `nervus_xcp.c`, `nervus_xcp.h` | a small XCP-on-CAN slave in portable C: the tool can read the model's signals and states and change its calibration parameters, and nothing else |
| `ECU.a2l` | the A2L file with the board's XCP CAN identifiers and bit rate |
| `Makefile` / `CMakeLists.txt` | the board's build |

**Build** runs the board's build tool and then writes the addresses of the built program into the A2L file, so
the Calibration window (or INCA and CANape) finds every variable. **Flash** programs the board over USB. The
buttons are enabled when the tools are installed.

## Steps for a board

1. Install the board's tools (see the table above) and connect the board with a USB data cable.
2. **Code → Board Project…**: choose the subsystem, the board, the CAN bit rate and the XCP identifiers, and a
   folder. Press **Create project**.
3. Edit `io.c`: which ADC channel is the throttle sensor, which PWM drives the motor. Until you do, the inputs
   stay at their initial values.
4. **Build**, then **Flash**.
5. Connect the transceiver to the bus and a CAN adapter to the laptop. In **Tools → Calibration…** choose **XCP
   on CAN**, the project's A2L file and your adapter, and **Connect**.

## In Python

```python
from nervus.codegen.boards import create_project, build, fill_a2l_addresses

create_project(model.block("ECU"), "esp32", "ecu_esp32", request_id=0x7E0, response_id=0x7E8, bitrate=500000)
build("ecu_esp32", "esp32")
fill_a2l_addresses("ecu_esp32")
```

## Still to come

The XCP slave on the board answers polling (read and write); DAQ lists and the working and reference pages are
already in the [virtual ECU](calibration.md) and will follow on the boards. **Processor-in-the-loop** (the
board runs the code, the plant runs in Nervus, step by step) and **hardware-in-the-loop** (the plant in real
time against a real ECU) come next, once a board has been tested.
