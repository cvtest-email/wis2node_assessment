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
- Network access to `gb.wis2dev.io:8883` (WIS2Dev) and, when you switch, production brokers such as `globalbroker.meteo.fr:8883`

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

WIS2Dev defaults are `mqtts://gb.wis2dev.io:8883` with username/password `everyone` / `everyone`. Switch to production WIS with the **Production WIS** button (or `p`). That **keeps origin, cache and monitor** and splits each pane:

- **Top:** `globalbroker.meteo.fr`
- **Bottom:** `globalbroker.inmet.gov.br`

Both production brokers are subscribed at the same time (same `everyone` / `everyone` MQTTS topics as `mosquitto_sub`). **WIS2Dev** (or `d`) switches back. There is no automatic hop and no France-then-Brazil sequence.

```bash
mosquitto_sub -h globalbroker.meteo.fr -p 8883 -u everyone -P everyone -t 'origin/a/wis2/<centre-id>/#' -v
mosquitto_sub -h globalbroker.inmet.gov.br -p 8883 -u everyone -P everyone -t 'origin/a/wis2/<centre-id>/#' -v
mosquitto_sub -h globalbroker.meteo.fr -p 8883 -u everyone -P everyone -t 'cache/a/wis2/<centre-id>/#' -v
```

The tool does **not** point the three panes at the node's own broker (for example `wis2.pngmet.gov.pg`). That host has origin notifications only, not Global Cache or monitor. To watch the node directly:

```bash
python3 wis2_assess.py --centre <centre-id> --host wis2.pngmet.gov.pg
```

Start on both production brokers immediately with `--production`. In the live console, use the **WIS2Dev** / **Production WIS** buttons (or `d` / `p`) to switch.

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
- **GDC** — WCMP2 records on the WIS2Dev catalogue `gdc.wis2dev.io`; GDC ETS on monitor (this tool does not replace wis2-gdc)
- **HTTP** — DNS, TLS, status, headers, download, redirects, canonical URL, hash, latency on sampled origin canonical links (when a report is written)

Overall verdict: `PASS`, `PASS WITH WARNINGS`, `FAIL`, or `INCOMPLETE`.

Keys: `1` `2` `3` focus panes, `g` refresh the WIS2Dev GDC, `c` check the operational Canada, China and Germany caches (not WIS2Dev), **WIS2Dev** / **Production WIS** buttons (or `d` / `p`) switch brokers, `r` write report, `s` save evidence, `q` quit.

Message detail shows a human-readable notification (station, times, location, download URL). Inline BUFR/base64 is summarised, not dumped.

The live assessment uses `gb.wis2dev.io` and `gdc.wis2dev.io` only. Canada (`wis2-gdc.weather.gc.ca`), China (`gdc.wis.cma.cn/api`) and Germany (`wis2.dwd.de/gdc`) are shown in a separate pane and do not count toward the WIS2Dev verdict. One-shot check:

```bash
python3 wis2_assess.py --centre <centre-id> --gdc-caches
```

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
