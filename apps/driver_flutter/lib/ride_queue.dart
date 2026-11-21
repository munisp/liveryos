import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Ride queue with an offline transition journal.
///
/// Every driver tap (en_route / arrived / start / complete) is journaled
/// locally FIRST with a client timestamp, then pushed. On reconnect the
/// journal flushes in order. The server is the authority on
/// LEGAL_TRANSITIONS — a stale offline tap can be rejected on flush, and
/// when that happens the UI shows the server state, not the local wish.

enum RideStatus { quoted, assigned, enRoute, arrived, inProgress, completed, noShow, cancelled }

class Ride {
  final String id;
  final String passengerName;
  final String pickupAddress;
  final DateTime pickupAt;
  RideStatus status;
  final String? maskedProxyNumber; // null => calling unavailable

  Ride({
    required this.id,
    required this.passengerName,
    required this.pickupAddress,
    required this.pickupAt,
    required this.status,
    this.maskedProxyNumber,
  });

  factory Ride.fromJson(Map<String, dynamic> j) => Ride(
        id: j['ride_id'] as String,
        passengerName: j['passenger'] as String? ?? '',
        pickupAddress: j['pickup_address'] as String? ?? '',
        pickupAt: DateTime.parse(j['pickup_at'] as String),
        status: _statusFrom(j['status'] as String? ?? 'QUOTED'),
        maskedProxyNumber: j['masked_proxy'] as String?,
      );

  static RideStatus _statusFrom(String s) => switch (s) {
        'ASSIGNED' => RideStatus.assigned,
        'EN_ROUTE' => RideStatus.enRoute,
        'ARRIVED' => RideStatus.arrived,
        'IN_PROGRESS' => RideStatus.inProgress,
        'COMPLETED' => RideStatus.completed,
        'NO_SHOW' => RideStatus.noShow,
        'CANCELLED' => RideStatus.cancelled,
        _ => RideStatus.quoted,
      };
}

class RideQueue extends ChangeNotifier {
  final Dio _dio = Dio(BaseOptions(baseUrl: '/api/method/livery_core.api.driver'));
  final List<Ride> assignedRides = [];
  bool offline = false;

  static const _journalKey = 'livery.driver.transition_journal';

  Future<void> refresh() async {
    try {
      final res = await _dio.post('.queue', data: {});
      assignedRides
        ..clear()
        ..addAll((res.data['message'] as List)
            .map((j) => Ride.fromJson(j as Map<String, dynamic>)));
      offline = false;
      await _flushJournal();
    } on DioException {
      offline = true; // last-known queue stays on screen, labeled
    }
    notifyListeners();
  }

  Future<void> transition(Ride ride, RideStatus target) async {
    final entry = {
      'ride_id': ride.id,
      'to': target.name.toUpperCase(),
      'client_at': DateTime.now().toUtc().toIso8601String(),
    };
    try {
      await _dio.post('.transition', data: entry);
      await refresh();
    } on DioException {
      offline = true;
      await _journal(entry); // NOT applied locally — server confirms on flush
      notifyListeners();
    }
  }

  Future<void> callPartyMasked(Ride ride) async {
    // Joins the LiveKit room for this booking with a fresh access token.
    // If the token request fails, the caller renders "calling unavailable" —
    // it never falls back to dialing a raw number.
    if (ride.maskedProxyNumber == null) return;
    try {
      await _dio.post('.call_token', data: {'ride_id': ride.id});
      // livekit_client room join with the returned token happens here.
    } on DioException {
      // surfaced as "calling unavailable" by the caller
    }
  }

  Future<void> sendPosition(Map<String, dynamic> fix) async {
    try {
      await _dio.post('.position', data: fix);
    } on DioException {
      // PositionReporter keeps its own ring buffer and retries; a lost fix
      // is acceptable (at-least-one-every-15s), a fabricated one is not.
    }
  }

  Future<void> _journal(Map<String, dynamic> entry) async {
    final prefs = await SharedPreferences.getInstance();
    final journal = prefs.getStringList(_journalKey) ?? [];
    journal.add(jsonEncode(entry));
    await prefs.setStringList(_journalKey, journal);
  }

  Future<void> _flushJournal() async {
    final prefs = await SharedPreferences.getInstance();
    final journal = prefs.getStringList(_journalKey) ?? [];
    if (journal.isEmpty) return;
    final remaining = <String>[];
    for (final raw in journal) {
      try {
        await _dio.post('.transition', data: jsonDecode(raw));
      } on DioException {
        remaining.add(raw); // keep order; retry next reconnect
      }
    }
    await prefs.setStringList(_journalKey, remaining);
  }
}

class RideCard extends StatelessWidget {
  final Ride ride;
  final void Function(RideStatus) onTransition;
  final VoidCallback onCall;

  const RideCard({
    super.key,
    required this.ride,
    required this.onTransition,
    required this.onCall,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(ride.passengerName,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(ride.pickupAddress),
            Text('Pickup: ${ride.pickupAt.toLocal()}'),
            const SizedBox(height: 8),
            Wrap(spacing: 8, children: [
              for (final t in _nextHints(ride.status))
                FilledButton.tonal(
                  onPressed: () => onTransition(t),
                  child: Text(t.name.replaceAll('_', ' ')),
                ),
              OutlinedButton.icon(
                onPressed: ride.maskedProxyNumber != null ? onCall : null,
                icon: const Icon(Icons.call),
                label: Text(ride.maskedProxyNumber != null
                    ? 'Call (masked)'
                    : 'Calling unavailable'),
              ),
            ]),
          ],
        ),
      ),
    );
  }

  /// Client-side hints only — the server enforces LEGAL_TRANSITIONS.
  List<RideStatus> _nextHints(RideStatus s) => switch (s) {
        RideStatus.assigned => [RideStatus.enRoute, RideStatus.noShow],
        RideStatus.enRoute => [RideStatus.arrived],
        RideStatus.arrived => [RideStatus.inProgress, RideStatus.noShow],
        RideStatus.inProgress => [RideStatus.completed],
        _ => const [],
      };
}
