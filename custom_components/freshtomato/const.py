"""Constants for the FreshTomato integration."""
from __future__ import annotations

DOMAIN = "freshtomato"

# Config entry keys
CONF_HTTP_ID = "http_id"
CONF_TRACK_WIRED = "track_wired"

# Defaults
DEFAULT_PORT = 80
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_TRACK_WIRED = True
DEFAULT_USERNAME = "admin"

# Coordinator update keys
DATA_COORDINATOR = "coordinator"

# update.cgi exec targets — ONE call fetches multiple data blobs at once
# FreshTomato supports combining them with a single POST body using multiple
# "exec" fields, but the safest/most compatible approach is two calls:
#   1. exec=devlist  → wlnoise[], wldev[], dhcpd_lease[], arp[], active WAN stats
#   2. exec=netdev   → real-time interface byte counters (tx/rx)
# The status-overview ASP page also loads nvram vars once at page-load time.
# We replicate that with a targeted nvram POST. Total: 3 HTTP calls per cycle.

EXEC_DEVLIST = "devlist"
EXEC_NETDEV = "netdev"

# NVRAM variables to fetch in one call from update.cgi?exec=nvram
# These are mostly static (router name, firmware, WAN config) and are fetched
# once on startup plus every STATUS_NVRAM_INTERVAL cycles.
NVRAM_VARS = [
    "t_model_name",
    "t_build_time",
    "os_version",
    "wan_ipaddr",
    "wan_netmask",
    "wan_gateway",
    "wan_proto",
    "wan_dns",
    "wan_ifname",
    "wan_ifnames",
    "wan_lease",
    "wan_get_dns",
    "ppp_get_ip",
    "lan_ipaddr",
    "lan1_ipaddr",
    "lan_netmask",
    "lan_hostname",
    "wl0_ssid",
    "wl0_channel",
    "wl0_radio",
    "wl0_mode",
    "wl0_net_mode",
    "wl0_security_mode",
    "wl0_closed",
    "wl1_ssid",
    "wl1_channel",
    "wl1_radio",
    "wl1_mode",
    "wl1_net_mode",
    "wl1_security_mode",
    "wl1_closed",
    "http_id",
    "uptime",
    "cpu_temp",
    "t_cpu_temp",
]

# How many poll cycles between full NVRAM refreshes (semi-static data)
NVRAM_REFRESH_INTERVAL = 10

# Platform constants
PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "device_tracker"]
