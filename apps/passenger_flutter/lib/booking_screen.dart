import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

/// Booking screen — quote first (with engine provenance), then confirm.
///
/// The quote card shows which engine produced the price (engine_version)
/// and how long it is valid. Prices are integer minor units server-side;
/// display formatting happens here and only here.
class BookingScreen extends StatefulWidget {
  const BookingScreen({super.key});

  @override
  State<BookingScreen> createState() => _BookingScreenState();
}

class _BookingScreenState extends State<BookingScreen> {
  final _dio = Dio(BaseOptions(baseUrl: '/api/method/livery_core.api.passenger'));
  final _pickup = TextEditingController();
  final _dropoff = TextEditingController();
  DateTime _when = DateTime.now().add(const Duration(hours: 2));
  String _serviceClass = 'business';
  Map<String, dynamic>? _quote;
  String? _error;
  bool _busy = false;

  Future<void> _getQuote() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final res = await _dio.post('.quote', data: {
        'pickup': _pickup.text,
        'dropoff': _dropoff.text,
        'pickup_at': _when.toUtc().toIso8601String(),
        'service_class': _serviceClass,
      });
      setState(() => _quote = res.data['message'] as Map<String, dynamic>);
    } on DioException catch (e) {
      setState(() => _error = e.response?.statusCode == 429
          ? 'Busy — please retry in a moment'
          : 'Could not quote right now — check connection');
    } finally {
      setState(() => _busy = false);
    }
  }

  Future<void> _confirm() async {
    final q = _quote;
    if (q == null) return;
    setState(() => _busy = true);
    try {
      final res = await _dio.post('.book', data: {'quote_id': q['quote_id']});
      final booking = res.data['message'] as Map<String, dynamic>;
      if (!mounted) return;
      Navigator.pushNamed(context, '/track', arguments: booking['sales_order']);
    } on DioException {
      setState(() => _error = 'Booking failed — your quote may have expired');
    } finally {
      setState(() => _busy = false);
    }
  }

  String _fmt(int minor, String ccy) {
    final amount = minor / 100;
    return ccy == 'NGN'
        ? '₦${amount.toStringAsFixed(2)}'
        : '€${amount.toStringAsFixed(2)}';
  }

  @override
  Widget build(BuildContext context) {
    final q = _quote;
    return Scaffold(
      appBar: AppBar(title: const Text('Book a ride')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _pickup,
            decoration: const InputDecoration(labelText: 'Pickup address'),
          ),
          TextField(
            controller: _dropoff,
            decoration: const InputDecoration(labelText: 'Dropoff address'),
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            initialValue: _serviceClass,
            decoration: const InputDecoration(labelText: 'Service class'),
            items: const [
              DropdownMenuItem(value: 'economy', child: Text('Economy')),
              DropdownMenuItem(value: 'business', child: Text('Business')),
              DropdownMenuItem(value: 'executive', child: Text('Executive')),
              DropdownMenuItem(value: 'sprinter', child: Text('Sprinter')),
            ],
            onChanged: (v) => setState(() => _serviceClass = v ?? 'business'),
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _busy ? null : _getQuote,
            child: Text(_busy ? 'Quoting…' : 'Get quote'),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ),
          if (q != null) ...[
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _fmt(q['quoted_minor'] as int, q['currency'] as String),
                      style: Theme.of(context).textTheme.headlineMedium,
                    ),
                    Text(
                      'quote engine ${q['engine_version']} · '
                      'valid ${q['valid_minutes']} min',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    const SizedBox(height: 12),
                    FilledButton.tonal(
                      onPressed: _busy ? null : _confirm,
                      child: const Text('Confirm booking'),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
