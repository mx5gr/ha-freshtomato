"""FreshTomato router API client."""
from __future__ import annotations

import ast
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_NVRAM_OBJ_RE  = re.compile(r"nvram\s*=\s*(\{[^;]+\})\s*;", re.DOTALL)
_NVRAM_PUSH_RE = re.compile(r"tomato_helper\.push\(\['([^']+)','([^']*)'\]\)")

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

# CRITICAL: Use compiled regex + module-level raw-string replacement so the
# backreference \1 is never mangled by string-processing tools.
# Do NOT inline this as a string literal inside a function call.
_UNQUOTED_KEY_RE   = re.compile(r'(?<=[{,])\s*([a-zA-Z_]\w*)\s*:')
_UNQUOTED_KEY_REPL = r'"\1":'

_HEX_INT_RE = re.compile(r'0x([0-9a-fA-F]+)')


@dataclass
class RouterData:
    wireless_clients: list[dict[str, Any]] = field(default_factory=list)
    dhcp_leases:      list[dict[str, Any]] = field(default_factory=list)
    arp_table:        list[dict[str, Any]] = field(default_factory=list)
    netdev:           dict[str, dict[str, int]] = field(default_factory=dict)
    wl_noise:         list[int] = field(default_factory=list)
    nvram:            dict[str, str] = field(default_factory=dict)
    wan_ip:           str = ""
    wan_netmask:      str = ""
    wan_gateway:      str = ""
    wan_uptime:       int = 0
    wan_lease:        int = 0
    eth_ports:        dict[str, str] = field(default_factory=dict)


class FreshTomatoAPI:

    def __init__(self, host, port, http_id, username, password,
                 ssl=False, verify_ssl=True, session=None):
        self._host       = host
        self._port       = port
        self._http_id    = http_id
        self._username   = username
        self._password   = password
        self._ssl        = ssl
        self._verify_ssl = verify_ssl
        self._owns_session = session is None
        self._session    = session or self._make_session()
        scheme           = "https" if ssl else "http"
        self._base_url   = f"{scheme}://{host}:{port}"

    def _make_session(self):
        ssl_param = False if (self._ssl and not self._verify_ssl) else None
        connector = aiohttp.TCPConnector(ssl=ssl_param)
        auth = aiohttp.BasicAuth(self._username, self._password)
        return aiohttp.ClientSession(connector=connector, auth=auth, timeout=REQUEST_TIMEOUT)

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _post_update_cgi(self, body: str) -> str:
        url = f"{self._base_url}/update.cgi"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer":      f"{self._base_url}/status-overview.asp",
        }
        try:
            async with self._session.post(url, data=body, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.text()
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                raise InvalidAuth(f"Auth failed: {err}") from err
            raise CannotConnect(f"HTTP error {err.status}: {err}") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(f"Connection error: {err}") from err

    @classmethod
    def _parse_js_vars(cls, text: str) -> dict[str, Any]:
        """Parse quasi-JS variable assignments from update.cgi responses."""
        result  = {}
        lines   = text.splitlines()
        n_lines = len(lines)
        i       = 0

        while i < n_lines:
            line = lines[i].strip()
            m = re.match(r'^([a-zA-Z_]\w*)\s*=\s*(.*)', line)
            if not m:
                i += 1
                continue

            name = m.group(1)
            rest = m.group(2).rstrip(';').strip()
            depth = rest.count('[') + rest.count('{') - rest.count(']') - rest.count('}')

            if depth <= 0:
                raw = rest
            else:
                parts = [rest]
                i += 1
                while i < n_lines and depth > 0:
                    nl = lines[i].rstrip(';').strip()
                    depth += nl.count('[') + nl.count('{') - nl.count(']') - nl.count('}')
                    parts.append(nl)
                    i += 1
                raw = ' '.join(parts)

            raw = (raw
                   .replace('true',  'True')
                   .replace('false', 'False')
                   .replace('null',  'None')
                   .replace("'",     '"'))

            # Quote unquoted JS object keys using pre-compiled regex + raw-string repl.
            # This is the ONLY safe way — inlining r'"\1":' in a sub() call is
            # fine in source code but previous sessions embedded a literal \x01
            # control character here which corrupted every parsed object.
            raw = _UNQUOTED_KEY_RE.sub(_UNQUOTED_KEY_REPL, raw)
            raw = _HEX_INT_RE.sub(lambda mo: str(int(mo.group(1), 16)), raw)

            try:
                result[name] = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                result[name] = raw.strip('"').strip("'")

            i += 1

        return result

    async def test_connection(self) -> bool:
        await self._post_update_cgi(f"_http_id={self._http_id}&exec=devlist")
        return True

    async def fetch_devlist(self) -> dict[str, Any]:
        """Fetch exec=devlist — returns wldev, wlnoise, dhcpd_lease, arplist,
        nvram subset, etherstates, gc_time in one response."""
        text = await self._post_update_cgi(f"_http_id={self._http_id}&exec=devlist")
        return self._parse_js_vars(text)

    async def fetch_netdev(self) -> dict[str, Any]:
        """Fetch exec=netdev — per-interface byte counters."""
        text   = await self._post_update_cgi(f"_http_id={self._http_id}&exec=netdev")
        parsed = self._parse_js_vars(text)
        netdev = parsed.get("netdev", {})
        if isinstance(netdev, dict) and netdev:
            first = next(iter(netdev.values()), {})
            if isinstance(first, dict) and all(k in ("rx", "tx", "rxp", "txp") for k in first):
                return {"netdev": netdev}
        return {"netdev": self._parse_netdev_raw(text)}

    @staticmethod
    def _parse_netdev_raw(text: str) -> dict[str, dict[str, int]]:
        """Fallback regex-based netdev parser for binary/non-standard formats."""
        result = {}
        for im in re.finditer(r'"(\w+)"\s*:\s*\{([^}]*)\}', text):
            iface, block = im.group(1), im.group(2)
            counters = {}
            for fm in re.finditer(r'\b(rx|tx|rxp|txp)\b[\s:;\x01]+(0x[0-9a-fA-F]+|\d+)', block):
                k, v = fm.group(1), fm.group(2)
                counters[k] = int(v, 16) if v.startswith("0x") else int(v)
            if counters:
                result[iface] = counters
        return result

    async def fetch_nvram(self, variables: list[str]) -> dict[str, str]:
        """Try exec=nvram. Returns empty dict if unsupported."""
        body   = f"_http_id={self._http_id}&exec=nvram&_nvram={'+'.join(variables)}"
        text   = await self._post_update_cgi(body)
        parsed = self._parse_js_vars(text)
        nvram  = parsed.get("nvram", {})
        if isinstance(nvram, dict):
            return {k: str(v) for k, v in nvram.items()}
        return {}

    async def fetch_nvram_from_asp(self, variables: list[str]) -> dict[str, str]:
        """Scrape nvram from status-data.jsx."""
        url     = f"{self._base_url}/status-data.jsx"
        headers = {"Referer": f"{self._base_url}/status-overview.asp"}
        try:
            async with self._session.get(
                url, params={"_http_id": self._http_id}, headers=headers
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                raise InvalidAuth from err
            raise CannotConnect from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect from err

        result = {}
        mo = _NVRAM_OBJ_RE.search(text)
        if mo:
            raw = (mo.group(1)
                   .replace("true", "True").replace("false", "False")
                   .replace("null", "None").replace("'", '"'))
            raw = _UNQUOTED_KEY_RE.sub(_UNQUOTED_KEY_REPL, raw)
            try:
                data = ast.literal_eval(raw)
                if isinstance(data, dict):
                    result = {k: str(v) for k, v in data.items() if k in variables}
            except (ValueError, SyntaxError):
                pass
        for key, val in _NVRAM_PUSH_RE.findall(text):
            if key in variables:
                result[key] = val
        return result

    async def fetch_firmware_version(self) -> str | None:
        """Try exec=nvram for firmware build strings."""
        for var in ("t_build_time", "t_build", "os_version", "tomato_version"):
            try:
                text   = await self._post_update_cgi(
                    f"_http_id={self._http_id}&exec=nvram&_nvram={var}")
                parsed = self._parse_js_vars(text)
                nvram  = parsed.get("nvram", {})
                if isinstance(nvram, dict) and nvram.get(var):
                    return str(nvram[var])
            except (CannotConnect, InvalidAuth):
                return None
        return None

    async def fetch_etherstates(self) -> dict[str, str]:
        """Fetch physical Ethernet port states via exec=etherstates.

        Returns a dict like {"port0": "1000FD", "port1": "DOWN", ...}.
        Only keys matching portN (N = digit) are returned; binary/garbage
        keys from unsupported builds are silently dropped.
        Keys with value "disabled" indicate ports absent on this hardware.
        """
        body   = f"_http_id={self._http_id}&exec=etherstates"
        text   = await self._post_update_cgi(body)
        parsed = self._parse_js_vars(text)
        raw    = parsed.get("etherstates", {})
        if not isinstance(raw, dict):
            return {}
        _VALID = re.compile(r'^port\d+$')
        return {k: str(v) for k, v in raw.items()
                if isinstance(k, str) and _VALID.match(k)}

    async def fetch_about_page(self) -> str | None:
        """Fetch firmware version string from /about.asp.

        The version is embedded in every CSS/JS asset URL as a cache-busting
        query parameter, e.g.:
            <link href="tomato.css?rel=2026.1">
        We extract that value and return it as "FreshTomato YYYY.N".

        /about.asp is the only page we need — no fallback to status-overview.asp.
        """
        try:
            url = f"{self._base_url}/about.asp"
            headers = {"Referer": f"{self._base_url}/status-overview.asp"}
            async with self._session.get(
                url,
                params={"_http_id": self._http_id},
                headers=headers,
            ) as resp:
                if resp.status not in (200, 304):
                    _LOGGER.debug("fetch_about_page: HTTP %d", resp.status)
                    return None
                text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("fetch_about_page: error=%s", err)
            return None

        # Primary: version from ?rel=YYYY.N in any asset href/src
        m = re.search(r'\?rel=([\d.]+)', text)
        if m:
            return f"FreshTomato {m.group(1)}"

        # Fallback: explicit version string in page body
        m = re.search(
            r"(FreshTomato[\s/]+[\d.]+[^\s<\"']*|Tomato\s+v[\d.]+[^\s<\"']*)",
            text, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        _LOGGER.debug("fetch_about_page: no version found in about.asp")
        return None

    async def toggle_wifi_radio(self, unit: int, enable: bool) -> None:
        url  = f"{self._base_url}/wlradio.cgi"
        body = (f"_http_id={self._http_id}&enable={'1' if enable else '0'}"
                f"&_wl_unit={unit}&_nextpage=status-overview.asp&_nextwait=5")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer":      f"{self._base_url}/status-overview.asp",
        }
        try:
            async with self._session.post(url, data=body, headers=headers) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect from err


class CannotConnect(Exception):
    """Raised when the router cannot be reached."""

class InvalidAuth(Exception):
    """Raised when credentials or http_id are invalid."""
