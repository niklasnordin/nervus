# Help wanted

Things where Nervus needs something only a user can provide.

## Stateflow sample models (needs MATLAB)

To import Simulink models with **Stateflow** charts, the tests need real files saved by Simulink, with what
Simulink computes for them. If you have MATLAB with Simulink and Stateflow:

1. In MATLAB, go to the folder `scripts/matlab` of the Nervus repository (`cd <the nervus folder>/scripts/matlab`).
2. Run `make_stateflow_samples`. It builds two small models (a lamp with three states; a gearbox with states
   inside states, parallel states, timers and a junction), simulates them, and saves both models and Simulink's
   results in a new folder, `stateflow_samples`.
3. Open a pull request with that folder, or attach it as a zip file to an
   [issue](https://github.com/ikjbi/nervus/issues).

If the script stops with an error, report the error message (releases differ in small ways). The
[README in scripts/matlab](https://github.com/ikjbi/nervus/blob/main/scripts/matlab/README.md) also explains how to
build the lamp model by hand.

## Logs from your scanner app

Nervus reads [logs from OBD-II scanner apps and CAN loggers](../obd.md#logs-from-scanner-apps-and-can-loggers).
If a log from your app does not open, attach a short piece of it to an issue (remove the GPS columns if you
like).

## A test on your hardware

Once the [development-board targets](../roadmap.md) are out: try them on your board and report what happens. See
[Hardware](../hardware.md) for what to buy.
