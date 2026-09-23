"""Manage the robot's Wi-Fi over SSH, using NetworkManager's `nmcli`.

Plain Python, no Tk: WifiManager takes the paramiko SSHClient and runs
`sudo nmcli ...` on the robot. It's kept apart from the tab (tabs/wifi_tab.py)
so the parsing and command building can be tested without a robot or a window.

The robot needs NetworkManager (nmcli) and passwordless sudo for the SSH user.

A note on secrets: a Wi-Fi password travels as an nmcli argument, so it's
briefly visible in the robot's process list. That's acceptable on a
single-user robot; it is never written to a log by this app.
"""

import re
import shlex

# New networks are ranked between a home network (10 in the reference setup)
# and a fallback hotspot (0), so the robot prefers home but beats the hotspot.
DEFAULT_PRIORITY = 5

WIFI_TYPES = {"wifi", "802-11-wireless"}  # `connection show` says "802-11-wireless" in terse mode

# How a network is joined.
OPEN, WPA_PSK, SAE, ENTERPRISE, UNSUPPORTED = "open", "wpa-psk", "sae", "enterprise", "unsupported"

KIND_LABELS = {
    WPA_PSK: "WPA / WPA2 password",
    SAE: "WPA3 password",
    OPEN: "Open (no password)",
}


class WifiError(Exception):
    """A Wi-Fi action failed. The message is fit to show the user."""


class LinkLost(WifiError):
    """The SSH link dropped while a command ran.

    Expected when the robot leaves the network we are connected over.
    """


# ---------------- Parsing nmcli output ----------------
def split_terse(line):
    """Split one line of `nmcli -t` output. ':' separates fields; a literal ':'
    or '\\' inside a value is escaped with a backslash."""
    fields, current, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


def classify_security(text):
    """Map a scan row's SECURITY column to how we would join the network."""
    t = text.strip().upper()
    if t in ("", "--"):
        return OPEN
    if "802.1X" in t:
        return ENTERPRISE
    if "WEP" in t or "OWE" in t:
        return UNSUPPORTED
    if "WPA3" in t and "WPA2" not in t and "WPA1" not in t:
        return SAE
    if "WPA" in t:
        return WPA_PSK
    return UNSUPPORTED


def parse_scan(text):
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list` -> rows, best signal first."""
    best = {}
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) < 4 or not fields[1].strip():  # hidden networks broadcast no name (or just blanks)
            continue
        in_use, ssid, signal, security = fields[0], fields[1], fields[2], fields[3]
        try:
            strength = int(signal)
        except ValueError:
            strength = 0
        currently_used = in_use.strip() == "*"
        if security.strip() not in ("", "--"):
            label = security.strip()
        else:
            # nmcli leaves SECURITY empty for the network we're on, so "Open" would be a guess
            label = "in use" if currently_used else "Open"
        row = {
            "ssid": ssid,
            "signal": strength,
            "security": label,
            "kind": classify_security(security),
            "in_use": currently_used,
        }
        previous = best.get(ssid)
        if previous is None:
            best[ssid] = row
        else:  # same network seen on several access points: keep the strongest
            in_use_any = previous["in_use"] or row["in_use"]
            if row["signal"] > previous["signal"]:
                best[ssid] = row
            best[ssid]["in_use"] = in_use_any
    return sorted(best.values(), key=lambda r: (not r["in_use"], -r["signal"], r["ssid"].lower()))


def parse_connections(text):
    """`nmcli -t -f NAME,UUID,TYPE,DEVICE connection show` -> Wi-Fi profiles only."""
    profiles = []
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) >= 4 and fields[2] in WIFI_TYPES:
            profiles.append({"name": fields[0], "uuid": fields[1], "device": fields[3]})
    return profiles


def parse_status(text):
    """`nmcli -t -f GENERAL.CONNECTION,IP4.ADDRESS device show DEV` -> current connection and IP."""
    info = {"connection": "", "ip": ""}
    for line in text.splitlines():
        fields = split_terse(line)
        key, value = fields[0], ":".join(fields[1:])
        if key == "GENERAL.CONNECTION" and value != "--":
            info["connection"] = value
        elif key.startswith("IP4.ADDRESS") and not info["ip"]:
            info["ip"] = value.split("/")[0]
    return info


# ---------------- Building commands ----------------
def nmcli_cmd(*args):
    """A remote command line for `sudo nmcli ...`, with every argument shell-quoted.

    `env LC_ALL=C` keeps nmcli's output in English whatever the robot's locale.
    """
    return " ".join(shlex.quote(part) for part in ("sudo", "-n", "env", "LC_ALL=C", "nmcli", *args))


def _security_args(kind, password):
    if kind == OPEN:
        return []
    return ["wifi-sec.key-mgmt", kind, "wifi-sec.psk", password]


def validate_network(ssid, password, kind):
    """Raise WifiError, with a plain-language reason, if these can't be saved."""
    if not ssid:
        raise WifiError("Enter the network name (SSID).")
    if len(ssid.encode("utf-8")) > 32:
        raise WifiError("A Wi-Fi network name can't be longer than 32 bytes.")
    if "\x00" in ssid or "\x00" in password:
        raise WifiError("The name and password can't contain null characters.")
    if kind == ENTERPRISE:
        raise WifiError("Enterprise networks (WPA-Enterprise / 802.1X, common at schools) aren't supported here.")
    if kind == UNSUPPORTED:
        raise WifiError("This network's security type isn't supported here.")
    if kind == WPA_PSK:
        is_hex_key = len(password) == 64 and re.fullmatch(r"[0-9A-Fa-f]{64}", password)
        if not (8 <= len(password) <= 63 or is_hex_key):
            raise WifiError("A WPA/WPA2 password must be 8 to 63 characters.")
    elif kind == SAE and not password:
        raise WifiError("Enter the network's password.")


def _friendly_error(text):
    message = text.strip()
    lowered = message.lower()
    if "a password is required" in lowered or "a terminal is required" in lowered:
        return ("The robot needs passwordless sudo for Wi-Fi changes, but sudo asked for a password. "
                "Set that up on the robot, or run the nmcli command there yourself.")
    if "nmcli" in lowered and ("not found" in lowered or "no such file" in lowered):
        return "NetworkManager (nmcli) isn't installed on the robot."
    return message.splitlines()[-1] if message else "The robot reported an error without details."


# ---------------- Talking to the robot ----------------
class WifiManager:
    def __init__(self, client):
        self.client = client
        self._device = None

    def _run(self, command, timeout=20):
        try:
            _, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            code = stdout.channel.recv_exit_status()
        except Exception as e:  # timeout, EOF, SSH error: the link went away
            raise LinkLost(str(e) or type(e).__name__) from e
        if code != 0:
            raise WifiError(_friendly_error(err or out))
        return out

    def wifi_device(self):
        if self._device is None:
            out = self._run(nmcli_cmd("-t", "-f", "DEVICE,TYPE", "device", "status"))
            for line in out.splitlines():
                fields = split_terse(line)
                if len(fields) >= 2 and fields[1] == "wifi":
                    self._device = fields[0]
                    break
            else:
                raise WifiError("NetworkManager doesn't see a Wi-Fi adapter on the robot.")
        return self._device

    def saved_networks(self):
        """Saved Wi-Fi profiles. `hotspot` marks an access-point profile: the robot's fallback."""
        listing = self._run(nmcli_cmd("-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"))
        networks = []
        for profile in parse_connections(listing):
            details = self._run(nmcli_cmd(
                "-g", "802-11-wireless.ssid,802-11-wireless.mode,connection.autoconnect-priority",
                "connection", "show", "uuid", profile["uuid"],
            )).split("\n")
            details += [""] * (3 - len(details))
            try:
                priority = int(details[2])
            except ValueError:
                priority = 0
            networks.append({
                "name": profile["name"],
                "uuid": profile["uuid"],
                "ssid": details[0] or profile["name"],
                "hotspot": details[1].strip() == "ap",
                "active": bool(profile["device"]),
                "priority": priority,
            })
        return sorted(networks, key=lambda n: (not n["active"], -n["priority"], n["name"].lower()))

    def status(self, saved=None):
        """Where the robot's Wi-Fi is right now: {device, connection, ip, hotspot}."""
        device = self.wifi_device()
        info = parse_status(self._run(nmcli_cmd("-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", device)))
        if saved is None:
            saved = self.saved_networks()
        info["device"] = device
        info["hotspot"] = any(n["hotspot"] and n["name"] == info["connection"] for n in saved)
        return info

    def scan(self, rescan=True):
        """Nearby networks. Pass rescan=False while the robot hosts its hotspot,
        since asking the radio to scan then can interrupt the hotspot."""
        out = self._run(nmcli_cmd(
            "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
            "ifname", self.wifi_device(), "--rescan", "yes" if rescan else "no",
        ), timeout=40)
        return parse_scan(out)

    def save_network(self, ssid, password, kind, hidden=False, priority=DEFAULT_PRIORITY, saved=None):
        """Add (or update) a saved network. Doesn't connect to it.

        Returns the saved profile, with "created" telling whether this call added it.
        """
        validate_network(ssid, password, kind)
        if saved is None:
            saved = self.saved_networks()
        existing = next((n for n in saved if n["name"] == ssid), None)
        if existing and existing["hotspot"]:
            raise WifiError(f"'{ssid}' is the robot's own hotspot profile. Choose a different network.")
        if existing and kind == OPEN:
            raise WifiError(f"'{ssid}' is already saved. Forget it first if you want to re-add it as an open network.")

        common = [
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", str(priority),
            "802-11-wireless.hidden", "yes" if hidden else "no",
        ] + _security_args(kind, password)
        if existing:
            self._run(nmcli_cmd("connection", "modify", "uuid", existing["uuid"], *common))
        else:
            self._run(nmcli_cmd(
                "connection", "add", "type", "wifi", "ifname", self.wifi_device(),
                "con-name", ssid, "ssid", ssid, *common,
            ))
        profile = next((n for n in self.saved_networks() if n["name"] == ssid), None)
        if profile is None:
            raise WifiError("The network was saved, but the robot didn't list it afterwards.")
        return {**profile, "created": existing is None}

    def connect(self, profile):
        """Switch the robot's Wi-Fi to a saved network.

        If the robot is reached over Wi-Fi, this normally cuts our own SSH link,
        which surfaces as LinkLost. NetworkManager falls back to the hotspot
        profile if the new network can't be joined.
        """
        self._run(nmcli_cmd("-w", "25", "connection", "up", "uuid", profile["uuid"]), timeout=35)

    def forget(self, profile):
        if profile["hotspot"]:
            raise WifiError("That's the robot's hotspot, its fallback when no known network is in range. "
                            "It can't be removed from here.")
        self._run(nmcli_cmd("connection", "delete", "uuid", profile["uuid"]))
