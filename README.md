# FEMA IPAWS to Meshtastic

A cron-friendly Python tool that checks FEMA IPAWS alert feeds and relays new
matching alerts to a configured Meshtastic channel.

The structure mirrors the MeshtasticSpaceTools pattern: one script, environment
configuration, serial/TCP Meshtastic support, and a dry-run mode for testing.

## Features

- Polls FEMA IPAWS staging or production feeds.
- Supports `public`, `wea`, `eas`, `nwem`, and `public_non_eas` feed paths.
- Parses CAP 1.2 XML alerts from the IPAWS feed.
- Sends concise Meshtastic text messages over serial or TCP.
- Tracks sent alert IDs in a local JSON state file to avoid duplicate broadcasts.
- Optional filters for SAME codes, UGC codes, event codes, and severity.
- Designed for Linux cron usage.

## Requirements

- Python 3.10+
- Internet access from the polling host
- Meshtastic device reachable by USB serial or TCP/IP

Install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Create a local config file:

```bash
cp .env.example .env
```

Important settings:

```ini
IPAWS_ENVIRONMENT=staging
IPAWS_FEED=public
IPAWS_LOOKBACK_MINUTES=10
IPAWS_STATE_FILE=/var/lib/ipaws-meshtastic/ipaws_state.json
IPAWS_VERIFY_TLS=true

MESHTASTIC_CONNECTION_TYPE=serial
MESHTASTIC_SERIAL_PORT=/dev/ttyUSB0
MESHTASTIC_CHANNEL=0
MESHTASTIC_SEND_ENABLED=false
```

Use `MESHTASTIC_SEND_ENABLED=false` first. The script will print messages
without transmitting or marking alerts as sent.

If your Python runtime cannot validate the FEMA TLS certificate chain, set
`IPAWS_CA_BUNDLE` to a CA bundle path. For temporary lab testing only, you can
set `IPAWS_VERIFY_TLS=false`.

## Running

Dry-run from the project directory:

```bash
. .venv/bin/activate
python ipaws_meshtastic.py
```

When the output looks right, set:

```ini
MESHTASTIC_SEND_ENABLED=true
```

## Cron

FEMA recommends polling no more frequently than every 2 minutes. A typical cron
entry:

```cron
*/2 * * * * cd /opt/FemaIPAWS && /opt/FemaIPAWS/.venv/bin/python ipaws_meshtastic.py >> /var/log/ipaws_meshtastic.log 2>&1
```

Use an absolute `IPAWS_STATE_FILE` path for cron, and make sure the cron user can
read/write that path and access the Meshtastic serial device.

## Filtering

Leave filters blank to relay all active alerts from the chosen feed.

Examples:

```ini
# Maricopa County, AZ SAME code
IPAWS_FILTER_SAME_CODES=004013

# All Arizona alerts with SAME or UGC geocodes
IPAWS_FILTER_STATES=AZ

# Only extreme and severe alerts
IPAWS_FILTER_SEVERITIES=EXTREME,SEVERE

# Only specific CAP event codes, such as Tornado Warning SAME event code
IPAWS_FILTER_EVENT_CODES=TOR
```

Multiple values are comma-separated. Filters are combined, so if more than one
filter type is set an alert must match all configured filter groups.

## IPAWS Notes

The FEMA IPAWS All-Hazards Information Feed provides public alerts in CAP format
for redistribution. The staging feed is useful for development and testing:

```text
https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/<timestamp>
```

Production uses:

```text
https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/<timestamp>
```

The script constructs the timestamp automatically from `IPAWS_LOOKBACK_MINUTES`.
