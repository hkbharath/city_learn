# MATLAB Bridge Integration Guide

## Overview

The `MatlabBridge` class provides a lightweight adapter to integrate MATLAB-based charger models into CityLearn's `MatlabCharger`. It supports both **mock mode** (for testing without MATLAB) and **engine-backed mode** (for real MATLAB integration).

## Quick Start

### 1. Test Mode (No MATLAB Required)

Use the mock bridge for unit tests and prototyping:

```python
from citylearn.utils.matlab_bridge import MatlabBridge

# Create a mock bridge (no engine, uses deterministic mock snapshots)
bridge = MatlabBridge()

# Use it with MatlabCharger
charger = MatlabCharger(
    episode_tracker=tracker,
    charger_simulation=sim,
    matlab_bridge=bridge,
    ...
)
```

### 2. Real MATLAB Engine Integration

#### Prerequisites

1. **MATLAB Engine for Python** must be installed:
   ```bash
   # Navigate to MATLAB's engine directory (replace with your MATLAB version)
   cd "C:\Program Files\MATLAB\R2023b\extern\engines\python"
   python -m pip install .
   ```

2. **Active MATLAB Installation**: Your machine must have MATLAB installed with a valid license.

#### Option A: Auto-Start MATLAB Engine

Let the bridge automatically start MATLAB:

```python
from citylearn.utils.matlab_bridge import MatlabBridge

# Auto-start MATLAB engine
bridge = MatlabBridge(start_matlab_engine=True)

charger = MatlabCharger(
    episode_tracker=tracker,
    charger_simulation=sim,
    matlab_bridge=bridge,
    ...
)
```

#### Option B: Provide an Existing Engine

Start MATLAB manually and pass the engine:

```python
import matlab.engine
from citylearn.utils.matlab_bridge import MatlabBridge

# Start MATLAB (this can take 10-30 seconds)
eng = matlab.engine.start_matlab()

# Create bridge with existing engine
bridge = MatlabBridge(engine=eng)

charger = MatlabCharger(
    episode_tracker=tracker,
    charger_simulation=sim,
    matlab_bridge=bridge,
    ...
)

# Clean up
eng.quit()
```

## API Reference

### MatlabBridge Constructor

```python
MatlabBridge(
    engine: Optional[Any] = None,
    start_fn: str = "start",
    reset_fn: str = "reset",
    read_fn: str = "read_snapshot",
    update_fn: str = "update_action",
    snapshot_attr: str = "snapshot",
    start_matlab_engine: bool = False,
    matlab_engine_kwargs: Optional[Dict[str, Any]] = None,
)
```

**Parameters:**

- `engine`: An existing MATLAB engine or any object with the required methods (optional).
- `start_fn`: Name of the engine's start method (default: `"start"`).
- `reset_fn`: Name of the engine's reset method (default: `"reset"`).
- `read_fn`: Name of the engine's snapshot read method (default: `"read_snapshot"`).
- `update_fn`: Name of the engine's action update method (default: `"update_action"`).
- `snapshot_attr`: Name of the engine's snapshot attribute (default: `"snapshot"`).
- `start_matlab_engine`: If `True`, automatically start MATLAB engine (default: `False`).
- `matlab_engine_kwargs`: Keyword arguments passed to `matlab.engine.start_matlab()`.

### Expected Snapshot Fields

The MATLAB model is expected to return snapshots containing:

| Field | Type | Description |
|-------|------|-------------|
| `charger_id` | str | Charger identifier |
| `electricity_consumption` | float | Power consumption (kW) |
| `charging_action` | float | Charging action value (kWh) |
| `connected` | bool | Whether EV is connected |
| `soc` | float | Battery state-of-charge (0-1) |
| `thd` | float | Total Harmonic Distortion (%) |
| `time_step` | int | Current time step |

### Bridge Methods

#### `start()`
Start the MATLAB model if available.

```python
bridge.start()
```

#### `reset(payload: Mapping[str, Any]) -> Dict[str, Any]`
Reset the MATLAB model and return initial snapshot.

```python
snapshot = bridge.reset({
    "charger_id": "charger-1",
    "time_step": 0,
    "max_charging_power_kw": 11.0,
    "soc": 0.5,
    ...
})
```

#### `read_snapshot() -> Optional[Mapping[str, Any]]`
Poll and retrieve the latest snapshot from the MATLAB model.

```python
snapshot = bridge.read_snapshot()
if snapshot and "thd" in snapshot:
    thd_value = snapshot["thd"]
```

#### `update_action(payload: Mapping[str, Any])`
Send an action update to the MATLAB model.

```python
bridge.update_action({
    "action_value": 5.5,  # New charging command
})
```

#### `snapshot` Property
Convenience property equivalent to `read_snapshot()`.

```python
current_snapshot = bridge.snapshot
```

## MATLAB Model Implementation Example

Your MATLAB model should implement the following interface:

```matlab
classdef ChargerModel
    methods
        function start(obj)
            % Initialize the MATLAB model
        end
        
        function snapshot = reset(obj, payload)
            % Reset and return initial snapshot
            % payload: struct with charger parameters
            % Returns: struct with snapshot fields
            
            snapshot = struct();
            snapshot.charger_id = payload.charger_id;
            snapshot.electricity_consumption = 0.5;
            snapshot.charging_action = 0.0;
            snapshot.connected = true;
            snapshot.soc = payload.soc;
            snapshot.thd = 0.02;  % 2% THD
            snapshot.time_step = payload.time_step;
        end
        
        function snapshot = read_snapshot(obj)
            % Return the current snapshot
            % This is called repeatedly during simulation
            snapshot = obj.snapshot;
        end
        
        function update_action(obj, payload)
            % Process a new action from CityLearn
            % Compute the power response and internal states
        end
    end
    
    properties
        snapshot  % Current snapshot state
    end
end
```

## Troubleshooting

### MATLAB Engine Not Starting
```python
# Check if MATLAB Engine is installed
try:
    import matlab.engine
    print("MATLAB Engine is available")
except ImportError:
    print("MATLAB Engine not found. Install it from MATLAB's extern/engines/python")
```

### Slow MATLAB Startup
Starting MATLAB can take 10-30 seconds. For better performance:
- Start the engine once and reuse it across multiple simulations
- Use mock mode (`MatlabBridge()` without engine) during development

### Snapshot Field Missing
If a required field is missing from the snapshot:
```python
snapshot = bridge.read_snapshot()
if snapshot is None:
    print("No snapshot available")
elif "thd" not in snapshot:
    print("THD field is missing from MATLAB model snapshot")
```

## Testing

Unit tests use `_FakeMatlabBridge` to avoid requiring MATLAB installation:

```python
from citylearn.utils.matlab_bridge import _FakeMatlabBridge

def test_charger_with_matlab_bridge():
    bridge = _FakeMatlabBridge()
    # ... test code ...
    assert "thd" in bridge.read_snapshot()
```

## Performance Notes

- **Mock mode**: Negligible overhead, suitable for rapid iteration
- **Engine-backed mode**: 1-10ms per snapshot poll, depending on MATLAB model complexity
- **Time step ratio**: The bridge supports different sampling rates between MATLAB and CityLearn via `matlab_step_seconds` parameter in `MatlabCharger`

## See Also

- [MatlabCharger Documentation](citylearn/matlab_ev_charger.py)
- [CityLearn Electric Vehicle Charger](citylearn/electric_vehicle_charger.py)
