#!/usr/bin/env python3
"""
IPAWS alerts to Meshtastic.

Fetches recent FEMA IPAWS CAP alerts and sends new matching alerts to a
configured Meshtastic channel. Designed to be run periodically by cron.
"""

import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests
import urllib3
from dotenv import load_dotenv
from requests import RequestException
from urllib3.exceptions import InsecureRequestWarning


CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
FEED_PATHS = {
    "eas": "eas",
    "nwem": "nwem",
    "wea": "PublicWEA",
    "public": "public",
    "public_non_eas": "public_non_eas",
}
ENVIRONMENTS = {
    "staging": "https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest",
    "production": "https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest",
}
USPS_STATE_FIPS = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}


@dataclass
class Config:
    ipaws_environment: str = "staging"
    ipaws_feed: str = "public"
    lookback_minutes: int = 10
    request_timeout_seconds: int = 20
    verify_tls: bool = True
    ca_bundle: str = ""
    state_file: Path = Path("ipaws_state.json")
    max_alerts_per_run: int = 3
    sent_cache_size: int = 200
    filter_same_codes: set[str] = field(default_factory=set)
    filter_ugc_codes: set[str] = field(default_factory=set)
    filter_event_codes: set[str] = field(default_factory=set)
    filter_severities: set[str] = field(default_factory=set)
    filter_states: set[str] = field(default_factory=set)
    language: str = "en"
    message_prefix: str = "IPAWS"
    max_message_length: int = 200
    send_delay_seconds: float = 2.0
    connection_type: str = "serial"
    serial_port: str = "/dev/ttyUSB0"
    tcp_host: str = "192.168.1.100"
    channel: int = 0
    send_enabled: bool = True


@dataclass
class Alert:
    identifier: str
    sender: str
    sent: str
    status: str
    msg_type: str
    event: str
    headline: str
    description: str
    instruction: str
    area: str
    urgency: str
    severity: str
    certainty: str
    expires: str
    same_codes: set[str]
    ugc_codes: set[str]
    event_codes: set[str]
    parameters: dict[str, list[str]]


def load_config() -> Config:
    load_dotenv()
    return Config(
        ipaws_environment=os.getenv("IPAWS_ENVIRONMENT", "staging").lower(),
        ipaws_feed=os.getenv("IPAWS_FEED", "public").lower(),
        lookback_minutes=int(os.getenv("IPAWS_LOOKBACK_MINUTES", "10")),
        request_timeout_seconds=int(os.getenv("IPAWS_REQUEST_TIMEOUT_SECONDS", "20")),
        verify_tls=os.getenv("IPAWS_VERIFY_TLS", "true").lower() == "true",
        ca_bundle=os.getenv("IPAWS_CA_BUNDLE", ""),
        state_file=Path(os.getenv("IPAWS_STATE_FILE", "ipaws_state.json")),
        max_alerts_per_run=int(os.getenv("IPAWS_MAX_ALERTS_PER_RUN", "3")),
        sent_cache_size=int(os.getenv("IPAWS_SENT_CACHE_SIZE", "200")),
        filter_same_codes=parse_csv_set(os.getenv("IPAWS_FILTER_SAME_CODES", "")),
        filter_ugc_codes=parse_csv_set(os.getenv("IPAWS_FILTER_UGC_CODES", "")),
        filter_event_codes=parse_csv_set(os.getenv("IPAWS_FILTER_EVENT_CODES", "")),
        filter_severities=parse_csv_set(os.getenv("IPAWS_FILTER_SEVERITIES", "")),
        filter_states=parse_state_filter(os.getenv("IPAWS_FILTER_STATES", "")),
        language=os.getenv("IPAWS_LANGUAGE", "en").lower(),
        message_prefix=os.getenv("IPAWS_MESSAGE_PREFIX", "IPAWS"),
        max_message_length=int(os.getenv("MESHTASTIC_MAX_MESSAGE_LENGTH", "200")),
        send_delay_seconds=float(os.getenv("MESHTASTIC_SEND_DELAY_SECONDS", "2")),
        connection_type=os.getenv("MESHTASTIC_CONNECTION_TYPE", "serial").lower(),
        serial_port=os.getenv("MESHTASTIC_SERIAL_PORT", "/dev/ttyUSB0"),
        tcp_host=os.getenv("MESHTASTIC_TCP_HOST", "192.168.1.100"),
        channel=int(os.getenv("MESHTASTIC_CHANNEL", "0")),
        send_enabled=os.getenv("MESHTASTIC_SEND_ENABLED", "true").lower() == "true",
    )


def parse_csv_set(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def parse_state_filter(value: str) -> set[str]:
    states = parse_csv_set(value)
    unknown_states = states - set(USPS_STATE_FIPS)
    if unknown_states:
        raise ValueError(
            "Unsupported IPAWS_FILTER_STATES value(s): "
            + ", ".join(sorted(unknown_states))
            + ". Use two-letter USPS state abbreviations, such as AZ."
        )
    return states


def feed_url(config: Config) -> str:
    if config.ipaws_environment not in ENVIRONMENTS:
        raise ValueError(f"Unsupported IPAWS_ENVIRONMENT: {config.ipaws_environment}")
    if config.ipaws_feed not in FEED_PATHS:
        raise ValueError(f"Unsupported IPAWS_FEED: {config.ipaws_feed}")

    since = datetime.now(timezone.utc) - timedelta(minutes=config.lookback_minutes)
    since_text = since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return f"{ENVIRONMENTS[config.ipaws_environment]}/{FEED_PATHS[config.ipaws_feed]}/recent/{since_text}"


def fetch_alert_xml(config: Config) -> bytes:
    url = feed_url(config)
    logging.info("Fetching IPAWS %s/%s feed: %s", config.ipaws_environment, config.ipaws_feed, url)
    verify = config.ca_bundle if config.ca_bundle else config.verify_tls
    if verify is False:
        urllib3.disable_warnings(category=InsecureRequestWarning)
        logging.warning("IPAWS TLS certificate verification is disabled.")
    response = requests.get(url, timeout=config.request_timeout_seconds, verify=verify)
    response.raise_for_status()
    return response.content


def parse_alerts(xml_content: bytes, language: str) -> list[Alert]:
    root = ET.fromstring(xml_content)
    alerts = []
    for alert_node in root.findall(".//cap:alert", CAP_NS):
        info_node = choose_info_node(alert_node.findall("cap:info", CAP_NS), language)
        if info_node is None:
            continue
        alerts.append(alert_from_node(alert_node, info_node))
    return alerts


def choose_info_node(info_nodes: Iterable[ET.Element], language: str) -> ET.Element | None:
    nodes = list(info_nodes)
    if not nodes:
        return None
    for node in nodes:
        node_language = text(node, "cap:language").lower()
        if node_language.startswith(language):
            return node
    return nodes[0]


def alert_from_node(alert_node: ET.Element, info_node: ET.Element) -> Alert:
    same_codes: set[str] = set()
    ugc_codes: set[str] = set()
    event_codes: set[str] = set()
    parameters: dict[str, list[str]] = {}

    for event_code in info_node.findall("cap:eventCode", CAP_NS):
        name = text(event_code, "cap:valueName")
        value = text(event_code, "cap:value")
        if value:
            event_codes.add(value.upper())
        if name.upper() == "SAME" and value:
            same_codes.add(value.upper())

    for parameter in info_node.findall("cap:parameter", CAP_NS):
        name = text(parameter, "cap:valueName")
        value = text(parameter, "cap:value")
        if name and value:
            parameters.setdefault(name, []).append(value)

    area_names = []
    for area in info_node.findall("cap:area", CAP_NS):
        area_desc = text(area, "cap:areaDesc")
        if area_desc:
            area_names.append(area_desc)
        for geocode in area.findall("cap:geocode", CAP_NS):
            name = text(geocode, "cap:valueName")
            value = text(geocode, "cap:value")
            if name.upper() == "SAME" and value:
                same_codes.add(value.upper())
            if name.upper() == "UGC" and value:
                ugc_codes.add(value.upper())

    return Alert(
        identifier=text(alert_node, "cap:identifier"),
        sender=text(alert_node, "cap:sender"),
        sent=text(alert_node, "cap:sent"),
        status=text(alert_node, "cap:status"),
        msg_type=text(alert_node, "cap:msgType"),
        event=text(info_node, "cap:event"),
        headline=text(info_node, "cap:headline"),
        description=text(info_node, "cap:description"),
        instruction=text(info_node, "cap:instruction"),
        area="; ".join(area_names),
        urgency=text(info_node, "cap:urgency"),
        severity=text(info_node, "cap:severity"),
        certainty=text(info_node, "cap:certainty"),
        expires=text(info_node, "cap:expires"),
        same_codes=same_codes,
        ugc_codes=ugc_codes,
        event_codes=event_codes,
        parameters=parameters,
    )


def text(node: ET.Element, path: str) -> str:
    child = node.find(path, CAP_NS)
    if child is None or child.text is None:
        return ""
    return " ".join(child.text.split())


def is_active_alert(alert: Alert) -> bool:
    if alert.status.lower() != "actual":
        return False
    if alert.msg_type.lower() not in {"alert", "update"}:
        return False
    expires = parse_datetime(alert.expires)
    return expires is None or expires > datetime.now(timezone.utc)


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        logging.warning("Could not parse timestamp: %s", value)
        return None


def matches_filters(alert: Alert, config: Config) -> bool:
    if config.filter_states and not matches_state_filter(alert, config.filter_states):
        return False
    if config.filter_same_codes and not alert.same_codes.intersection(config.filter_same_codes):
        return False
    if config.filter_ugc_codes and not alert.ugc_codes.intersection(config.filter_ugc_codes):
        return False
    if config.filter_event_codes and not alert.event_codes.intersection(config.filter_event_codes):
        return False
    if config.filter_severities and alert.severity.upper() not in config.filter_severities:
        return False
    return True


def matches_state_filter(alert: Alert, states: set[str]) -> bool:
    state_fips_codes = {USPS_STATE_FIPS[state] for state in states}
    if any(same_code_state(code) in state_fips_codes for code in alert.same_codes):
        return True
    if any(ugc_code_state(code) in states for code in alert.ugc_codes):
        return True
    return False


def same_code_state(code: str) -> str:
    normalized = code.strip()
    if len(normalized) == 6 and normalized.isdigit():
        return normalized[1:3]
    return ""


def ugc_code_state(code: str) -> str:
    normalized = code.strip().upper()
    if len(normalized) >= 2 and normalized[:2].isalpha():
        return normalized[:2]
    return ""


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"sent_alert_ids": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not read state file %s: %s", path, exc)
        return {"sent_alert_ids": []}


def save_state(path: Path, state: dict, cache_size: int) -> None:
    sent_ids = list(dict.fromkeys(state.get("sent_alert_ids", [])))
    state["sent_alert_ids"] = sent_ids[-cache_size:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def new_alerts(alerts: list[Alert], config: Config, state: dict) -> list[Alert]:
    sent_ids = set(state.get("sent_alert_ids", []))
    candidates = [
        alert for alert in alerts
        if alert.identifier
        and alert.identifier not in sent_ids
        and is_active_alert(alert)
        and matches_filters(alert, config)
    ]
    candidates.sort(key=lambda item: parse_datetime(item.sent) or datetime.min.replace(tzinfo=timezone.utc))
    return candidates[:config.max_alerts_per_run]


def format_alert_message(alert: Alert, config: Config) -> str:
    summary = first_parameter(alert, "CMAMtext") or first_parameter(alert, "CMAMlongtext")
    summary = summary or alert.headline or alert.description or alert.event

    parts = [
        config.message_prefix,
        alert.event,
        alert.severity,
        alert.area,
        summary,
    ]
    message = " | ".join(part for part in parts if part)
    message = ascii_clean(message)
    return truncate(message, config.max_message_length)


def first_parameter(alert: Alert, name: str) -> str:
    values = alert.parameters.get(name, [])
    return values[0] if values else ""


def ascii_clean(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"\s+", " ", value)
    return value.encode("ascii", errors="ignore").decode("ascii").strip()


def truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def send_messages(messages: list[str], config: Config) -> bool:
    if not messages:
        logging.info("No new matching IPAWS alerts to send.")
        return True

    if not config.send_enabled:
        print("\nTEST MODE - Messages NOT sent to Meshtastic")
        print(f"Channel: {config.channel}")
        for index, message in enumerate(messages, start=1):
            print(f"\nMessage {index} ({len(message)} chars):")
            print(message)
        return True

    interface = None
    try:
        import meshtastic.serial_interface
        import meshtastic.tcp_interface

        if config.connection_type == "tcp":
            logging.info("Connecting to Meshtastic via TCP: %s", config.tcp_host)
            interface = meshtastic.tcp_interface.TCPInterface(hostname=config.tcp_host)
        else:
            logging.info("Connecting to Meshtastic via serial: %s", config.serial_port)
            interface = meshtastic.serial_interface.SerialInterface(devPath=config.serial_port)

        for index, message in enumerate(messages, start=1):
            logging.info("Sending alert %s/%s to channel %s", index, len(messages), config.channel)
            interface.sendText(message, channelIndex=config.channel)
            if index < len(messages):
                time.sleep(config.send_delay_seconds)
        return True
    except Exception as exc:
        logging.error("Error sending to Meshtastic: %s", exc)
        return False
    finally:
        if interface:
            interface.close()


def main() -> int:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    config = load_config()
    logging.info("IPAWS alerts -> Meshtastic")
    logging.info("Feed: %s/%s", config.ipaws_environment, config.ipaws_feed)
    logging.info("Send to Meshtastic: %s", "enabled" if config.send_enabled else "disabled")

    state = load_state(config.state_file)
    try:
        alerts = parse_alerts(fetch_alert_xml(config), config.language)
    except ValueError as exc:
        logging.error("Configuration error: %s", exc)
        return 1
    except RequestException as exc:
        logging.error("Could not fetch IPAWS feed: %s", exc)
        return 1
    except ET.ParseError as exc:
        logging.error("Could not parse IPAWS XML response: %s", exc)
        return 1
    alerts_to_send = new_alerts(alerts, config, state)
    messages = [format_alert_message(alert, config) for alert in alerts_to_send]

    success = send_messages(messages, config)
    if success and config.send_enabled:
        sent_ids = state.setdefault("sent_alert_ids", [])
        sent_ids.extend(alert.identifier for alert in alerts_to_send)
        state["last_success_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        save_state(config.state_file, state, config.sent_cache_size)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
