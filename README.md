# ha-freshtomato

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Home Assistant custom integration for routers running [FreshTomato](https://freshtomato.org/) firmware. Monitor your router's network status, control Wi-Fi radios, and track connected devices — all locally, with no cloud dependency.

---

## Features

- **Local polling** — communicates directly with your router's `update.cgi` API, no external services
- **Full UI setup** — configured entirely through the HA UI, no YAML required
- **Dual-stack support** — works over HTTP or HTTPS, with optional SSL certificate verification bypass
- **Bridge / AP mode aware** — correctly handles routers in Wireless Ethernet Bridge (WET) or AP mode where there is no dedicated WAN connection
- **Device tracking** — tracks both wireless and wired (DHCP) clients from a single HTTP call per cycle

---

## Entities

### Sensors

| Entity | Description |
|---|---|
| **WAN IP Address** | Current WAN IP |
| **WAN Gateway** | Upstream gateway IP |
| **WAN Connection Type** | WAN protocol (`dhcp`, `pppoe`, `disabled`, …) |
| **WAN Uptime** | Seconds since WAN connected *(unavailable in bridge/AP mode)* |
| **WAN DHCP Lease Remaining** | Remaining DHCP lease time *(unavailable in bridge/AP mode)* |
| **WAN Download (total)** | Total bytes received on WAN interface |
| **WAN Upload (total)** | Total bytes transmitted on WAN interface |
| **br0 IP Address** | IP of the primary LAN bridge (name reflects actual interface) |
| **br1 IP Address** | IP of the secondary/uplink bridge, if present |
| **Firmware Version** | FreshTomato build version (e.g. `FreshTomato 2026.1`) |
| **Router Model** | Hardware model string from nvram |
| **Total Connected Devices** | Combined unique wireless + wired client count |
| **Wireless Connected Devices** | Active wireless clients |
| **Wired Connected Devices** | Clients with active DHCP leases |
| **2.4 GHz / 5 GHz SSID** | SSID per band |
| **2.4 GHz / 5 GHz Channel** | Active channel per band |
| **2.4 GHz / 5 GHz Mode** | Radio mode (`Access Point`, `Wireless Ethernet Bridge`, `Wireless Client`, …) |
| **2.4 GHz / 5 GHz Noise Floor** | Noise floor in dBm |
| **2.4 GHz / 5 GHz Security Mode** | Security mode (`wpa2`, `open`, …) |
| **WAN Port** | Physical WAN port: speed + duplex (e.g. `1 Gbps, Full Duplex`) |
| **LAN0 – LAN3 Port** | Physical LAN port: speed + duplex per port |

### Binary Sensors

| Entity | Description |
|---|---|
| **WAN Connected** | On when WAN IP is present |
| **2.4 GHz / 5 GHz SSID Broadcast** | On when SSID is visible (not hidden) |
| **Wireless Client Mode** | On when router operates as a wireless client or bridge |
| **WAN Link** | Physical WAN port link up/down |
| **LAN0 – LAN3 Link** | Physical LAN port link up/down, one entity per port |

### Controls

| Entity | Type | Description |
|---|---|---|
| **2.4 GHz Radio** | Switch | Enable / disable the 2.4 GHz radio |
| **5 GHz Radio** | Switch | Enable / disable the 5 GHz radio |
| **Reboot Router** | Button | Sends a reboot command |
| **Renew WAN DHCP Lease** | Button | Forces a DHCP lease renewal |

### Device Tracker

One `device_tracker` entity per discovered client MAC address. Wireless and wired clients are both sourced from the same `exec=devlist` API call. Wired tracking can be toggled independently in the integration's options.

---

## Requirements

- Home Assistant 2024.1 or later
- A router running [FreshTomato](https://freshtomato.org/) firmware
- The router's **HTTP ID** (CSRF token) — find it at `Administration → Admin Access → Web Admin ID` in the FreshTomato UI

---

## Installation

### Via HACS (recommended)

1. In HACS go to **Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/mx5gr/ha-freshtomato` with category **Integration**
3. Click **Download**
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/freshtomato/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **FreshTomato**
3. Fill in the connection details:

| Field | Description |
|---|---|
| **Host** | Router IP or hostname (e.g. `192.168.0.1`) |
| **Port** | HTTP port — default `80` |
| **SSL** | Enable for HTTPS |
| **Verify SSL** | Uncheck for self-signed certificates |
| **Username** | Router admin username — default `admin` |
| **Password** | Router admin password |
| **HTTP ID** | Web Admin ID from `Administration → Admin Access` in FreshTomato |

### Options

Click **Configure** on the integration card after setup to adjust:

| Option | Description |
|---|---|
| **Scan interval** | Poll frequency in seconds (default 30, min 10, max 600) |
| **Track wired devices** | Include DHCP clients in device tracking |

---

## Notes

### Bridge / AP mode
When WAN protocol is `disabled` (Wireless Ethernet Bridge or pure AP mode), **WAN Uptime** and **WAN DHCP Lease Remaining** show as **Unavailable** — this is correct, not an error. All other entities work normally.

### Physical Ethernet ports
Port entities (WAN, LAN0–LAN3) are created dynamically on the first poll. Each shows link state as a human-readable string including speed and duplex — for example `1 Gbps, Full Duplex` or `Disconnected`. Ports reported as `disabled` by the firmware are not created. The binary sensor companion entity for each port shows raw link up/down state.

### Finding the HTTP ID
In FreshTomato navigate to `Administration → Admin Access`. The **Web Admin ID** field contains a token like `TIDxxxxxxxxxxxxxxxx`. This is required for every API call and cannot be omitted.

---

## Supported Firmware

Tested on FreshTomato 2026.1 (ARM) and HA 2026.2.3 . The integration targets the `update.cgi` API which has been stable across all FreshTomato and upstream Tomato versions. Older builds may not expose all nvram variables; missing sensors degrade gracefully to Unavailable rather than causing errors.

---

## License

MIT — see [LICENSE](LICENSE) for details.
