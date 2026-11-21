import 'package:flutter/material.dart';
import 'booking_screen.dart';
import 'tracking_screen.dart';

/// LiveryOS passenger app (native).
///
/// Same honesty contract as the PWA:
/// - ETA always renders with its source label ("~12 min (estimate)").
///   eta_source "none" renders "—".
/// - Offline state announces itself with the timestamp of the last
///   successful update; nothing pretends to be live.
/// - WhatsApp is the primary share/support channel on NG tenants;
///   SMS/voice on PT tenants (channel policy is server-side).
void main() {
  runApp(const PassengerApp());
}

class PassengerApp extends StatelessWidget {
  const PassengerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LiveryOS',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1D3A5F)),
        useMaterial3: true,
      ),
      initialRoute: '/book',
      routes: {
        '/book': (_) => const BookingScreen(),
        '/track': (_) => const TrackingScreen(),
      },
    );
  }
}
