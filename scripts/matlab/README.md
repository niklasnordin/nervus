# Sample models from MATLAB

Nervus imports Simulink models (`.slx`, `.mdl`). To add the **Stateflow** charts to the import, the tests need
real files saved by Simulink, with what Simulink computes for them.

## Make them

You need MATLAB with Simulink and Stateflow (any recent release).

1. Start MATLAB and go to this folder: `cd <the nervus folder>/scripts/matlab`
2. Run `make_stateflow_samples`
3. It writes the folder `stateflow_samples` next to this file:
   - `sf_lamp.slx` and `.mdl`: three states, guards, entry and during actions.
   - `sf_gearbox.slx` and `.mdl`: states inside states, parallel states, `after()`, a junction and exit actions.
   - A `.csv` file per model with Simulink's outputs.
   - `versions.txt` with the MATLAB release.
4. Commit the folder, or send it (a zip file is fine).

## If the script stops with an error

Send the error message. Releases differ in small ways, and the script cannot be tested without MATLAB.
Alternatively, build `sf_lamp` by hand:

1. Make a new model. Add a **Chart** from the Stateflow library and a **Repeating Sequence** with the time values
   `[0 4 8 10]` and output values `[0 80 0 0]`.
2. Open the chart and add three states:
   - `Off` with `entry: lamp = 0;`
   - `On` with `entry: lamp = 1;`
   - `Blinking` with `entry: lamp = 2;` and `during: count = count + 1;`
3. Add these transitions:
   - a default transition into `Off`
   - `Off` → `On` with `[speed > 10]`
   - `On` → `Blinking` with `[speed > 50]`
   - `Blinking` → `Off` with `[speed < 5]`
   - `On` → `Off` with `[speed < 5]`
4. In the Symbols pane, set the data:
   - `speed` is an input
   - `lamp` and `count` are outputs
5. Connect the Repeating Sequence to the chart's input and two Outports to its outputs.
6. Set the model's solver to fixed-step discrete with a step of 0.01 s and a stop time of 10 s. Set the chart's
   update method to discrete, with sample time 0.01.
7. Run the model, then save it as `sf_lamp.slx`. A screenshot of the chart and the scope also helps.
