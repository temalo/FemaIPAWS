# FEMA IPAWS to Meshtastic

A cron-friendly Python tool that checks FEMA IPAWS alert feeds and relays new
matching alerts to a configured Meshtastic channel.

The structure mirrors the MeshtasticSpaceTools pattern: one script, environment
configuration, serial/TCP Meshtastic support, and a dry-run mode for testing.

## Features

- Polls FEMA IPAWS staging or production feeds.
- Supports `public`, `wea`, `eas`, `nwem`, and `public_non_eas` feed paths.
- Parses CAP 1.2 XML alerts from the IPAWS feed.
- Extracts `<polygon>` and `<circle>` geometry, computes the centroid, and
  reverse-geocodes to a human-readable landmark (e.g. "near Wickenburg, AZ"
  instead of just "Maricopa County").
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

## Log Maintenance

The repo includes a helper script to back up the current log file and truncate it
so the active log stays manageable:

```bash
chmod +x /opt/FemaIPAWS/scripts/backup_and_truncate_log.sh
```

Set these defaults in your `.env` file:

```ini
IPAWS_LOG_FILE=/var/log/ipaws_meshtastic.log
IPAWS_LOG_BACKUP_DIR=/var/backups/ipaws-meshtastic-logs
IPAWS_LOG_RETENTION_DAYS=14
```

The script also recognizes `LOG_FILE`, `BACKUP_DIR`, `IPAWS_BACKUP_DIR`, and
`LOG_RETENTION_DAYS` as fallback names for older deployments.

By default the script rotates this log path:

```text
/var/log/ipaws_meshtastic.log
```

and stores timestamped backups here:

```text
/var/backups/ipaws-meshtastic-logs
```

Backups older than `IPAWS_LOG_RETENTION_DAYS` are removed after each run. Set the
value to `0` if you want to keep backups indefinitely.

You can also override the log path, backup directory, and retention days when
invoking the script:

```bash
/opt/FemaIPAWS/scripts/backup_and_truncate_log.sh /var/log/ipaws_meshtastic.log /var/backups/ipaws-meshtastic-logs 14
```

To run it from cron every night at 00:15, add a second crontab entry:

```cron
15 0 * * * /opt/FemaIPAWS/scripts/backup_and_truncate_log.sh >> /var/log/ipaws_meshtastic_maintenance.log 2>&1
```

Edit the crontab with:

```bash
crontab -e
```

Make sure the cron user can read and truncate the source log, and can write to the
backup directory and maintenance log path. The script automatically looks for a
dotenv file in these locations, in order:

1. `IPAWS_ENV_FILE` if set
2. `/opt/FemaIPAWS/.env`
3. `/opt/FemaIPAWS/scripts/.env`
4. the current working directory's `.env`

That makes it easier to run from cron or from a copied deployment layout while
still keeping the cron entry short.

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

## Location Resolution

CAP alerts often include a `<polygon>` or `<circle>` element describing the
precise affected area. The script extracts that geometry, computes the
centroid, and reverse-geocodes it to a human-readable landmark using the
OpenStreetMap Nominatim API. The result is added to the broadcast message as a
`Location:` line, e.g.:

```text
Location: near Wickenburg, AZ
Area: Maricopa County
```

Configuration:

```ini
IPAWS_GEOCODE_ENABLED=true
IPAWS_GEOCODE_URL=https://nominatim.openstreetmap.org/reverse
IPAWS_GEOCODE_USER_AGENT=ipaws-meshtastic/1.0 (contact: you@example.com)
IPAWS_GEOCODE_ZOOM=14
IPAWS_GEOCODE_TIMEOUT_SECONDS=10
IPAWS_GEOCODE_CACHE_SIZE=500
```

Set a contact address in `IPAWS_GEOCODE_USER_AGENT` — Nominatim's usage policy
requires identifiable User-Agents. Reverse lookups are cached in the state
file by rounded coordinates, so repeat polygons over the same area incur no
extra requests.

If the centroid falls in an unpopulated area, the label falls back to the
containing county (e.g. `in Yavapai County, AZ`). If geocoding is disabled or
fails, the label falls back to raw coordinates.

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
