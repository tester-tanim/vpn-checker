# vpn-checker

A lightweight Python command-line tool that helps QA engineers verify that a VPN is doing its job: changing your IP, protecting your DNS, blocking IPv6 leaks, and not exposing your real address when the connection drops.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
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
- [Project structure](#project-structure)
- [Running the tests](#running-the-tests)
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
- **Speed and latency test** compared against your baseline, with a configurable pass/fail threshold
- **Live leak monitor** (`watch`) for kill switch, network switch, and sleep/wake tests, including outage duration tracking
- **Connect-time measurement** (`wait`) to see how long the VPN takes to bring the tunnel up
- **Automated repeat runner** (`loop`) that can connect and disconnect your VPN client for you
- **JSON and HTML reports** you can attach to bug tickets
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

- Python 3.9 or newer
- Internet access
- A VPN client you want to test
- The `requests` library

## Installation

```bash
git clone https://github.com/tester-tanim/vpn-checker.git
cd vpn-checker
pip install -r requirements.txt
```

Or, using a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
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
python vpn_checker.py baseline [--speed]
```

| Option | Description |
|---|---|
| `--speed` | Also record download speed and latency, so `check --speed` can compare against them later |

Writes `vpn_baseline.json` in the current directory.

### `check`

Runs all verification checks while the VPN is **connected**.

```bash
python vpn_checker.py check [--country CODE] [--out FILE] [--html FILE] [--speed] [--max-speed-loss PERCENT]
```

| Option | Description |
|---|---|
| `--country CODE` | Expected 2-letter country code of the VPN server (e.g. `DE`, `US`, `JP`). If omitted, the country check is skipped. |
| `--out FILE` | Save a JSON report to this file. |
| `--html FILE` | Save a readable HTML report (green/red status table) to this file. |
| `--speed` | Also run a speed and latency test and compare it with the baseline. |
| `--max-speed-loss PERCENT` | Maximum allowed speed drop before the speed check FAILS (default `50`). Only used with `--speed`. |

Checks performed:

| # | Check | Pass condition | Fail / warn condition |
|---|---|---|---|
| 1 | Public IP | Differs from baseline IP | Same as baseline (FAIL) |
| 2 | Country | Matches `--country` | Mismatch (FAIL) |
| 3 | Org / ISP | Differs from baseline org | Looks like your real ISP (FAIL) |
| 4 | DNS leak | No resolver belongs to your real ISP | Resolver owned by real ISP (FAIL), resolver in your real country (WARN) |
| 5 | Local DNS config | Original DNS servers not configured | Original servers still present (WARN) |
| 6 | IPv6 | No IPv6 reachable | Same IPv6 as baseline (FAIL), different IPv6 (WARN, verify it belongs to the VPN) |
| 7 | Speed (with `--speed`) | Speed drop within `--max-speed-loss` | Drop exceeds the limit (FAIL), no baseline speed stored (INFO) |

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

`watch` also reports how long each outage lasted (time to recover after a drop) and warns if the connection never came back.

### `wait`

Measures how long the VPN takes to connect. Run it, then click Connect in your VPN app. It polls until your public IP differs from the baseline.

```bash
python vpn_checker.py wait [--timeout SECONDS]
```

| Option | Default | Description |
|---|---|---|
| `--timeout` | `60` | Give up (and FAIL) after this many seconds |

### `speed`

Runs a standalone download speed and latency test. If the baseline was recorded with `--speed`, it also prints the change versus baseline.

```bash
python vpn_checker.py speed [--size MB]
```

| Option | Default | Description |
|---|---|---|
| `--size` | `10` | Download size in MB |

### `loop`

Runs `check` repeatedly, useful for catching intermittent failures.

By default it does **not** touch your VPN, so reconnect between runs yourself. If you pass `--connect-cmd` and `--disconnect-cmd`, it drives the VPN client for you: connect, wait for the IP to change (recording connect time), run all checks, disconnect, repeat.

```bash
python vpn_checker.py loop [--count N] [--delay SECONDS] [--country CODE] [--out FILE]
                           [--speed] [--max-speed-loss PERCENT]
                           [--connect-cmd CMD] [--disconnect-cmd CMD] [--timeout SECONDS]
```

| Option | Default | Description |
|---|---|---|
| `--count` | `5` | Number of runs |
| `--delay` | `10` | Seconds to wait between runs |
| `--country` | none | Expected country code |
| `--out` | none | JSON report path. With `--count` above 1, files are suffixed per run (`report_1.json`, `report_2.json`, ...) |
| `--speed` | off | Include the speed comparison in every run |
| `--max-speed-loss` | `50` | Allowed speed drop in percent |
| `--connect-cmd` | none | Shell command that connects the VPN |
| `--disconnect-cmd` | none | Shell command that disconnects the VPN |
| `--timeout` | `60` | Seconds to wait for the VPN to come up after `--connect-cmd` |

Example with WireGuard, 20 runs, reports and connect-time statistics:

```bash
python vpn_checker.py loop --count 20 --country DE --speed --out report.json \
  --connect-cmd "sudo wg-quick up wg0" \
  --disconnect-cmd "sudo wg-quick down wg0"
```

At the end it prints how many runs failed plus the minimum, average, and maximum connect time.

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

Running speed test...
[INFO] VPN speed: 62.4 Mbps  Latency: 74.0 ms
[PASS] Speed change vs baseline: -38% (100.2 -> 62.4 Mbps, latency 21.0 -> 74.0 ms)

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
[INFO] Recovered after 7.4s outage
[PASS] t=  19.8s  OK   ip=185.220.x.x

Samples: 30 | OK: 22 | DOWN: 8 | LEAK: 0
Outages: 1 | longest: 7.4s | average: 7.4s
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
| Reconnect flakiness | `loop --count 20` with `--connect-cmd` / `--disconnect-cmd` |
| Connect time | `wait`, then click Connect (or let `loop` measure it automatically) |
| Performance impact | `baseline --speed`, then `check --speed --max-speed-loss 40` |
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

- Reports saved with `--out` or `--html` also contain IP addresses. Redact them before attaching to public issues.
- `--connect-cmd` and `--disconnect-cmd` run through your shell exactly as written. Only pass commands you trust, and never build them from untrusted input.
- The tool sends requests to these third-party services:

  | Service | Used for |
  |---|---|
  | `ipinfo.io` (fallback: `ipapi.co`) | Public IP, country, org |
  | `api.ipify.org` / `api6.ipify.org` | IP polling and IPv6 check |
  | `bash.ws` | DNS leak detection |
  | `speed.cloudflare.com`, `www.cloudflare.com` | Speed and latency tests (only with `--speed` / `speed`) |

  These services will see your IP address (that is their purpose). Review their privacy policies if that matters for your environment.

## Limitations

- **WebRTC leaks cannot be tested from Python.** Check them manually at https://browserleaks.com/webrtc.
- The DNS leak test depends on the free `bash.ws` API. If it is unreachable, the tool reports a WARN instead of failing.
- The org/ISP comparison is a loose text match. Always review WARN results manually.
- `check` and `watch` verify behavior from the machine they run on. They do not inspect packet contents; use Wireshark or `tcpdump` to confirm traffic is encrypted.
- The tool does not connect or disconnect your VPN. You control the VPN client.
- The speed test measures single-connection HTTPS download throughput. It is a quick comparison, not a replacement for `iperf3` against your own server, and results vary between runs, so use a generous `--max-speed-loss`.
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

## Project structure

```text
vpn-checker/
├── vpn_checker.py            # the tool (single file)
├── requirements.txt          # runtime dependency (requests)
├── tests/
│   ├── conftest.py
│   └── test_vpn_checker.py   # offline tests with a simulated network
├── .github/workflows/ci.yml  # runs the tests on every push and pull request
├── CHANGELOG.md
├── LICENSE
├── .gitignore
└── README.md
```

## Running the tests

The tests use a simulated network, so they need neither internet access nor a real VPN.

```bash
pip install pytest
pytest -v
```

They cover IP, country, DNS leak, IPv6 leak, and speed checks, report generation, connect-time measurement, the leak monitor, and the automated `loop` runner.

## Roadmap

- [x] Automatic VPN connect/disconnect through `--connect-cmd` / `--disconnect-cmd`
- [x] Speed and latency comparison
- [x] HTML report export
- [x] Connect-time measurement
- [ ] Android `adb` mode (VPN state, routes, Private DNS)
- [ ] `iperf3` integration for server-side throughput tests
- [ ] Matrix runner across countries and protocols
- [ ] CSV export and a combined report for `loop` runs
- [ ] `pytest` plugin with ready-made fixtures
- [ ] Configurable API providers and offline ISP range lists
- [ ] Packet-capture assertions through `tshark`

## Contributing

Contributions are welcome.

1. Fork the repository
2. Create a branch: `git checkout -b feature/my-change`
3. Make your changes, run `pytest`, and test them against a real VPN
4. Commit: `git commit -m "Add my change"`
5. Push and open a pull request

When reporting a bug, include your OS, Python version, VPN client and protocol, the command you ran, and the output (with IP addresses redacted).

## License

Released under the [MIT License](LICENSE).

---

**Disclaimer:** This tool is intended for testing VPN software you own or are authorized to test. It reports on observable network behavior and is not a substitute for a full security audit.
