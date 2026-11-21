import 'dart:async';

import 'package:geolocator/geolocator.dart';

/// Position reporter — one fix every 15 s while a ride is active.
///
/// Fixes carry the DEVICE timestamp (UTC) — the server rejects naive,
/// future (>5 min), and out-of-order fixes, so this reporter never adjusts
/// or "smooths" timestamps: it reports what the GPS chip said, when it
/// said it. Offline fixes are dropped after a short ring buffer rather
/// than replayed late (a 20-minute-old position is worse than none — the
/// server marks the driver stale honestly).
class PositionReporter {
  PositionReporter({required this.onFix, required this.onStateChange});

  final void Function(Map<String, dynamic> fix) onFix;
  final VoidCallback onStateChange;

  StreamSubscription<Position>? _sub;
  final List<Map<String, dynamic>> _ring = [];
  static const _ringMax = 20; // ~5 minutes at 15 s cadence

  String get stateLabel {
    if (_sub == null) return 'position off';
    if (_ring.isEmpty) return 'position live';
    return 'queued offline — ${_ring.length} pending';
  }

  Future<void> start() async {
    final permission = await Geolocator.requestPermission();
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      onStateChange();
      return; // UI shows "position off" — no fake fixes, ever
    }
    _sub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        distanceFilter: 10,
      ),
    ).listen((pos) {
      final fix = {
        'lat': pos.latitude,
        'lng': pos.longitude,
        'speed_kmh': pos.speed * 3.6,
        'heading': pos.heading,
        'recorded_at': pos.timestamp.toUtc().toIso8601String(),
      };
      if (_ring.isNotEmpty) {
        _ring.add(fix);
        if (_ring.length > _ringMax) _ring.removeAt(0);
      } else {
        onFix(fix);
      }
      onStateChange();
    });
  }

  /// Called on reconnect: flush the ring in order (server drops
  /// out-of-order anyway, but order keeps its work minimal).
  void flush() {
    for (final fix in List.of(_ring)) {
      onFix(fix);
    }
    _ring.clear();
    onStateChange();
  }

  Future<void> stop() async {
    await _sub?.cancel();
    _sub = null;
    onStateChange();
  }
}
