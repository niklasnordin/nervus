# Roadmap to Nervus 1.0

Nervus 0.1 models, simulates and tests ECU software, and generates its C code. **Nervus 1.0** closes the loop to the
real vehicle: it also **reads from and writes to a real ECU**, measuring and calibrating it while it runs, flashing
its software and reading its diagnostics, like ETAS INCA or Vector CANape. A car or truck function can then be
developed in one tool from the first model to the calibrated ECU in the vehicle.

The detailed task list is [plan.md](https://github.com/ikjbi/nervus/blob/main/plan.md); this page groups it into
releases. Each release is usable on its own.

## 0.1: model, simulate, generate (released)

Block diagrams, physical models, state charts, test cases with coverage and MC/DC, requirements, CAN and OBD-II,
C code generation (MISRA C, fixed point, integer-only code, reusable functions, FMI), an XCP slave in the model,
A2L export, Simulink `.slx` import, 33 tutorials. [Changelog](https://github.com/ikjbi/nervus/blob/main/CHANGELOG.md).

## 0.2: reach and polish

- **Standalone installers** for macOS, Windows and Linux (no Python needed).
- **Versioned documentation**: one site per release.
- Simulink import: older `.mdl` files, and Stateflow charts as State Chart blocks.
- **Faster simulation** for large models (compiled execution of the model).
- What users report: bugs, missing blocks, unclear tutorials.

## 0.3: real hardware

- **A development-board target** (an STM32 or ESP32 with CAN): the generated code with a scheduler template and
  hardware drivers, built and flashed from Nervus.
- **Processor-in-the-loop (PIL)**: the generated code runs on the board, the plant in Nervus, compared step by step
  with the simulation.
- **Hardware-in-the-loop (HIL)**: the plant model in real time on the PC, talking CAN to a real ECU.
- The XCP slave on the board, so the board can be measured and calibrated like a production ECU.

## 0.4 to 0.6: measurement and calibration (the first INCA part)

- **A2L import of any ECU's description** (not only Nervus's own): measurements, characteristics (values, curves,
  maps with their axes), conversion methods, record layouts, memory segments.
- **XCP master** over CAN, CAN FD and Ethernet (UDP and TCP), and CCP for older ECUs: connect, seed and key, read and
  write memory, DAQ lists with time stamps at several rates.
- **Measurement windows**: oscilloscope, numeric, bar and state displays of the ECU's variables while it runs; recording
  to MDF4 with triggers; the recordings open in the Data Inspector next to simulations.
- **Calibration editors**: values, curves and maps as tables and 3-D surfaces, changed live in the ECU, with
  **working and reference pages** (switch between the calibration under test and the safe one in one click).
- The same windows work against the model (simulation), the development board and a production ECU.

## 0.7 and 0.8: data sets, flashing and diagnostics

- **Calibration data sets**: read and write HEX and S19 files and calibration files (DCM, CDF/CDFX); compare and merge
  two data sets; a history of changes with who changed what and why.
- **Flashing** over UDS (ISO 14229) through the ECU's bootloader: program, verify, checksums, and back to a known
  software if something goes wrong.
- **Diagnostics** over UDS and KWP2000: read and clear trouble codes with their freeze frames, read data by identifier,
  run routines.

## 0.9: release candidate

- **Experiments** saved as files: the ECU connection, the windows, the variables and their layout, so a session can be
  opened again on the next day or by a colleague.
- A **stable Python API** for scripting measurements, calibrations and tests against the real ECU (automated
  calibration and end-of-line style tests).
- **Safety**: write-protection per memory area, plausibility limits for every calibration value (from the A2L file),
  a confirmation before flashing, and a log of every write.
- Tutorials on a bench: a development board with a small plant (a DC motor or a throttle), measured, calibrated and
  flashed from Nervus.
- Feedback from users who try it on their own benches.

## 1.0: what it means

Nervus 1.0 is released when:

1. **A real ECU can be read and written**: measurement (DAQ) and calibration over XCP on CAN, CAN FD and Ethernet, and
   flashing and diagnostics over UDS, tested against at least two different ECUs or boards.
2. **The file formats are stable**: models (`.nervus`), experiments and calibration data sets from 1.0 open in every
   later 1.x release.
3. **The Python API is stable**: it follows semantic versioning, so scripts written for 1.0 keep working in 1.x.
4. **Everything is documented** in the user guide and the in-app help, with tutorials from the first model to the
   calibrated ECU on a bench.
5. **It installs without Python** on macOS, Windows and Linux.

## Decided along the way

- **Inside Nervus or a separate application?** The measurement and calibration tool can be a workspace inside
  Nervus or its own application that shares Nervus's A2L, XCP and CAN code. *Recommendation:* one application with a
  **Calibration** workspace, because the same windows then work against the model, a board and the real ECU, and a
  calibration found on the vehicle can go back into the model in one step. To be decided before 0.4.
- **Which hardware?** CAN interfaces through python-can (PEAK, Kvaser, Vector, SocketCAN, and others); a first
  development board for the tutorials.

## Safety and the law

Writing to an ECU changes how a vehicle behaves. Nervus's calibration and flashing features are for **development
work on test benches and test vehicles** by people who know what they change. Changing the software or calibration
of a vehicle used on public roads can make it unsafe, and changes that affect emissions or safety systems are illegal
in most countries. Nervus will warn, limit and log, but the responsibility stays with the user.

## Help wanted

Contributions are welcome at every step: try a release on your own hardware and report what happens, add a block or
a tutorial, or take on one of the items above. See
[CONTRIBUTING.md](https://github.com/ikjbi/nervus/blob/main/CONTRIBUTING.md); merged contributions are credited in
**Help ▸ About Nervus ▸ Contributors**.
