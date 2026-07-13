# AGENTS.md for ha-ambient-local

## OVERVIEW
Integration between Home Assistant and Ambient Weather console for local API communication.

## STRUCTURE
```
custom_components
└── ambient_local
    ├── __init__.py      # Main integration setup and entry points
    ├── config_flow.py   # Configuration flow for setup and options
    ├── console.py       # Client for the console's HTTP API
    ├── const.py         # Constants utilized across the integration
    ├── coordinator.py    # Data management and caching functionality
    ├── entity.py        # Base entity definitions for Home Assistant
    ├── listener.py      # HTTP listener for push updates
    └── sensor.py        # Defines sensor entities
```

## WHERE TO LOOK
| Task                               | Location                                |
|------------------------------------|----------------------------------------|
| Setup integration                  | custom_components/ambient_local/__init__.py |
| Configuration flow                 | custom_components/ambient_local/config_flow.py |
| Console API interaction            | custom_components/ambient_local/console.py |
| Sensor definitions and logic       | custom_components/ambient_local/sensor.py |

## CODE MAP
- **`__init__.py`**: Initializes integration, handles setup and unloading of platforms.
- **`config_flow.py`**: Manages user configuration and provisioning setup.
- **`console.py`**: Handles API calls to the Ambient Weather console.
- **`coordinator.py`**: Coordinates data fetching and caching for the integration.
- **`listener.py`**: Listens for data updates from the console via HTTP push.
- **`sensor.py`**: Defines various sensor entities based on console data.

## CONVENTIONS
- Use `async/await` for asynchronous operations.
- Constants in `const.py` are prefixed with `CONF_` for clarity in config-related usage.

## ANTI-PATTERNS / GOTCHAS
- **Service Registration**: Ensure that services are not re-registered upon reloading entries, check for existing service definition first.
- **Exception Handling**: Always catch specific exceptions to avoid masking other errors, especially in async contexts.

## COMMANDS
- **Run listener**: Ensure console settings are always reapplied by using the service `reapply_console_settings`.

## NOTES
- Use the provided constants for consistent configuration keys.
- Debugging should log errors using `_LOGGER` for easier tracking.