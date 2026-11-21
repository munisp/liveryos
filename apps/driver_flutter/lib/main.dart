import 'package:flutter/material.dart';
import 'ride_queue.dart';
import 'position_reporter.dart';

/// LiveryOS driver app (native, Android-first for NG device economics; iOS
/// build from the same codebase).
///
/// Honesty rules carried over from the PWA:
/// - Transitions are sent to the bench and the SERVER validates
///   LEGAL_TRANSITIONS; the button layout only *hints* what's legal.
/// - Position reporting labels its state ("live" / "queued offline —
///   n pending") — it never claims a fix reached the server when it didn't.
/// - Masked calling joins a LiveKit room; if the token request fails the
///   call button says "calling unavailable", it does not dial the raw
///   number (un-masking is a deliberate dispatch operation, not a fallback).
void main() {
  runApp(const DriverApp());
}

class DriverApp extends StatelessWidget {
  const DriverApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LiveryOS Driver',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF2F5D3A)),
        useMaterial3: true,
      ),
      home: const RideQueueScreen(),
    );
  }
}

class RideQueueScreen extends StatefulWidget {
  const RideQueueScreen({super.key});

  @override
  State<RideQueueScreen> createState() => _RideQueueScreenState();
}

class _RideQueueScreenState extends State<RideQueueScreen> {
  final queue = RideQueue();
  late final PositionReporter reporter;

  @override
  void initState() {
    super.initState();
    reporter = PositionReporter(
      onFix: (fix) => queue.sendPosition(fix),
      onStateChange: () => setState(() {}),
    );
    queue.addListener(() => setState(() {}));
    queue.refresh();
    reporter.start();
  }

  @override
  void dispose() {
    reporter.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final rides = queue.assignedRides;
    return Scaffold(
      appBar: AppBar(
        title: const Text('My rides'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(24),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Text(
              reporter.stateLabel, // "live" | "queued offline — n pending"
              style: const TextStyle(fontSize: 12),
            ),
          ),
        ),
      ),
      body: rides.isEmpty
          ? Center(child: Text(queue.offline
              ? 'Offline — showing last known queue'
              : 'No assigned rides'))
          : ListView.builder(
              itemCount: rides.length,
              itemBuilder: (context, i) => RideCard(
                ride: rides[i],
                onTransition: (target) => queue.transition(rides[i], target),
                onCall: () => queue.callPartyMasked(rides[i]),
              ),
            ),
    );
  }
}
