# Paper artifacts

This directory contains the driver scripts, scenarios and processed results behind the evaluation in

> *A Web-based multi-user containerized simulation platform for IEEE 802.11p vehicular networks built on Veins*
> (manuscript submitted to *Computer Networks*).

Every number in Section 4 of the paper is read from the JSON files listed below; the raw OMNeT++ result files
(`.sca`, `.vec`, per-run logs) are attached to the GitHub release `v1.0.0` as `vein-iov-paper-raw-results.zip`
(unpack it into this directory to rerun the analysis scripts).

## Where each result comes from

| Paper item | Script (subcommand) | Result file(s) |
|---|---|---|
| Table 2 (isolation and security tests) | `exp_security.py`, `exp_quota.py`, `exp_isolation.py` | `results/expSecurity/security.json`, `results/expQuota/quota.json`, `results/expIsolation/isolation.json` |
| Table 3 (output consistency and runtime, CLI vs. platform) | `exp_timing_v6.py e1`, then `analyze_v6.py` | `results_v6/E1_consistency_timing.json`, `results_v6/E1_metrics.json` |
| Table 5, Fig. 18 (parallel execution, four workloads) | `exp_timing_v6.py t2`, `exp_parallel_cli.py` | `results_v6/T2_W{1..4}.json`, `results_v6/T2b_W{1..4}_cli_parallel.json`, CPU-frequency samples in `results_v6/freq/` |
| Section 4.3, interleaved W2 measurement (command line, command line with quotas, platform) | `exp_quota_effect.py W2` | `results_v6/T2c_W2_quota_effect.json` |
| Fig. 19 (completion time vs. number of concurrent tasks) | `exp_timing_v6.py e2` | `results_v6/E2_concurrency.json` |
| Section 4.4 noVNC latency | `exp_timing_v6.py e4` | `results_v6/E4_novnc.json` |
| Table 6 (resource footprint, start-up breakdown) | `exp_profile.py` (sampler, runs alongside the timing batch), then `analyze_v6.py` | `results_v6/T3_profile.json` |
| Figs. 20–21 (beaconing case study, 27 runs) | `exp_sweep.py submit`, `exp_sweep.py analyze` | `results/expSweep/sweep.json`, `results/expSweep/sweep_metrics.json` |
| Supplementary: state reconciliation after a worker crash; result isolation and cancellation latency | `exp_reconcile.py`, `exp_fixes.py` | `results/expReconcile/reconcile.json`, `results/expFixes/fixes.json` |
| Supplementary: concurrent runs of one project (build race regression test) | `exp_buildrace.py` | `results/expBuildRace/buildrace.json` |

`figures/charts_v6.json` (Chinese labels) and `figures/charts_en_v6.json` (English labels) hold the exact series
plotted in Figs. 17–21. Fig. 17 and Table 4 (manual steps) come from the workflow analysis in Section 4.2, not from
a script. `run_chain_v6.py` is the unattended chain the authors used (sweep analysis → sampler switch → timing
batch); `parse_sca.py` computes the network metrics from `.sca` files.

### Note on the simulation entrypoint

The timing experiments were run before one change in this release: `worker/entrypoint.sh` now compiles the project
in a container-private copy instead of the shared project directory (concurrent runs of one project could otherwise
overwrite each other's build files; this caused one failed run in `results_v6/T2c_W2_quota_effect_before_buildfix.json`).
The simulation itself still runs in the project directory. `exp_buildrace.py` checks that results are identical line
by line to those of the previous entrypoint and that 3 rounds of 10 concurrent runs of one project all succeed.
`exp_quota_effect.py` (Section 4.3) was rerun with the new entrypoint.

## Workloads and scenarios

- `scenario/` – the Veins 5.3 `RSUExampleScenario` (Erlangen), 200 s; workload W1 and Table 3.
- `scenario_long/` – the same scenario with periodic beaconing, 900 s; workload W3 and the base of the sweep.
- `calib/routes_{50,300,600}.rou.xml` – multi-entry random trips generated with SUMO `randomTrips.py`
  (`calib/gen_routes.sh`); workloads W2 (300 vehicles, 1 Hz) and W4 (600 vehicles, 5 Hz) and the sweep.
  `exp_sweep.py` builds each sweep scenario from `scenario_long/` and these route files.

## Rerunning

1. Build the simulation image from `worker/dockerfile` (tag `veins-simulation-worker:latest`) and start the backend,
   Redis, the simulation worker and the analysis worker as described in the repository README.
2. Run the scripts from this directory with the backend's Python environment (`requirements.txt` already contains
   `httpx`, `docker` and `PyJWT`, the only third-party packages they use), e.g.
   `python exp_timing_v6.py all`. `exp_quota.py` and `exp_isolation.py` import the worker module to obtain the
   exact container parameters the platform uses. Scripts read the administrator credentials and JWT secret from
   the repository's `config.cfg`.
3. Timing scripts skip items whose result file already exists; delete a file to rerun that item.

The timing results were measured on a single workstation (Windows 11, Docker Desktop with the WSL 2 backend);
the CPU-frequency sampler in `exp_timing_v6.py` uses the Windows performance counter
`% Processor Performance` and records nothing on other systems.

## Third-party files

The scenario files are taken from the Veins 5.3 examples and keep their original headers:
`*.net.xml` and `*.poly.xml` are CC-BY-SA-2.0 (derived from OpenStreetMap data), the other files are
GPL-2.0-or-later or (GPL-2.0-or-later OR CC-BY-SA-4.0). The generated route files in `calib/` are derived from the
same road network. Everything else in this directory is covered by the repository's MIT license.
