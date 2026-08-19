# <img width="50" height="50" align="absmiddle" alt="logo" src="https://raw.githubusercontent.com/ludero-git/ha-greenflux/main/custom_components/greenflux/brand/icon.png" /> GreenFlux for Home Assistant

Home Assistant custom integration for GreenFlux CPO platforms.

[![Latest Release](https://img.shields.io/github/v/release/ludero-git/ha-greenflux?display_name=tag\&sort=semver)](https://github.com/ludero-git/ha-greenflux/releases/latest)

## Installation

### Step 1: Install

#### Via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ludero-git&repository=ha-greenflux&category=integration)

1. Click "Download" to install.
2. Restart Home Assistant.

#### Manually

1. Copy `custom_components/greenflux` to `<config>/custom_components/greenflux`.
2. Restart Home Assistant.

### Step 2: Configure

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=greenflux)

Or manually: Go to **Settings > Devices & services > Add integration > GreenFlux**.

1. Enter the GreenFlux API URL and API token.
2. Enable automatic charger creation or manually add chargers by ID.

Automatic charger creation is disabled by default because large GreenFlux platforms may consume additional API capacity during initial setup.

## Rate limiting design

The integration avoids per-charger and per-socket polling.

* All charge stations, including newly added stations, are fetched once per hour using the bulk [Charge Stations endpoint](https://developer.greenflux.com/reference/chargestations_getallchargestations).
* Charge station notifications are fetched once per minute using a single bulk [Charge Station Notifications request](https://developer.greenflux.com/reference/chargestationnotifications_getchargestationnotifications) per GreenFlux platform.
* MeterValues are fetched once per minute using a single bulk request per GreenFlux platform.
* Notification and MeterValues responses are filtered locally for configured chargers and sockets.
* The notification polling updates active RFID Tag IDs and session state.
* MeterValues update current power, cumulative energy usage, and active GreenFlux session IDs when available.
* Short overlapping time windows are used for notifications and MeterValues to reduce the chance of missing delayed records between polling cycles.
* Automatic charger creation reuses the initial charge-station response instead of immediately fetching the same data again.
* Adding a charger manually performs one single-station request for validation using the [Charge Station endpoint](https://developer.greenflux.com/reference/chargestations_getchargestationbyid).
* Stop Session uses the session ID already obtained from live MeterValues when available. An additional MeterValues request is only used as a fallback when the active session ID is missing.
* HTTP `429` responses are surfaced through Home Assistant's coordinator handling and `Retry-After` is respected when GreenFlux provides it.

In normal operation, live data therefore requires two bulk requests per configured GreenFlux platform per minute, regardless of the number of chargers or sockets.

GreenFlux API documentation: https://developer.greenflux.com/

## License

[MIT](/LICENSE)
