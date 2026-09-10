# WIS2 Node Assessment console

Terminal tool for **GISC approval** of any WIS2 Node / wis2box. It subscribes to the WIS2Dev Global Broker, checks WCMP2 in the Global Discovery Catalogue, and writes the official assessment report.

The only node-specific value you enter is the WIS2 **centre-id**. Topics are built automatically:

```text
origin/a/wis2/<centre-id>/#
cache/a/wis2/<centre-id>/#
monitor/a/wis2/<centre-id>
```

These match the usual `mosquitto_sub` commands used on Rocky Linux and other systems that do not have MQTT Explorer.

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

Three panes show origin, cache and monitor together. The assessment checklist includes:

- WCMP2 in GDC: `https://gdc.wis2dev.io/collections/wis2-discovery-metadata/items?q="<centre-id>"`
- `origin/a/wis2/<centre-id>/metadata`
- `origin/a/wis2/<centre-id>/data/#`
- `cache/a/wis2/<centre-id>/metadata`
- `cache/a/wis2/<centre-id>/data/core/#`
- monitor ETS reports and errors

Keys: `1` `2` `3` focus panes, `g` refresh GDC, `r` write report, `s` save evidence, `q` quit.

Reports are written to `wis2_assessment_output/<centre-id>_<timestamp>/REPORT.txt`.

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
