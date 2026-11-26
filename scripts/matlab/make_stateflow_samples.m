function make_stateflow_samples()
%MAKE_STATEFLOW_SAMPLES  Sample Simulink models with Stateflow charts, to build Nervus's Stateflow import.
%
%   Run it in MATLAB (with Simulink and Stateflow) from any folder:
%
%       >> cd <the nervus repository>/scripts/matlab
%       >> make_stateflow_samples
%
%   It writes, next to this file, in the folder "stateflow_samples":
%
%       sf_lamp.slx, sf_lamp.mdl        a chart with three states, guards, entry and during actions
%       sf_gearbox.slx, sf_gearbox.mdl  hierarchy, parallel states, after(), a junction, exit actions
%       sf_lamp.csv, sf_gearbox.csv     what Simulink computes for each model (time and the outputs)
%       versions.txt                    the MATLAB, Simulink and Stateflow versions
%
%   Commit that folder (or send it). Nervus's tests then check that the imported charts compute the same
%   outputs as Simulink did. Nothing here needs a licence beyond Simulink and Stateflow.

here = fileparts(mfilename('fullpath'));
out = fullfile(here, 'stateflow_samples');
if ~exist(out, 'dir')
    mkdir(out);
end

make_lamp(out);
make_gearbox(out);

fid = fopen(fullfile(out, 'versions.txt'), 'w');
for product = {'MATLAB', 'Simulink', 'Stateflow'}
    v = ver(product{1});
    if ~isempty(v)
        fprintf(fid, '%s %s %s\n', v(1).Name, v(1).Version, v(1).Release);
    end
end
fclose(fid);
fprintf('Done: the files are in %s\n', out);
end


function make_lamp(out)
% A warning lamp: Off -> On above 10 km/h, On -> Blinking above 50 km/h, back to Off below 5 km/h.
name = 'sf_lamp';
ch = new_model(name, 'Lamp');

data(ch, 'speed', 'Input', 'double');
data(ch, 'lamp', 'Output', 'double');
data(ch, 'count', 'Output', 'double');

off = state(ch, 'Off', [40 40 120 70], sprintf('Off\nentry: lamp = 0;'));
on = state(ch, 'On', [240 40 120 70], sprintf('On\nentry: lamp = 1;'));
blink = state(ch, 'Blinking', [240 180 160 90], ...
    sprintf('Blinking\nentry: lamp = 2;\nduring: count = count + 1;'));

default_transition(ch, off);
transition(ch, off, on, '[speed > 10]');
transition(ch, on, blink, '[speed > 50]');
transition(ch, blink, off, '[speed < 5]');
transition(ch, on, off, '[speed < 5]');

% speed: up from 0 to 80 km/h in 4 s, then down to 0 at 8 s
add_block('simulink/Sources/Repeating Sequence', [name '/Speed'], ...
    'rep_seq_t', '[0 4 8 10]', 'rep_seq_y', '[0 80 0 0]', 'Position', [40 90 90 130]);
add_line(name, 'Speed/1', 'Lamp/1');
finish(name, 'Lamp', {'lamp', 'count'}, out);
end


function make_gearbox(out)
% Parallel states: a gearbox (Parked, and Driving with Low and High inside) and a monitor of the shifts.
name = 'sf_gearbox';
ch = new_model(name, 'Gearbox');
ch.Decomposition = 'PARALLEL_AND';

data(ch, 'pedal', 'Input', 'double');
data(ch, 'gear', 'Output', 'double');
data(ch, 'shifts', 'Output', 'double');
data(ch, 'warning', 'Output', 'double');

box = state(ch, 'Gear', [30 30 460 320], 'Gear');
monitor = state(ch, 'Monitor', [520 30 260 320], 'Monitor');

% Inside Gear (exclusive): Parked, and Driving with Low and High inside it.
parked = state(box, 'Parked', [60 80 110 60], sprintf('Parked\nentry: gear = 0;'));
driving = state(box, 'Driving', [220 70 250 260], sprintf('Driving\nexit: shifts = shifts + 1;'));
low = state(driving, 'Low', [260 120 100 50], sprintf('Low\nentry: gear = 1;'));
high = state(driving, 'High', [260 230 100 50], sprintf('High\nentry: gear = 2;'));

default_transition(box, parked);
default_transition(driving, low);
transition(box, parked, driving, '[pedal > 0.1]');
transition(driving, low, high, 'after(1, sec)[pedal > 0.5]');
transition(driving, high, low, '[pedal < 0.3]');
% A junction: leaving Driving through it, back to Parked when the pedal is released.
j = Stateflow.Junction(box);
j.Position.Center = [140 300];
transition(box, driving, j, '[pedal < 0.05]');
transition(box, j, parked, '');

% Inside Monitor (exclusive): Ok and Busy.
ok = state(monitor, 'Ok', [560 80 110 60], sprintf('Ok\nentry: warning = 0;'));
busy = state(monitor, 'Busy', [560 220 110 60], sprintf('Busy\nentry: warning = 1;'));
default_transition(monitor, ok);
transition(monitor, ok, busy, '[shifts >= 2]');
transition(monitor, busy, ok, 'after(2, sec)');

add_block('simulink/Sources/Repeating Sequence', [name '/Pedal'], ...
    'rep_seq_t', '[0 1 3 5 6 8 10]', 'rep_seq_y', '[0 0.8 0.8 0.2 0 0.6 0]', 'Position', [40 90 90 130]);
add_line(name, 'Pedal/1', 'Gearbox/1');
finish(name, 'Gearbox', {'gear', 'shifts', 'warning'}, out);
end


% ------------------------------------------------------------------------------------------- helpers

function ch = new_model(name, chart)
if bdIsLoaded(name)
    close_system(name, 0);
end
new_system(name);
load_system(name);
add_block('sflib/Chart', [name '/' chart], 'Position', [160 70 300 170]);
rt = sfroot;
mdl = rt.find('-isa', 'Simulink.BlockDiagram', 'Name', name);
ch = mdl.find('-isa', 'Stateflow.Chart');
ch.ActionLanguage = 'MATLAB';
ch.ChartUpdate = 'DISCRETE';
ch.SampleTime = '0.01';
set_param(name, 'Solver', 'FixedStepDiscrete', 'FixedStep', '0.01', 'StopTime', '10');
end


function d = data(ch, name, scope, type)
d = Stateflow.Data(ch);
d.Name = name;
d.Scope = scope;
d.DataType = type;
end


function s = state(parent, name, position, label)
% parent: the chart, or the state this one is inside (positions are in chart coordinates)
s = Stateflow.State(parent);
s.Name = name;
s.Position = position;
s.LabelString = label;
end


function t = transition(parent, src, dst, label)
% parent: the chart or state that holds both ends
t = Stateflow.Transition(parent);
t.Source = src;
t.Destination = dst;
t.LabelString = label;
end


function default_transition(parent, dst)
t = Stateflow.Transition(parent);
t.Destination = dst;
t.DestinationOClock = 0;
x = dst.Position(1) + dst.Position(3) / 2;
y = dst.Position(2) - 25;
t.SourceEndpoint = [x y];
t.Midpoint = [x y + 12];
end


function finish(name, chart, outputs, out)
% Outports for the chart's outputs, simulate, write the outputs to CSV, save as .slx and .mdl.
for k = 1:numel(outputs)
    add_block('simulink/Sinks/Out1', [name '/' outputs{k}], 'Position', [400 60 + 50 * k 430 74 + 50 * k]);
    add_line(name, [chart '/' num2str(k)], [outputs{k} '/1']);
end
Simulink.BlockDiagram.arrangeSystem(name);
res = sim(name, 'SaveTime', 'on', 'TimeSaveName', 'tout', 'SaveOutput', 'on', ...
    'OutputSaveName', 'yout', 'SaveFormat', 'Array', 'ReturnWorkspaceOutputs', 'on');
csv = fullfile(out, [name '.csv']);
fid = fopen(csv, 'w');
fprintf(fid, '%s\n', strjoin([{'t'}, outputs], ','));
fclose(fid);
writematrix([res.tout res.yout], csv, 'WriteMode', 'append');
save_system(name, fullfile(out, [name '.slx']));
save_system(name, fullfile(out, [name '.mdl']));
close_system(name, 0);
fprintf('  %s: %d time points\n', name, numel(res.tout));
end
