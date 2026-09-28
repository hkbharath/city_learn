import numpy as np
import pytest

pytest.importorskip("gymnasium")

from citylearn.base import EpisodeTracker
from citylearn.data import ChargerSimulation
from citylearn.energy_model import Battery
from citylearn.electric_vehicle import ElectricVehicle
from citylearn.matlab_ev_charger import MatlabCharger
from citylearn.utils.matlab_bridge import _FakeMatlabBridge


def _make_tracker(length: int = 3) -> EpisodeTracker:
    tracker = EpisodeTracker(0, length - 1)
    tracker.next_episode(length, False, False, 0)
    return tracker


def _make_simulation(length: int, ev_id: str) -> ChargerSimulation:
    return ChargerSimulation(
        electric_vehicle_charger_state=[1] * length,
        electric_vehicle_id=[ev_id] * length,
        electric_vehicle_departure_time=[-1] * length,
        electric_vehicle_required_soc_departure=[-0.1] * length,
        electric_vehicle_estimated_arrival_time=[-1] * length,
        electric_vehicle_estimated_soc_arrival=[-0.1] * length,
    )


def _make_ev(tracker: EpisodeTracker, initial_soc: float = 0.5) -> ElectricVehicle:
    battery = Battery(
        capacity=100.0,
        nominal_power=50.0,
        initial_soc=initial_soc,
        efficiency=1.0,
        capacity_loss_coefficient=0.0,
        power_efficiency_curve=[[0.0, 1.0], [1.0, 1.0]],
        capacity_power_curve=[[0.0, 1.0], [1.0, 1.0]],
        loss_coefficient=0.0,
        seconds_per_time_step=3600,
        episode_tracker=tracker,
    )
    ev = ElectricVehicle(episode_tracker=tracker, battery=battery, seconds_per_time_step=3600)
    ev.reset()
    return ev


def test_matlab_charger_starts_in_matlab_on_reset_and_collects_snapshot():
    tracker = _make_tracker(4)
    ev = _make_ev(tracker, initial_soc=0.4)
    sim = _make_simulation(4, ev.name)
    bridge = _FakeMatlabBridge()

    charger = MatlabCharger(
        episode_tracker=tracker,
        charger_simulation=sim,
        charger_id="matlab-1",
        max_charging_power=11.0,
        max_discharging_power=11.0,
        connected_electric_vehicle=ev,
        seconds_per_time_step=3600,
        matlab_step_seconds=60.0,
        matlab_bridge=bridge,
    )
    charger.reset()

    # Verify bridge was started and reset
    assert bridge.started is True
    assert bridge.reset_calls and bridge.reset_calls[0]["charger_id"] == "matlab-1"
    
    # Verify time step ratio calculation
    assert charger.matlab_time_step_ratio == pytest.approx(60.0)
    
    # Verify snapshot was captured
    assert charger.last_matlab_snapshot is not None
    snapshot = charger.last_matlab_snapshot
    
    # Verify snapshot contains all required fields for charger synchronization
    # 1. electricity_consumption - required for power consumption tracking
    assert "electricity_consumption" in snapshot
    assert snapshot["electricity_consumption"] == pytest.approx(0.5)
    
    # 2. charging_action - required for charging action tracking
    assert "charging_action" in snapshot
    assert snapshot["charging_action"] == pytest.approx(0.0)
    
    # 3. connected - required for EV connection state tracking
    assert "connected" in snapshot
    assert snapshot["connected"] is True
    
    # 4. soc - required for battery state-of-charge tracking (if EV is connected)
    assert "soc" in snapshot
    assert snapshot["soc"] == pytest.approx(0.7)
    
    # 5. thd - Total Harmonic Distortion from MATLAB model
    assert "thd" in snapshot
    assert isinstance(snapshot["thd"], (int, float))
    assert snapshot["thd"] >= 0  # THD should be non-negative
    
    # 6. charger_id and time_step for traceability
    assert snapshot["charger_id"] == "matlab-1"
    assert snapshot["time_step"] == 0


def test_matlab_charger_without_ev_and_thd_access():
    """Test MATLAB charger when the MATLAB model manages the EV internally."""
    tracker = _make_tracker(4)
    sim = _make_simulation(4, "internal-ev")
    bridge = _FakeMatlabBridge()

    # Create charger WITHOUT a connected EV - MATLAB will manage it internally
    charger = MatlabCharger(
        episode_tracker=tracker,
        charger_simulation=sim,
        charger_id="matlab-2",
        max_charging_power=11.0,
        max_discharging_power=11.0,
        connected_electric_vehicle=None,  # No local EV - MATLAB manages it
        seconds_per_time_step=3600,
        matlab_step_seconds=60.0,
        matlab_bridge=bridge,
    )
    charger.reset()

    # Verify the charger initialized without an EV
    assert charger.connected_electric_vehicle is None
    
    # Verify MATLAB model was started
    assert bridge.started is True
    assert charger.last_matlab_snapshot is not None
    
    # Verify we can access THD values from the charger
    thd_values = charger.thd_values
    assert isinstance(thd_values, dict)
    assert 0 in thd_values  # First time step should have THD data
    assert thd_values[0] == pytest.approx(0.02)
    
    # Verify get_thd_at_time_step method works
    thd_at_0 = charger.get_thd_at_time_step(0)
    assert thd_at_0 is not None
    assert thd_at_0 == pytest.approx(0.02)
    
    # Verify non-existent time step returns None
    thd_at_999 = charger.get_thd_at_time_step(999)
    assert thd_at_999 is None
