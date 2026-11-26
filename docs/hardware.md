# Hardware: what to buy

You don't need any hardware to use Nervus: models, simulation, tests and code generation run on the laptop.
Hardware comes in when the generated code should run on a real microcontroller, like an ECU, or when Nervus talks to
a real car or truck. This page says what to buy for each step. Nervus will support **several development
boards**, so you can use the board you have (see the [roadmap](roadmap.md)).

## A CAN adapter for the laptop

To see and talk to a CAN bus (a car, a truck, a development board), the laptop needs a USB-to-CAN adapter. Nervus
uses [python-can](https://python-can.readthedocs.io), which supports most adapters.

| Adapter | About | Notes |
|---|---|---|
| **CANable 2.0** (or another *candleLight* adapter) | €35 | CAN and CAN FD, open source; python-can interface `gs_usb` or `slcan` |
| PEAK PCAN-USB | €250 | professional; drivers for Windows, macOS (via MacCAN) and Linux |
| Kvaser Leaf | €300 | professional |
| ELM327 (OBD-II only) | €10–30 | reads OBD-II values from a car, but is not a general CAN adapter |

## A development board

A development board runs the generated C code like an ECU and talks CAN. Two kinds will be supported first:

| | **STM32 Nucleo-G474RE** | **ESP32 development board** |
|---|---|---|
| Price | about €25 | about €8 |
| CAN | 3 controllers, **CAN FD** (needed for newer cars) | 1 controller (TWAI), classic CAN only (fine for trucks/J1939 and most cars) |
| Like a real ECU | yes: the same kind of chip, fixed timing | less: timing varies more |
| Flashing and debugging | a debugger is built in: one USB cable flashes and steps through the code | flashing over USB; debugging is more limited |
| Extras | | Wi-Fi and Bluetooth |
| Best for | learning real ECU work | cheap experiments, wireless projects |

**If you buy one, take the Nucleo-G474RE.** Projects for both are made with **Code → Board Project…**
([Development boards](boards.md)).

What to look at when you choose a board:

- **CAN FD or classic CAN.** Newer cars use CAN FD; trucks (J1939) and older cars use classic CAN.
- **A built-in debugger**, so you can step through the code on the board.
- **3.3 V.** The CAN transceiver must work at the board's voltage (3.3 V for both boards above).
- **Documentation and community**: both boards above are widely used.

## The rest of the shopping list

1. **A CAN transceiver module, 3.3 V (about €5–10)**, for example an SN65HVD230 board. The microcontroller
   speaks CAN but cannot drive the bus wires; the transceiver does. Some boards have one built in (for example
   the LilyGO T-CAN485 with an ESP32): check the listing.
2. **A USB data cable that fits the board.** Some cables only charge, and those won't work.
3. **Jumper wires** (female to female). Two of them, CAN-H and CAN-L, connect the board's transceiver to the
   laptop's adapter, plus a ground wire.
4. **Two 120 Ω resistors**, one at each end of the bus. Most transceiver modules have one fitted, and the
   CANable has a switch for its own, so check before you buy.
5. Later, for the bench tutorials: a potentiometer (as a pedal), and a small DC motor with a motor driver board.

That comes to about **€75 with the Nucleo** or about **€55 with an ESP32**, adapter included. The compilers and
flashing tools are free.

```
laptop ──USB── CANable ──CAN-H/CAN-L/GND── transceiver ──── development board ──USB── laptop
                (120 Ω)                     (120 Ω)                             (power, flashing)
```

## Connecting to a car or truck

To read OBD-II from a car, an **ELM327** adapter is enough (see [OBD-II](obd.md)). To log the raw CAN bus, a
CAN adapter with an **OBD-II to DB9 cable** (cars) or a **J1939 9-pin Deutsch cable** (trucks) works; check which
pins the adapter uses for CAN-H and CAN-L before you buy the cable. Nervus only *reads* from a vehicle (see
[Safety and the law](roadmap.md#safety-and-the-law)).
