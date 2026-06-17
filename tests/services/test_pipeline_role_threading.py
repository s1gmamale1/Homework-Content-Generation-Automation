"""Guard: the per-role override params (extract_provider/model, judge_*_ov) are
declared on every pipeline function in the call chain AND forwarded inward.

This class of bug (a call site passes a kwarg the callee's signature doesn't
declare, or an intermediate function fails to forward it) is invisible to the
rest of the suite because the real run() chain is DB-gated/skipped — so a plain
`pytest` stays green while a real job would raise TypeError or silently drop the
override. These checks run with no DB.
"""
import inspect

from app.services import pipeline

_ROLE_PARAMS = ("extract_provider", "extract_model", "judge_provider_ov", "judge_model_ov")


def test_all_chain_functions_declare_the_role_params():
    for fn_name in ("_execute_one_phase", "_execute_phase", "_run_content_phases_parallel"):
        fn = getattr(pipeline, fn_name)
        params = inspect.signature(fn).parameters
        for p in _ROLE_PARAMS:
            assert p in params, f"{fn_name} is missing param {p!r}"


def test_execute_one_phase_forwards_role_params_to_execute_phase():
    # _execute_one_phase must thread the overrides into its _execute_phase call,
    # else the resolved values never reach the extract/judge logic.
    src = inspect.getsource(pipeline._execute_one_phase)
    for p in _ROLE_PARAMS:
        assert f"{p}={p}" in src, f"_execute_one_phase does not forward {p} to _execute_phase"
