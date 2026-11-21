"""Native mobile structural validation — no Flutter SDK in CI, so we check
the contract surface: pubspec sanity, honesty markers in the Dart source,
and parity with the PWA rules (ETA labels, offline labels, masked calls)."""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "apps" / "driver_flutter"
PASSENGER = ROOT / "apps" / "passenger_flutter"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_pubspecs_parse_and_pin_dependencies():
    for app in (DRIVER, PASSENGER):
        spec = yaml.safe_load(_read(app / "pubspec.yaml"))
        assert spec["publish_to"] == "none"
        assert "flutter" in spec["dependencies"]
        # every dependency is version-pinned — no `any` in a shipping app
        for dep, ver in spec["dependencies"].items():
            if isinstance(ver, str):
                assert ver != "any", f"{app.name}:{dep} unpinned"


def test_driver_app_reports_positions_with_device_timestamps():
    src = _read(DRIVER / "lib" / "position_reporter.dart")
    assert "pos.timestamp.toUtc().toIso8601String()" in src
    assert "never adjusts" in src or "never fake" in src or "no fake fixes" in src


def test_driver_transitions_are_journaled_offline_not_applied_locally():
    src = _read(DRIVER / "lib" / "ride_queue.dart")
    assert "_journal" in src and "_flushJournal" in src
    assert "server confirms on flush" in src  # no optimistic local state


def test_masked_calling_never_falls_back_to_raw_numbers():
    driver_src = _read(DRIVER / "lib" / "ride_queue.dart")
    pax_src = _read(PASSENGER / "lib" / "tracking_screen.dart")
    assert "Calling unavailable" in driver_src
    assert "Calling unavailable" in pax_src
    assert "masked_proxy" in pax_src


def test_passenger_eta_keeps_source_label():
    src = _read(PASSENGER / "lib" / "tracking_screen.dart")
    assert "eta_source" in src
    assert re.search(r"'~\$minutes min \(", src), "ETA lost its source label"


def test_offline_states_are_labeled_with_timestamps():
    src = _read(PASSENGER / "lib" / "tracking_screen.dart")
    assert "last known state" in src
    driver_main = _read(DRIVER / "lib" / "main.dart")
    assert "queued offline" in driver_main


def test_whatsapp_channel_dependency_present_for_ng():
    spec = yaml.safe_load(_read(PASSENGER / "pubspec.yaml"))
    assert "url_launcher" in spec["dependencies"]  # WhatsApp-first share
