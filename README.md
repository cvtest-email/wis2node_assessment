# WIS2 Node Assessment console

Terminal tool for **GISC approval** of any WIS2 Node / wis2box. It observes the WIS2Dev Global Broker and GDC, runs WTH / WNM / MQTT / HTTP tests on what a GISC can see, and writes an assessment report with a pass/fail matrix and evidence.

The only node-specific value you enter is the WIS2 **centre-id**. Topics are built automatically:

```text
origin/a/wis2/<centre-id>/#
cache/a/wis2/<centre-id>/#
monitor/a/wis2/<centre-id>
```

These match the usual `mosquitto_sub` commands used on systems that do not have MQTT Explorer.

## Requirements

- Python 3.9+
- Network access to `gb.wis2dev.io:8883` and `gdc.wis2dev.io`

## Install

```bash
git clone https://github.com/cvtest-email/wis2node_assessment.git
cd wis2node_assessment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 wis2_assess.py
```

The wizard asks for:

1. **centre-id** of the node under assessment (required)
2. Optional topic edits (defaults from centre-id)
3. Broker flags, same as `mosquitto_sub`: `-h`, `-p`, `-u`, `-P`, `-v`, TLS
4. Name of the **GISC** writing the report

WIS2Dev defaults are `mqtts://gb.wis2dev.io:8883` with username/password `everyone` / `everyone`.

Non-interactive example:

```bash
python3 wis2_assess.py --centre au-bom --gisc "GISC Melbourne"
```

`--center` and `--center-id` are accepted as aliases.

## Live console

Three panes show origin, cache and monitor together. The assessment engine scores named tests:

- **WTH** — topic structure, metadata/data/cache/monitor topics, centre-id consistency
- **WNM** — JSON, required fields, UUID, pubtime, geometry, links, content, integrity, cache flag, topic/message relationship
- **MQTT** — broker reachability, TLS, authentication, subscriptions (the broker this session connected to)
- **GDC** — WCMP2 records present; GDC ETS on monitor (this tool does not replace wis2-gdc)
- **HTTP** — DNS, TLS, status, headers, download, redirects, canonical URL, hash, latency on sampled origin canonical links (when a report is written)

Overall verdict: `PASS`, `PASS WITH WARNINGS`, `FAIL`, or `INCOMPLETE`.

Keys: `1` `2` `3` focus panes, `g` refresh GDC, `r` write report, `s` save evidence, `q` quit.

Reports are written to `wis2_assessment_output/<centre-id>_<timestamp>/`. That directory includes `REPORT.txt`, `tests.json`, and captured MQTT/GDC evidence.

Use `--skip-http` to write a report without probing data-server URLs.

## Headless collection

```bash
python3 wis2_assess.py --centre <centre-id> --headless --duration 120
```

## Replay a previous capture

```bash
python3 wis2_assess.py --from-jsonl path/to/messages.jsonl --centre <centre-id>
```

## Assessment notes

- The WIS2 Node should publish metadata first, then data.
- About 30 minutes may be needed after metadata publication before the Test Global Broker and GDC can validate data notifications.
- Invalid WNM, missing metadata, or invalid WCMP2 appears on `monitor/a/wis2/<centre-id>`.
