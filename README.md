# vpn-checker

A lightweight Python command-line tool that helps QA engineers verify that a VPN is doing its job: changing your IP, protecting your DNS, blocking IPv6 leaks, and not exposing your real address when the connection drops.

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Android%20(Termux)-lightgrey)

---

## Table of contents

- [Why this tool](#why-this-tool)
- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Example output](#example-output)
- [Exit codes](#exit-codes)
- [Test scenarios](#test-scenarios)
- [Using it in CI](#using-it-in-ci)
- [Running on mobile](#running-on-mobile)
- [Privacy and security notes](#privacy-and-security-notes)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Why this tool

A VPN can look connected while still leaking. Your IP may change while your DNS queries still go to your ISP, or your real IPv6 address may stay visible, or traffic may flow unprotected for a few seconds after the tunnel drops.

Checking this by hand with several websites is slow and hard to repeat. `vpn-checker` records a baseline of your real network identity, then compares it against the VPN-connected state and tells you exactly what passed or failed.

## Features

- **Baseline capture** of your real IP, country, ISP, IPv6 address, and local DNS servers
- **IP check** to confirm the public IP changed
- **Country check** to confirm the exit location matches the server you selected
- **ISP/org check** to confirm traffic no longer appears to come from your real provider
- **DNS leak test** using a real resolver-detection API, not just local config
- **IPv6 leak test** to detect your real IPv6 address being exposed
- **Local DNS config check** to see whether your original DNS servers are still configured
- **Live leak monitor** (`watch`) for kill switch, network switch, and sleep/wake tests
- **Repeat runner** (`loop`) for flakiness and regression testing
- **JSON reports** you can attach to bug tickets
- **CI-friendly exit codes**
- **Single file, one dependency** (`requests`)

## How it works

1. With the VPN **off**, `baseline` stores your real network identity in `vpn_baseline.json`.
2. With the VPN **on**, `check` queries public IP and DNS services and compares the results to that baseline.
3. While you break the connection on purpose (kill the process, switch networks, toggle airplane mode), `watch` polls your public IP and flags any moment your real IP reappears.

| State | Meaning |
|---|---|
| `OK` | Traffic flows through an IP that is not your baseline IP |
| `DOWN` | No connectivity (expected behavior when a kill switch works) |
| `LEAK` | Traffic flows and the IP equals your real baseline IP (failed test) |

## Requirements

- Python 3.8 or newer
- Internet access
- A VPN client you want to test
- The `requests` library

## Installation

```bash
git clone https://github.com/<your-username>/vpn-checker.git
cd vpn-checker
pip install requests
```

Or, using a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install requests
```

Optionally create a `requirements.txt`:

```text
requests>=2.28
```

## Quick start

```bash
# 1. VPN disconnected: record your real network identity
python vpn_checker.py baseline

# 2. Connect your VPN to a country (for example Germany), then:
python vpn_checker.py check --country DE

# 3. Test the kill switch: start the monitor, then kill the VPN or switch networks
python vpn_checker.py watch --duration 60
```

## Commands

### `baseline`

Records your real IP, country, ISP, IPv6 address, and local DNS servers. Run it with the VPN **disconnected**.

```bash
python vpn_checker.py baseline
```

Writes `vpn_baseline.json` in the current directory.

### `check`

Runs all verification checks while the VPN is **connected**.

```bash
python vpn_checker.py check [--country CODE] [--out FILE]
```

| Option | Description |
|---|---|
| `--country CODE` | Expected 2-letter country code of the VPN server (e.g. `DE`, `US`, `JP`). If omitted, the country check is skipped. |
| `--out FILE` | Save a JSON report to this file. |

Checks performed:

| # | Check | Pass condition | Fail / warn condition |
|---|---|---|---|
| 1 | Public IP | Differs from baseline IP | Same as baseline (FAIL) |
| 2 | Country | Matches `--country` | Mismatch (FAIL) |
| 3 | Org / ISP | Differs from baseline org | Looks like your real ISP (FAIL) |
| 4 | DNS leak | No resolver belongs to your real ISP | Resolver owned by real ISP (FAIL), resolver in your real country (WARN) |
| 5 | Local DNS config | Original DNS servers not configured | Original servers still present (WARN) |
| 6 | IPv6 | No IPv6 reachable | Same IPv6 as baseline (FAIL), different IPv6 (WARN, verify it belongs to the VPN) |

### `watch`

Polls your public IP at a fixed interval to catch leaks during disruptive tests. Output is printed only when the state changes, so the log stays readable.

```bash
python vpn_checker.py watch [--duration SECONDS] [--interval SECONDS] [--out FILE]
```

| Option | Default | Description |
|---|---|---|
| `--duration` | `60` | How long to monitor, in seconds |
| `--interval` | `1.0` | Seconds between samples |
| `--out` | none | Save the full timeline as JSON |

### `loop`

Runs `check` repeatedly, useful for catching intermittent failures. It does **not** connect or disconnect the VPN itself. Reconnect between runs manually or with your own script.

```bash
python vpn_checker.py loop [--count N] [--delay SECONDS] [--country CODE] [--out FILE]
```

| Option | Default | Description |
|---|---|---|
| `--count` | `5` | Number of runs |
| `--delay` | `10` | Seconds to wait between runs |
| `--country` | none | Expected country code |
| `--out` | none | JSON report path (overwritten on each run, so it holds the last run) |

## Example output

> The output below is illustrative.

```text
$ python vpn_checker.py check --country DE
Baseline taken at 2026-11-23T10:15:02

[INFO] Current IP: 185.220.x.x  Country: DE  City: Frankfurt  Org: AS9009 M247 Europe SRL
[PASS] Public IP changed
[PASS] Country matches expected (DE)
[PASS] Org/ISP differs from baseline

Running DNS leak test (takes a few seconds)...
[INFO] Resolver 185.220.x.x  Germany  AS9009 M247 Europe SRL
[PASS] No DNS leak detected
[INFO] Local DNS servers: 10.8.0.1
[PASS] Local DNS config differs from baseline
[PASS] No IPv6 connectivity (blocked or unavailable) - no IPv6 leak

Summary: 0 FAIL, 0 WARN
Note: also check WebRTC manually at browserleaks.com/webrtc (needs a browser).
```

Kill switch test:

```text
$ python vpn_checker.py watch --duration 30
Watching for 30s (interval 1.0s). Baseline IP: 103.x.x.x
Now trigger your test (kill VPN, switch network, sleep/wake...). Ctrl+C to stop.

[PASS] t=   0.0s  OK   ip=185.220.x.x
[WARN] t=  12.4s  DOWN ip=None
[PASS] t=  19.8s  OK   ip=185.220.x.x

Samples: 30 | OK: 22 | DOWN: 8 | LEAK: 0
[PASS] Real IP was never exposed
```

A failing kill switch looks like this:

```text
[FAIL] t=  12.4s  LEAK ip=103.x.x.x
[FAIL] Real IP was exposed during the test - kill switch / reconnect failed
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed (warnings are allowed) |
| `1` | At least one check failed, or a fatal error occurred (for example, no baseline file) |

## Test scenarios

| Scenario | How to run it |
|---|---|
| Basic connection | `baseline`, connect, `check --country XX` |
| Different servers and protocols | Repeat `check` for each country and protocol (OpenVPN, WireGuard, IKEv2) |
| Kill switch | Run `watch`, kill the VPN process or tunnel, confirm `DOWN` and never `LEAK` |
| Wi-Fi to mobile data switch | Run `watch`, switch networks, confirm no `LEAK` during reconnect |
| Sleep / wake | Run `watch`, sleep the device, wake it, confirm no `LEAK` |
| Airplane mode | Run `watch`, toggle airplane mode on and off |
| Reconnect flakiness | `loop --count 20`, reconnecting between runs |
| IPv6 handling | Test on an IPv6-enabled network and confirm `check` reports no leak |

## Using it in CI

Because the exit code is non-zero on failure, you can use it as a gate. VPN tests change routing and DNS, so run them on an **isolated VM or self-hosted runner**, never on a shared machine.

Example GitHub Actions job on a self-hosted runner:

```yaml
name: vpn-smoke-test
on:
  workflow_dispatch:
  schedule:
    - cron: "0 2 * * *"

jobs:
  vpn-check:
    runs-on: [self-hosted, vpn-test]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install requests

      - name: Record baseline (VPN off)
        run: python vpn_checker.py baseline

      - name: Connect VPN
        run: ./scripts/connect_vpn.sh DE     # your own script

      - name: Verify tunnel
        run: python vpn_checker.py check --country DE --out report.json

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: vpn-report
          path: report.json
```

You can also call the tool from `pytest`:

```python
import subprocess

def test_vpn_has_no_leaks():
    result = subprocess.run(
        ["python", "vpn_checker.py", "check", "--country", "DE"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout
```

## Running on mobile

### Android (Termux)

```bash
pkg install python
pip install requests
python vpn_checker.py baseline      # VPN off
python vpn_checker.py check --country DE   # VPN on
```

The IP, DNS leak API, and IPv6 checks work well. The local DNS config check is best effort on Android and may show nothing.

### iOS

Use an app such as a-Shell or Pythonista. Some behavior may differ.

### Manual mobile checks

Open these in the phone's browser with the VPN off and on, then compare:

- https://ipleak.net
- https://browserleaks.com/webrtc
- https://dnsleaktest.com (Extended test)

Also test the kill switch (Android: Settings > VPN > Always-on VPN + Block connections without VPN), Private DNS on and off, airplane mode toggling, and Wi-Fi to cellular switching.

## Privacy and security notes

- **`vpn_baseline.json` contains your real public IP address.** Do not commit it or share it publicly. Add it to `.gitignore`:

  ```gitignore
  vpn_baseline.json
  report.json
  *.log
  ```

- Reports saved with `--out` also contain IP addresses. Redact them before attaching to public issues.
- The tool sends requests to these third-party services:

  | Service | Used for |
  |---|---|
  | `ipinfo.io` (fallback: `ipapi.co`) | Public IP, country, org |
  | `api.ipify.org` / `api6.ipify.org` | IP polling and IPv6 check |
  | `bash.ws` | DNS leak detection |

  These services will see your IP address (that is their purpose). Review their privacy policies if that matters for your environment.

## Limitations

- **WebRTC leaks cannot be tested from Python.** Check them manually at https://browserleaks.com/webrtc.
- The DNS leak test depends on the free `bash.ws` API. If it is unreachable, the tool reports a WARN instead of failing.
- The org/ISP comparison is a loose text match. Always review WARN results manually.
- `check` and `watch` verify behavior from the machine they run on. They do not inspect packet contents; use Wireshark or `tcpdump` to confirm traffic is encrypted.
- The tool does not connect or disconnect your VPN. You control the VPN client.
- Free IP APIs may rate-limit heavy use. Increase `--interval` or `--delay` if you hit limits.
- Geolocation databases can be inaccurate, so a country mismatch may sometimes be a database error rather than a VPN fault. Cross-check with a second source.

## Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| `No baseline found` | Run `python vpn_checker.py baseline` first, from the same directory |
| `Could not get public IP info` | No internet, or the IP APIs are blocked. Check connectivity and firewall rules |
| IP did not change after connecting | VPN is not actually connected, split tunneling excludes your terminal/browser, or the tunnel is not the default route |
| DNS leak reported | VPN client is not forcing its own DNS. Check the DNS leak protection setting and OS-level DNS (such as Android Private DNS) |
| DNS test shows WARN "could not run" | `bash.ws` unreachable. Retry, or test manually at dnsleaktest.com |
| IPv6 shown as WARN | Verify that the address belongs to the VPN. If it is your ISP's, the VPN does not handle IPv6 |
| Baseline taken while VPN was on | Disconnect the VPN and re-run `baseline`. A wrong baseline makes every comparison meaningless |

## Roadmap

- [ ] Automatic VPN connect/disconnect (WireGuard, OpenVPN, custom CLI)
- [ ] Android `adb` mode (VPN state, routes, Private DNS)
- [ ] `iperf3` speed comparison with and without VPN
- [ ] Matrix runner across countries and protocols
- [ ] HTML and CSV report export
- [ ] `pytest` plugin with ready-made fixtures
- [ ] Configurable API providers and offline ISP range lists
- [ ] Packet-capture assertions through `tshark`

## Contributing

Contributions are welcome.

1. Fork the repository
2. Create a branch: `git checkout -b feature/my-change`
3. Make your changes and test them against a real VPN
4. Commit: `git commit -m "Add my change"`
5. Push and open a pull request

When reporting a bug, include your OS, Python version, VPN client and protocol, the command you ran, and the output (with IP addresses redacted).

## License

Released under the MIT License. Create a `LICENSE` file in the repository root:

```text
MIT License

Copyright (c) 2026 <your name>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

**Disclaimer:** This tool is intended for testing VPN software you own or are authorized to test. It reports on observable network behavior and is not a substitute for a full security audit.
