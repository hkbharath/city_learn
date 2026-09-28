from typing import Any, Dict, List, Mapping, Optional

from citylearn.base import EpisodeTracker
from citylearn.data import ChargerSimulation
from citylearn.electric_vehicle import ElectricVehicle
from citylearn.electric_vehicle_charger import Charger


class MatlabCharger(Charger):
    """Bridge for a charger whose control loop runs in MATLAB.

    The MATLAB model owns the actual charger dynamics and optionally the EV model.
    This adapter keeps the CityLearn reset/step lifecycle while starting and polling
    the MATLAB model at a different sampling frequency.

    When `connected_electric_vehicle` is not provided, the MATLAB model is assumed to
    internally simulate the EV (battery, SOC, etc.). In this case, charger state
    synchronization will extract EV data from MATLAB snapshots but won't update a
    local EV object.
    """

    def __init__(
        self,
        episode_tracker: EpisodeTracker,
        charger_simulation: ChargerSimulation,
        charger_id: str = None,
        efficiency: float = None,
        max_charging_power: float = None,
        min_charging_power: float = None,
        max_discharging_power: float = None,
        min_discharging_power: float = None,
        charge_efficiency_curve: Dict[float, float] = None,
        discharge_efficiency_curve: Dict[float, float] = None,
        connected_electric_vehicle: ElectricVehicle = None,
        incoming_electric_vehicle: ElectricVehicle = None,
        phase_connection: str = None,
        time_step_ratio: int = None,
        matlab_bridge: Any = None,
        matlab_step_seconds: float = None,
        **kwargs,
    ):
        self.matlab_bridge = matlab_bridge
        self.matlab_step_seconds = matlab_step_seconds
        self.matlab_time_step_ratio = 1.0
        self.last_matlab_snapshot = None
        self.matlab_snapshots: List[Mapping[str, Any]] = []

        super().__init__(
            episode_tracker=episode_tracker,
            charger_simulation=charger_simulation,
            charger_id=charger_id,
            efficiency=efficiency,
            max_charging_power=max_charging_power,
            min_charging_power=min_charging_power,
            max_discharging_power=max_discharging_power,
            min_discharging_power=min_discharging_power,
            charge_efficiency_curve=charge_efficiency_curve,
            discharge_efficiency_curve=discharge_efficiency_curve,
            connected_electric_vehicle=connected_electric_vehicle,
            incoming_electric_vehicle=incoming_electric_vehicle,
            phase_connection=phase_connection,
            time_step_ratio=time_step_ratio,
            **kwargs,
        )

        if self.matlab_step_seconds is None:
            self.matlab_step_seconds = self.seconds_per_time_step
        self.matlab_time_step_ratio = (
            float(self.seconds_per_time_step) / max(float(self.matlab_step_seconds), 1e-9)
        )

    @property
    def matlab_bridge(self) -> Any:
        return self.__matlab_bridge

    @matlab_bridge.setter
    def matlab_bridge(self, matlab_bridge: Any):
        self.__matlab_bridge = matlab_bridge

    @property
    def thd_values(self) -> Dict[int, float]:
        """Extract Total Harmonic Distortion (THD) values from MATLAB snapshots.

        Returns a dictionary mapping time steps to THD values (as percentages).
        If a snapshot doesn't contain a THD field, it is skipped.

        Returns:
            dict: Mapping of time_step -> thd_value
        """
        thd_dict: Dict[int, float] = {}
        for snapshot in self.matlab_snapshots:
            if isinstance(snapshot, Mapping) and "thd" in snapshot:
                time_step = snapshot.get("time_step")
                if time_step is not None:
                    thd_dict[int(time_step)] = float(snapshot["thd"])
        return thd_dict

    def get_thd_at_time_step(self, time_step: int) -> Optional[float]:
        """Get THD value at a specific time step.

        Args:
            time_step (int): The time step to retrieve THD for.

        Returns:
            float or None: THD value if available, None otherwise.
        """
        thd_values = self.thd_values
        return thd_values.get(time_step)

    def _matlab_payload(self, time_step: Optional[int] = None) -> dict:
        current_time_step = self.time_step if time_step is None else time_step
        ev = self.connected_electric_vehicle

        payload = {
            "charger_id": self.charger_id,
            "time_step": current_time_step,
            "seconds_per_time_step": self.seconds_per_time_step,
            "matlab_step_seconds": self.matlab_step_seconds,
            "max_charging_power_kw": self.max_charging_power,
            "min_charging_power_kw": self.min_charging_power,
            "max_discharging_power_kw": self.max_discharging_power,
            "min_discharging_power_kw": self.min_discharging_power,
            "phase_connection": self.phase_connection,
            "efficiency": self.efficiency,
            "connected_ev": ev.name if ev is not None else None,
            "incoming_ev": (
                self.incoming_electric_vehicle.name
                if self.incoming_electric_vehicle is not None
                else None
            ),
        }

        # Include local EV state if available; MATLAB model may use this or manage EV internally
        if ev is not None and hasattr(ev, "battery"):
            payload["soc"] = getattr(ev.battery, "soc", None)
            payload["soc_percent"] = getattr(ev.battery, "soc", None)

        return payload

    def start_matlab_charger(self):
        """Launch the external MATLAB model if the bridge exposes a start call."""
        if self.matlab_bridge is None:
            return None

        start_method = getattr(self.matlab_bridge, "start", None)
        if callable(start_method):
            start_method()

        reset_method = getattr(self.matlab_bridge, "reset", None)
        if callable(reset_method):
            snapshot = reset_method(self._matlab_payload())
            self.last_matlab_snapshot = snapshot
            if snapshot is not None:
                self.matlab_snapshots.append(snapshot)
            return snapshot

        return None

    def refresh_matlab_snapshot(self) -> Optional[Mapping[str, Any]]:
        """Poll the external MATLAB model and synchronize returned values."""
        if self.matlab_bridge is None:
            return None

        read_method = getattr(self.matlab_bridge, "read_snapshot", None)
        snapshot = None
        if callable(read_method):
            snapshot = read_method()
        elif hasattr(self.matlab_bridge, "snapshot"):
            snapshot = self.matlab_bridge.snapshot

        if snapshot is None:
            return None

        self.last_matlab_snapshot = snapshot
        self.matlab_snapshots.append(snapshot)
        self._sync_from_matlab_snapshot(snapshot)
        return snapshot

    def _sync_from_matlab_snapshot(self, snapshot: Mapping[str, Any]):
        """Synchronize charger and EV state from MATLAB snapshot.

        When a local EV is connected, this updates its state (SOC, etc.).
        When no local EV is provided, MATLAB is assumed to manage the EV internally,
        and this method extracts charger-relevant data (power, connection state).
        """
        if not isinstance(snapshot, Mapping):
            return

        if "electricity_consumption" in snapshot:
            self._Charger__electricity_consumption[self.time_step] = float(
                snapshot["electricity_consumption"]
            )

        if "charging_action_kwh" in snapshot or "charging_action" in snapshot:
            key = "charging_action_kwh" if "charging_action_kwh" in snapshot else "charging_action"
            self._Charger__past_charging_action_values_kwh[self.time_step] = float(
                snapshot[key]
            )

        if "connected" in snapshot and snapshot.get("connected") is False:
            self.connected_electric_vehicle = None

        # Only update local EV if one is connected. MATLAB model manages EV internally otherwise.
        if "soc" in snapshot and self.connected_electric_vehicle is not None:
            ev = self.connected_electric_vehicle
            soc_value = float(snapshot["soc"])
            if hasattr(ev, "battery") and hasattr(ev.battery, "soc"):
                ev.battery.soc[self.time_step] = soc_value

    def reset(self):
        """Reset the charger and start the MATLAB model at the same lifecycle point."""
        super().reset()
        self.start_matlab_charger()

    def update_connected_electric_vehicle_soc(self, action_value: float):
        """If a MATLAB bridge is configured, let it compute the power response."""
        if self.matlab_bridge is not None:
            sync_method = getattr(self.matlab_bridge, "update_action", None)
            if callable(sync_method):
                sync_method(self._matlab_payload())
            elif hasattr(self.matlab_bridge, "last_action"):
                self.matlab_bridge.last_action = action_value

        snapshot = self.refresh_matlab_snapshot()
        if snapshot is not None and "electricity_consumption" in snapshot:
            self._Charger__electricity_consumption[self.time_step] = float(
                snapshot["electricity_consumption"]
            )
            return

        super().update_connected_electric_vehicle_soc(action_value)

    def __str__(self):
        return str(self.as_dict())
