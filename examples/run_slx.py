"""Run a Simulink model (.slx) headlessly via MATLAB Engine for Python and
capture both its console output and any logged signal data.

Usage
-----
    python run_slx.py path/to/model.slx
    python run_slx.py path/to/model.slx --stop-time 10 --output results.csv
    python run_slx.py path/to/model.slx --model-path extra/lib/dir --signals Soc,Thd

What gets captured
-------------------
1. Console output — anything the model or MATLAB itself prints during the run
   (``disp``/``fprintf`` calls, warnings, etc.) is redirected into a log file
   instead of the MATLAB desktop, since the model runs headless.
2. Signal data — each root-level Outport block's *final* value (at the end of
   the simulation) is pulled out of the returned ``Simulink.SimulationOutput``
   object (via ``yout``) and written to a single-row CSV, one column per port,
   named after its Outport block. If the model has no root Outport blocks,
   only the console output is saved, and a hint is printed.

Requires MATLAB + Simulink and the MATLAB Engine for Python package
(``python -m pip install matlabengine``, or run ``setup.py install`` from
``<matlabroot>/extern/engines/python`` — see ``tests/test_matlab_engine.py``
for a sanity check).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Simulink model (.slx) headlessly and capture its console output and logged signals."
    )
    parser.add_argument("model", type=Path, help="Path to the Simulink model (.slx)")
    parser.add_argument(
        "--stop-time",
        type=float,
        default=0.5,
        help="Override the model's configured simulation stop time (seconds)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="CSV path for logged signal data (default: <model>_output.csv)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Text file path for captured console output (default: <model>_console.log)",
    )
    parser.add_argument(
        "--model-path",
        action="append",
        default=[],
        help="Extra directory to add to the MATLAB path (repeatable)",
    )
    parser.add_argument(
        "--signals",
        default=None,
        help="Comma-separated list of logged signal names to export (default: all)",
    )
    parser.add_argument(
        "--vref-llc",
        type=float,
        default=400,
        help="Value to write to the 'Vref_LLC' Constant block before running (default: 300)",
    )
    parser.add_argument(
        "--vref-pfc",
        type=float,
        default=400,
        help="Value to write to the 'Vref_PFC' Constant block before running (default: 400)",
    )
    parser.add_argument(
        "--init-i-ccs",
        type=float,
        default=0,
        help="Value to write to the 'pi_init_I_CCS' Constant block before running (default: 0)",
    )
    parser.add_argument(
        "--target-i-ccs",
        type=float,
        default=0,
        help="Value to write to the 'pi_target_I_CCS' Constant block before running (default: 0)",
    )
    return parser.parse_args(argv)


def run_model(
    model: Path,
    stop_time: float | None,
    extra_paths: list[Path],
    signal_filter: list[str] | None,
    vref_llc: float,
    vref_pfc: float,
    init_i_ccs: float,
    target_i_ccs: float,
) -> tuple[pd.DataFrame, str]:
    import matlab.engine  # imported lazily so --help works without MATLAB installed

    model = model.resolve()
    model_name = model.stem

    eng = matlab.engine.start_matlab()

    # Log the model's console output via MATLAB's own `diary` instead of piping
    # eng.sim()'s stdout/stderr through Python StringIO buffers: that redirection
    # can corrupt the MATLAB Engine's internal parser state, causing unrelated
    # later eng.eval() calls to fail with a bogus "Invalid text character" error.
    diary_fd, diary_path = tempfile.mkstemp(suffix=".log")
    os.close(diary_fd)
    diary_path_matlab = diary_path.replace("\\", "/")

    try:
        # Keep MATLAB's working directory (and thus the Accelerator build cache
        # it writes to `slprj/` under it) pinned next to the model, regardless of
        # where this script is invoked from, so repeated launches hit the same
        # cache instead of scattering/rebuilding one per cwd.
        eng.cd(str(model.parent), nargout=0)
        eng.addpath(str(model.parent), nargout=0)
        for path in extra_paths:
            eng.addpath(str(Path(path).resolve()), nargout=0)

        eng.load_system(model_name, nargout=0)  # headless load, no editor window

        # Run in Accelerator mode instead of Normal (interpreted) mode: Simulink
        # builds a MEX target for the model once and caches it on disk (slprj/),
        # so a later launch that finds a matching cache reuses it instead of
        # recompiling. Only the Constant blocks poked below change between runs,
        # which doesn't invalidate the cached target.
        eng.set_param(model_name, "SimulationMode", "accelerator", nargout=0)

        eng.set_param(f"{model_name}/pi_Vref_LLC", "Value", str(vref_llc), nargout=0)
        eng.set_param(f"{model_name}/pi_Vref_PFC", "Value", str(vref_pfc), nargout=0)
        eng.set_param(f"{model_name}/pi_init_I_CCS", "Value", str(init_i_ccs), nargout=0)
        eng.set_param(f"{model_name}/pi_target_I_CCS", "Value", str(target_i_ccs), nargout=0)

        # Force root Outport logging on programmatically instead of relying on
        # Configuration Parameters -> Data Import/Export GUI settings, whose
        # layout/labels have moved around across MATLAB versions.
        eng.set_param(model_name, "SaveOutput", "on", nargout=0)
        eng.set_param(model_name, "OutputSaveName", "yout", nargout=0)
        eng.set_param(model_name, "SaveFormat", "Dataset", nargout=0)

        sim_args = ["ReturnWorkspaceOutputs", "on"]
        if stop_time is not None:
            sim_args = ["StopTime", str(stop_time)] + sim_args

        eng.eval(f"diary('{diary_path_matlab}');", nargout=0)
        eng.eval("diary on;", nargout=0)
        sim_out = eng.sim(model_name, *sim_args, nargout=1)
        eng.eval("diary off;", nargout=0)
        eng.workspace["simOut"] = sim_out

        data = _extract_output_ports(eng, model_name, signal_filter)
    finally:
        try:
            eng.eval("diary off;", nargout=0)
        except Exception:
            pass
        try:
            eng.close_system(model_name, 0, nargout=0)
        except Exception:
            pass
        eng.quit()

    console_output = Path(diary_path).read_text(errors="replace")
    Path(diary_path).unlink(missing_ok=True)
    return data, console_output


def _extract_output_ports(eng, model_name: str, signal_filter: list[str] | None) -> pd.DataFrame:
    """Pull each root-level Outport block's final value out of `simOut.yout`."""
    has_yout = eng.eval(
        "isa(simOut, 'Simulink.SimulationOutput') && ismember('yout', simOut.who)",
        nargout=1,
    )
    if not has_yout:
        return pd.DataFrame()

    eng.eval("yout = simOut.get('yout');", nargout=0)
    is_dataset = eng.eval("isa(yout, 'Simulink.SimulationData.Dataset')", nargout=1)

    if is_dataset:
        return _dataset_final_values(eng, "yout", signal_filter, model_name)

    # "Save format" was Array/Structure instead of Dataset: yout is a plain
    # matrix with one column per root Outport, in port-number order.
    names = _root_outport_names(eng, model_name)
    final_time = eng.eval("simOut.get('tout')(end)", nargout=1)
    final_row = eng.eval("simOut.get('yout')(end, :)'", nargout=1)
    # A single-outport model converts straight to a python float instead of a
    # matlab.double column vector.
    final_values = [final_row] if isinstance(final_row, (int, float)) else [row[0] for row in final_row]

    row = {}
    for col, name in enumerate(names):
        if signal_filter and name not in signal_filter:
            continue
        row[name] = final_values[col]

    return pd.DataFrame([row], index=pd.Index([final_time], name="time")) if row else pd.DataFrame()


def _root_outport_names(eng, model_name: str) -> list[str]:
    """Root Outport block names, in ascending port-number order."""
    info = eng.eval(
        f"cellfun(@(b) struct('name', get_param(b, 'Name'), "
        f"'port', str2double(get_param(b, 'Port'))), "
        f"find_system('{model_name}', 'SearchDepth', 1, 'BlockType', 'Outport'), "
        f"'UniformOutput', false)",
        nargout=1,
    )
    return [item["name"] for item in sorted(info, key=lambda item: item["port"])]


def _dataset_final_values(
    eng, dataset_var: str, signal_filter: list[str] | None, model_name: str
) -> pd.DataFrame:
    # Pull every element's name/final-time/final-value in a single round trip
    # instead of looping over per-element eng.eval() calls: many small
    # back-to-back calls into the MATLAB Engine session have been observed to
    # desync its parser, surfacing as a bogus "Invalid text character"
    # SyntaxError on a later, unrelated call. `Data(end, :)'` transposes the
    # final sample to a column so scalar and vector signals convert the same way.
    elements = eng.eval(
        f"arrayfun(@(i) struct('name', {dataset_var}{{i}}.Name, "
        f"'time', {dataset_var}{{i}}.Values.Time(end), "
        f"'data', {dataset_var}{{i}}.Values.Data(end, :)'), "
        f"1:{dataset_var}.numElements, 'UniformOutput', false)",
        nargout=1,
    )

    # Dataset elements are unnamed unless the signal feeding the Outport was
    # explicitly named; fall back to the root Outport block names (in port order)
    # in that case, since Dataset element order matches root-Outport port order.
    fallback_names = None

    row = {}
    final_time = None
    for i, elem in enumerate(elements):
        name = elem["name"]
        if not name:
            if fallback_names is None:
                fallback_names = _root_outport_names(eng, model_name)
            name = fallback_names[i] if i < len(fallback_names) else f"signal_{i + 1}"

        if signal_filter and name not in signal_filter:
            continue

        final_time = elem["time"]
        data = elem["data"]
        # A width-1 signal converts straight to a python float; width >1 stays a
        # matlab.double column vector, which iterates into one-element rows.
        values = [data] if isinstance(data, (int, float)) else [v[0] for v in data]

        if len(values) == 1:
            row[name] = values[0]
        else:
            for k, value in enumerate(values):
                row[f"{name}_{k + 1}"] = value

    return pd.DataFrame([row], index=pd.Index([final_time], name="time")) if row else pd.DataFrame()


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.model.exists():
        print(f"Model not found: {args.model}", file=sys.stderr)
        return 1

    results_dir = Path("py_results")
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output or results_dir / f"{args.model.stem}_output.csv"
    log_path = args.log or results_dir / f"{args.model.stem}_console.log"
    signal_filter = [s.strip() for s in args.signals.split(",")] if args.signals else None
    extra_paths = args.model_path + [str(args.model.parent)]

    print(f"Running {args.model} ...")
    data, console_output = run_model(
        args.model,
        args.stop_time,
        extra_paths,
        signal_filter,
        args.vref_llc,
        args.vref_pfc,
        args.init_i_ccs,
        args.target_i_ccs,
    )

    log_path.write_text(console_output)
    print(f"Console output saved to {log_path}")
    if console_output.strip():
        print("--- console output ---")
        print(console_output.strip())
        print("----------------------")

    if data.empty:
        print(
            "No output port data found. Make sure the model has root-level "
            "Outport blocks and Configuration Parameters -> Data Import/Export "
            "-> Output has 'Save to workspace' checked, then re-run."
        )
    else:
        data.to_csv(output_path)
        print(f"Logged {len(data.columns)} signal(s) (final value) -> {output_path}")
        print(data)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
