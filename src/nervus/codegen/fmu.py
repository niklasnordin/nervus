"""Export a model (or a subsystem) as an FMI 2.0 co-simulation FMU.

An **FMU** (Functional Mock-up Unit) is the standard way to exchange models between tools: Simulink, Dymola,
CANoe, SimulationX, Python (fmpy) and many others can run it. It is a zip file with a description of its variables
(``modelDescription.xml``), a compiled library for the current platform (``binaries/``) and the C sources
(``sources/``), so other platforms can build it too.

    from nervus.codegen.fmu import export_fmu
    export_fmu(model.block("ECU"), "ECU.fmu")

The FMU is built from the generated C code: inputs and outputs are the In and Out blocks, the calibratable
parameters (workspace variables) are **tunable parameters**. Each ``fmi2DoStep`` runs the generated step function
once per base period (the communication step must be a whole number of base periods). The generated code keeps its
state in global variables, so one process can run one instance of an FMU at a time.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from nervus import __version__
from nervus.codegen.generator import CodegenError, GeneratedCode, generate_c
from nervus.codegen.sil import find_compiler
from nervus.core.datatypes import DOUBLE, SINGLE, DataType

_TYPES = {"real_T": "double", "real32_T": "float"}


def platform_folder() -> str:
    """The FMI platform name of this computer: darwin64, linux64 or win64."""
    if sys.platform == "darwin":
        return "darwin64"
    if sys.platform.startswith("win"):
        return "win64"
    return "linux64"


def _library_suffix() -> str:
    return {"darwin64": ".dylib", "win64": ".dll"}.get(platform_folder(), ".so")


def _variables(code: GeneratedCode) -> list[dict[str, Any]]:
    """Scalar variables with value references: inputs, outputs, tunable parameters (floating-point only). Each has
    the C type of its storage (``dtype``): the FMU exchanges physical values as ``double`` and converts."""
    out: list[dict[str, Any]] = []
    for kind, fields in (("input", code.inputs), ("output", code.outputs)):
        for field, width in fields:
            for k in range(width):
                out.append({"name": field if width == 1 else f"{field}[{k}]", "c": f"{code.name}_{'U' if kind == 'input' else 'Y'}.{field}[{k}]",
                            "causality": kind, "start": 0.0, "description": "",
                            "dtype": code.io_types.get(field, DOUBLE)})  # fmt: skip
    for field, size, init, c_type, comment in code.tunables:
        if c_type not in _TYPES:
            continue  # fixed-point parameters stay at their calibration in the FMU
        for k in range(size):
            out.append({"name": field if size == 1 else f"{field}[{k}]", "c": f"{code.name}_P.{field}[{k}]",
                        "causality": "parameter", "start": float(init[k]), "description": comment,
                        "dtype": SINGLE if c_type == "real32_T" else DOUBLE})  # fmt: skip
    for vr, v in enumerate(out, start=1):
        v["vr"] = vr
    return out


def model_description(code: GeneratedCode, guid: str, description: str = "") -> str:
    vs = _variables(code)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<fmiModelDescription fmiVersion="2.0" modelName="{escape(code.name)}" guid="{{{guid}}}" '
        f'description="{escape(description)}" generationTool="Nervus {__version__}" '
        'variableNamingConvention="structured" numberOfEventIndicators="0">',
        f'  <CoSimulation modelIdentifier="{escape(code.name)}" canHandleVariableCommunicationStepSize="true" '
        'canNotUseMemoryManagementFunctions="true"/>',
        f'  <DefaultExperiment startTime="0" stepSize="{code.base_period!r}"/>',
        "  <ModelVariables>",
    ]
    outputs = []
    for k, v in enumerate(vs, start=1):
        if v["causality"] == "parameter":
            attrs = 'causality="parameter" variability="tunable"'
            start = f' start="{v["start"]!r}"'
        elif v["causality"] == "input":
            attrs = 'causality="input" variability="discrete"'
            start = ' start="0.0"'
        else:
            attrs = 'causality="output" variability="discrete" initial="calculated"'
            start = ""
            outputs.append(k)
        desc = f' description="{escape(v["description"])}"' if v["description"] else ""
        lines.append(f'    <ScalarVariable name="{escape(v["name"])}" valueReference="{v["vr"]}" {attrs}{desc}>')
        lines.append(f"      <Real{start}/>")
        lines.append("    </ScalarVariable>")
    lines += ["  </ModelVariables>", "  <ModelStructure>"]
    for tag in ("Outputs", "InitialUnknowns"):
        if outputs:
            lines.append(f"    <{tag}>")
            lines += [f'      <Unknown index="{k}"/>' for k in outputs]
            lines.append(f"    </{tag}>")
    lines += ["  </ModelStructure>", "</fmiModelDescription>", ""]
    return "\n".join(lines)


def _to_c(dtype: DataType, value: str) -> str:
    """A physical ``double`` value as the stored C value of ``dtype`` (rounded to the nearest, saturated, as the
    simulation's In block quantizes)."""
    if dtype.is_double:
        return value
    if dtype.kind == "single":
        return f"(real32_T){value}"
    if dtype.kind == "boolean":
        return f"(({value}) != 0.0) ? 1u : 0u"
    stored = f"round(({value} - {dtype.bias!r}) / {dtype.slope!r})"
    return f"({dtype.c_type})nv_fmu_sat({stored}, {float(dtype.stored_min)!r}, {float(dtype.stored_max)!r})"


def _from_c(dtype: DataType, lvalue: str) -> str:
    """The physical ``double`` value of a stored C value of ``dtype``."""
    if dtype.is_double:
        return lvalue
    if dtype.kind == "fixed" and not dtype.is_integer:
        return f"((fmi2Real){lvalue} * {dtype.slope!r}) + {dtype.bias!r}"
    return f"(fmi2Real){lvalue}"


def wrapper_source(code: GeneratedCode, guid: str) -> str:
    """The FMI 2.0 co-simulation functions around the generated step function."""
    n = code.name
    vs = _variables(code)
    get_cases = "\n".join(f"        case {v['vr']}: *value = {_from_c(v['dtype'], v['c'])}; return 1;" for v in vs)
    set_cases = "\n".join(f"        case {v['vr']}: {v['c']} = {_to_c(v['dtype'], 'value')}; return 1;" for v in vs)
    sat = (
        "static double nv_fmu_sat(double x, double lo, double hi)\n{\n    return (x > hi) ? hi : ((x < lo) ? lo : x);\n}\n\n"
        if any(v["dtype"].kind == "fixed" for v in vs)
        else ""
    )
    return f'''/* FMI 2.0 co-simulation wrapper for {n}. Generated by Nervus {__version__}. Do not edit by hand. */
#include <math.h>
#include <stddef.h>
#include <string.h>
#include "{n}.h"

typedef void* fmi2Component;
typedef void* fmi2ComponentEnvironment;
typedef void* fmi2FMUstate;
typedef unsigned int fmi2ValueReference;
typedef double fmi2Real;
typedef int fmi2Integer;
typedef int fmi2Boolean;
typedef char fmi2Char;
typedef const fmi2Char* fmi2String;
typedef char fmi2Byte;
typedef enum {{ fmi2OK, fmi2Warning, fmi2Discard, fmi2Error, fmi2Fatal, fmi2Pending }} fmi2Status;
typedef enum {{ fmi2ModelExchange, fmi2CoSimulation }} fmi2Type;
typedef enum {{ fmi2DoStepStatus, fmi2PendingStatus, fmi2LastSuccessfulTime, fmi2Terminated }} fmi2StatusKind;
typedef struct {{
    void (*logger)(fmi2ComponentEnvironment, fmi2String, fmi2Status, fmi2String, fmi2String, ...);
    void* (*allocateMemory)(size_t, size_t);
    void (*freeMemory)(void*);
    void (*stepFinished)(fmi2ComponentEnvironment, fmi2Status);
    fmi2ComponentEnvironment componentEnvironment;
}} fmi2CallbackFunctions;

#if defined(_WIN32)
#define FMI2_EXPORT __declspec(dllexport)
#else
#define FMI2_EXPORT __attribute__((visibility("default")))
#endif

#define BASE_PERIOD {code.base_period!r}
static int in_use = 0;
static double now = 0.0;

{sat}/* Variables by value reference, as physical double values (converted from and to their C storage types) */
static int get_var(fmi2ValueReference vr, fmi2Real* value)
{{
    switch (vr) {{
{get_cases}
        default: return 0;
    }}
}}

static int set_var(fmi2ValueReference vr, fmi2Real value)
{{
    switch (vr) {{
{set_cases}
        default: return 0;
    }}
}}

FMI2_EXPORT const char* fmi2GetTypesPlatform(void) {{ return "default"; }}
FMI2_EXPORT const char* fmi2GetVersion(void) {{ return "2.0"; }}
FMI2_EXPORT fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean on, size_t n, const fmi2String cat[]) {{
    (void)c; (void)on; (void)n; (void)cat; return fmi2OK;
}}

FMI2_EXPORT fmi2Component fmi2Instantiate(fmi2String name, fmi2Type type, fmi2String guid, fmi2String location,
                                          const fmi2CallbackFunctions* functions, fmi2Boolean visible,
                                          fmi2Boolean logging)
{{
    (void)name; (void)location; (void)functions; (void)visible; (void)logging;
    if (type != fmi2CoSimulation || in_use || strcmp(guid, "{{{guid}}}") != 0) {{ return NULL; }}
    in_use = 1;
    now = 0.0;
    {n}_initialize();
    return (fmi2Component)&in_use;
}}

FMI2_EXPORT void fmi2FreeInstance(fmi2Component c) {{ (void)c; in_use = 0; }}
FMI2_EXPORT fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean tol_defined, fmi2Real tol, fmi2Real start,
                                           fmi2Boolean stop_defined, fmi2Real stop)
{{
    (void)c; (void)tol_defined; (void)tol; (void)stop_defined; (void)stop; now = start; return fmi2OK;
}}
FMI2_EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component c) {{ (void)c; return fmi2OK; }}
FMI2_EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component c) {{ (void)c; return fmi2OK; }}
FMI2_EXPORT fmi2Status fmi2Terminate(fmi2Component c) {{ (void)c; return fmi2OK; }}
FMI2_EXPORT fmi2Status fmi2Reset(fmi2Component c) {{ (void)c; {n}_initialize(); now = 0.0; return fmi2OK; }}

FMI2_EXPORT fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2Real value[])
{{
    size_t i;
    (void)c;
    for (i = 0; i < nvr; i++) {{
        if (!get_var(vr[i], &value[i])) {{ return fmi2Error; }}
    }}
    return fmi2OK;
}}

FMI2_EXPORT fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2Real value[])
{{
    size_t i;
    (void)c;
    for (i = 0; i < nvr; i++) {{
        if (!set_var(vr[i], value[i])) {{ return fmi2Error; }}
    }}
    return fmi2OK;
}}

FMI2_EXPORT fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Integer v[])
{{ (void)c; (void)vr; (void)v; return n == 0 ? fmi2OK : fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Integer v[])
{{ (void)c; (void)vr; (void)v; return n == 0 ? fmi2OK : fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Boolean v[])
{{ (void)c; (void)vr; (void)v; return n == 0 ? fmi2OK : fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Boolean v[])
{{ (void)c; (void)vr; (void)v; return n == 0 ? fmi2OK : fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2GetString(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2String v[])
{{ (void)c; (void)vr; (void)v; return n == 0 ? fmi2OK : fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2SetString(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2String v[])
{{ (void)c; (void)vr; (void)v; return n == 0 ? fmi2OK : fmi2Error; }}

FMI2_EXPORT fmi2Status fmi2DoStep(fmi2Component c, fmi2Real t, fmi2Real h, fmi2Boolean no_set_prior)
{{
    double steps = h / BASE_PERIOD;
    long n = (long)floor(steps + 0.5), k;
    (void)c; (void)no_set_prior;
    if (n < 1 || fabs(steps - (double)n) > 1e-6 * steps) {{ return fmi2Error; }} /* whole base periods only */
    for (k = 0; k < n; k++) {{ {n}_step(); }}
    now = t + h;
    return fmi2OK;
}}
FMI2_EXPORT fmi2Status fmi2CancelStep(fmi2Component c) {{ (void)c; return fmi2Error; }}

FMI2_EXPORT fmi2Status fmi2GetStatus(fmi2Component c, const fmi2StatusKind s, fmi2Status* value)
{{ (void)c; (void)s; *value = fmi2OK; return fmi2OK; }}
FMI2_EXPORT fmi2Status fmi2GetRealStatus(fmi2Component c, const fmi2StatusKind s, fmi2Real* value)
{{ (void)c; *value = now; return s == fmi2LastSuccessfulTime ? fmi2OK : fmi2Discard; }}
FMI2_EXPORT fmi2Status fmi2GetIntegerStatus(fmi2Component c, const fmi2StatusKind s, fmi2Integer* value)
{{ (void)c; (void)s; (void)value; return fmi2Discard; }}
FMI2_EXPORT fmi2Status fmi2GetBooleanStatus(fmi2Component c, const fmi2StatusKind s, fmi2Boolean* value)
{{ (void)c; (void)s; *value = 0; return fmi2OK; }}
FMI2_EXPORT fmi2Status fmi2GetStringStatus(fmi2Component c, const fmi2StatusKind s, fmi2String* value)
{{ (void)c; (void)s; (void)value; return fmi2Discard; }}
FMI2_EXPORT fmi2Status fmi2SetRealInputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t n,
                                                   const fmi2Integer order[], const fmi2Real value[])
{{ (void)c; (void)vr; (void)n; (void)order; (void)value; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2GetRealOutputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t n,
                                                    const fmi2Integer order[], fmi2Real value[])
{{ (void)c; (void)vr; (void)n; (void)order; (void)value; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* s) {{ (void)c; (void)s; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate s) {{ (void)c; (void)s; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* s) {{ (void)c; (void)s; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate s, size_t* n)
{{ (void)c; (void)s; (void)n; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate s, fmi2Byte b[], size_t n)
{{ (void)c; (void)s; (void)b; (void)n; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte b[], size_t n, fmi2FMUstate* s)
{{ (void)c; (void)b; (void)n; (void)s; return fmi2Error; }}
FMI2_EXPORT fmi2Status fmi2GetDirectionalDerivative(fmi2Component c, const fmi2ValueReference u[], size_t nu,
                                                    const fmi2ValueReference z[], size_t nz, const fmi2Real dv[],
                                                    fmi2Real dz[])
{{ (void)c; (void)u; (void)nu; (void)z; (void)nz; (void)dv; (void)dz; return fmi2Error; }}
'''


def export_fmu(target: Any, path: str | Path, name: str | None = None, description: str = "",
               compile: bool = True) -> Path:  # fmt: skip
    """Generate C code for ``target`` (a Diagram or a Subsystem block) and package it as an FMI 2.0 co-simulation
    FMU at ``path``. With ``compile`` (needs a C compiler) the FMU contains a library for this platform; it always
    contains the sources."""
    code = generate_c(target, name)
    guid = str(uuid.uuid4())
    out = Path(path)
    if out.suffix != ".fmu":
        out = out.with_suffix(".fmu")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "sources"
        src.mkdir()
        for fname, text in code.c_files.items():
            (src / fname).write_text(text, encoding="utf-8")
        (src / "fmu_wrapper.c").write_text(wrapper_source(code, guid), encoding="utf-8")
        (root / "modelDescription.xml").write_text(model_description(code, guid, description), encoding="utf-8")
        if compile:
            cc = find_compiler()
            if cc is None:
                raise CodegenError("exporting a compiled FMU needs a C compiler (gcc, clang or cc)")
            binaries = root / "binaries" / platform_folder()
            binaries.mkdir(parents=True)
            lib = binaries / f"{code.name}{_library_suffix()}"
            functions = [str(src / f) for f in code.extra_sources if f.endswith(".c")]  # reusable subsystem functions
            cmd = [cc, "-shared", "-fPIC", "-O2", "-fvisibility=hidden", "-o", str(lib),
                   str(src / f"{code.name}.c"), *functions, str(src / "fmu_wrapper.c"), "-lm"]  # fmt: skip
            run = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if run.returncode != 0:
                raise CodegenError(f"the FMU does not compile:\n{run.stderr}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(root.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(root).as_posix())
    shutil.rmtree(Path(tmp), ignore_errors=True)
    return out
