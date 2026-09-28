from typing import Any, Dict, Mapping, Optional
import warnings


class MatlabBridge:
    """Lightweight bridge/adaptor to a MATLAB-based charger model.

    This class supports engine-backed or mock mode and exposes a small
    facade used by `MatlabCharger` and tests.
    """

    def __init__(
        self,
        engine: Optional[Any] = None,
        start_fn: str = "start",
        reset_fn: str = "reset",
        read_fn: str = "read_snapshot",
        update_fn: str = "update_action",
        snapshot_attr: str = "snapshot",
        start_matlab_engine: bool = False,
        matlab_engine_kwargs: Optional[Dict[str, Any]] = None,
    ):
        self.engine = engine
        self.start_fn = start_fn
        self.reset_fn = reset_fn
        self.read_fn = read_fn
        self.update_fn = update_fn
        self.snapshot_attr = snapshot_attr

        self.started = False
        self.reset_calls = []
        self.snapshots = []
        self.last_action: Optional[float] = None

        # Optionally auto-start the MATLAB engine if requested. This will
        # attempt to import the MATLAB engine API (``matlab.engine``) and
        # start a MATLAB session. If unavailable, a warning is issued and
        # the bridge remains in mock/engine-less mode.
        if start_matlab_engine and self.engine is None:
            try:
                import matlab.engine  # type: ignore

                kwargs = matlab_engine_kwargs or {}
                # matlab.engine.start_matlab accepts no kwargs normally,
                # but exposing kwargs keeps the API flexible for overlays.
                self.engine = matlab.engine.start_matlab(**kwargs)
            except Exception as e:  # pragma: no cover - depends on MATLAB
                warnings.warn(f"Could not start MATLAB engine: {e}")

    def start(self) -> None:
        if self.engine is not None and hasattr(self.engine, self.start_fn):
            start_method = getattr(self.engine, self.start_fn)
            if callable(start_method):
                start_method()

        self.started = True

    def reset(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        self.reset_calls.append(dict(payload))

        if self.engine is not None and hasattr(self.engine, self.reset_fn):
            reset_method = getattr(self.engine, self.reset_fn)
            if callable(reset_method):
                snapshot = reset_method(payload)
                if snapshot is not None:
                    self.snapshots.append(snapshot)
                    return snapshot

        time_step = int(payload.get("time_step", 0))
        max_p = float(payload.get("max_charging_power_kw") or 0.0)
        min_p = float(payload.get("min_charging_power_kw") or 0.0)

        electricity_consumption = min(max_p, 0.01 * max_p + 0.5)

        thd = 0.02 + 0.001 * (time_step % 10) + abs(max_p - min_p) / max(1.0, max_p + 1.0) * 0.01

        snapshot = {
            "charger_id": payload.get("charger_id"),
            "electricity_consumption": electricity_consumption,
            "charging_action": 0.0,
            "connected": True,
            "soc": payload.get("soc", None),
            "time_step": time_step,
            "thd": float(thd),
        }

        self.snapshots.append(snapshot)
        return snapshot

    def read_snapshot(self) -> Optional[Mapping[str, Any]]:
        if self.engine is not None and hasattr(self.engine, self.read_fn):
            read_method = getattr(self.engine, self.read_fn)
            if callable(read_method):
                snapshot = read_method()
                if snapshot is not None:
                    self.snapshots.append(snapshot)
                    return snapshot

        if self.engine is not None and hasattr(self.engine, self.snapshot_attr):
            snapshot = getattr(self.engine, self.snapshot_attr)
            if snapshot is not None:
                self.snapshots.append(snapshot)
                return snapshot

        return self.snapshots[-1] if self.snapshots else None

    def update_action(self, payload: Mapping[str, Any]) -> None:
        if isinstance(payload, Mapping):
            val = payload.get("action_value") or payload.get("charging_action")
            try:
                self.last_action = float(val) if val is not None else None
            except Exception:
                self.last_action = None
        else:
            try:
                self.last_action = float(payload)
            except Exception:
                self.last_action = None

        if self.engine is not None and hasattr(self.engine, self.update_fn):
            update_method = getattr(self.engine, self.update_fn)
            if callable(update_method):
                update_method(payload)

    @property
    def snapshot(self) -> Optional[Mapping[str, Any]]:
        return self.read_snapshot()


class _FakeMatlabBridge:
    """Simple fake bridge intended for tests.

    Mirrors the minimal API expected by `MatlabCharger`.
    """

    def __init__(self):
        self.started = False
        self.reset_calls = []
        self.snapshots = []
        self.last_action = None

    def start(self):
        self.started = True

    def reset(self, payload):
        self.reset_calls.append(payload)
        snapshot = {
            "charger_id": payload.get("charger_id"),
            "electricity_consumption": 0.5,
            "charging_action": 0.0,
            "connected": True,
            "soc": 0.7,
            "thd": 0.02,
            "time_step": payload.get("time_step", 0),
        }
        self.snapshots.append(snapshot)
        return snapshot

    def read_snapshot(self):
        return self.snapshots[-1] if self.snapshots else None

    def update_action(self, payload):
        if isinstance(payload, dict):
            self.last_action = payload.get("action_value") or payload.get("charging_action") or payload.get("action")
        else:
            self.last_action = payload

    @property
    def snapshot(self):
        return self.read_snapshot()


__all__ = ["MatlabBridge", "_FakeMatlabBridge"]
