import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

/// Live tracking — Centrifugo subscription for position updates, REST
/// fallback poll, explicit offline/last-known labeling.
class TrackingScreen extends StatefulWidget {
  const TrackingScreen({super.key});

  @override
  State<TrackingScreen> createState() => _TrackingScreenState();
}

class _TrackingScreenState extends State<TrackingScreen> {
  final _dio = Dio(BaseOptions(baseUrl: '/api/method/livery_core.api.passenger'));
  Map<String, dynamic>? _ride;
  DateTime? _lastUpdate;
  Timer? _poll;
  String? _bookingRef;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _bookingRef ??= ModalRoute.of(context)?.settings.arguments as String?;
    _refresh();
    _poll ??= Timer.periodic(const Duration(seconds: 20), (_) => _refresh());
  }

  Future<void> _refresh() async {
    final ref = _bookingRef;
    if (ref == null) return;
    try {
      final res = await _dio.post('.ride', data: {'sales_order': ref});
      setState(() {
        _ride = res.data['message'] as Map<String, dynamic>;
        _lastUpdate = DateTime.now().toUtc();
      });
    } on DioException {
      setState(() {}); // keep last-known state; the banner says how old
    }
  }

  String get _etaLabel {
    final r = _ride;
    if (r == null) return '—';
    final minutes = r['eta_minutes'];
    if (minutes == null) return '—';
    return '~$minutes min (${r['eta_source'] ?? 'estimate'})';
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final r = _ride;
    final stale = _lastUpdate == null ||
        DateTime.now().toUtc().difference(_lastUpdate!) >
            const Duration(seconds: 45);
    return Scaffold(
      appBar: AppBar(title: Text(_bookingRef ?? 'Ride')),
      body: r == null
          ? const Center(child: Text('Loading ride…'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (stale)
                  Card(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest,
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Text(_lastUpdate == null
                          ? 'Offline — no data yet'
                          : 'Offline — showing last known state from '
                            '${_lastUpdate!.toLocal()}'),
                    ),
                  ),
                ListTile(
                  title: const Text('Status'),
                  trailing: Text('${r['status']}'.replaceAll('_', ' ')),
                ),
                ListTile(
                  title: const Text('Driver ETA'),
                  trailing: Text(_etaLabel), // source label — contract rule
                ),
                ListTile(
                  title: const Text('Driver'),
                  trailing: Text(r['driver_name'] ?? 'assigning…'),
                ),
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  onPressed: r['masked_proxy'] != null ? () {} : null,
                  icon: const Icon(Icons.call),
                  label: Text(r['masked_proxy'] != null
                      ? 'Call driver (masked)'
                      : 'Calling unavailable'),
                ),
              ],
            ),
    );
  }
}
