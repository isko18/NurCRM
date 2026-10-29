/// Reference implementation for cafe WebSocket clients (Flutter/Dart).
/// Copy into your Flutter app and wire [refreshAccessToken] / [onAuthFailure].
///
/// Dependencies: web_socket_channel, http (or your API client).
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

typedef RefreshAccessToken = Future<String?> Function();
typedef OnAuthFailure = Future<void> Function(String reason);
typedef OnConnected = void Function();
typedef OnDisconnected = void Function(int? code, String? reason);
typedef OnMessage = void Function(Map<String, dynamic> message);

const int wsCloseUnauthorized = 4401;
const int wsCloseForbidden = 4403;

const List<Duration> _backoffSteps = [
  Duration(seconds: 1),
  Duration(seconds: 2),
  Duration(seconds: 5),
  Duration(seconds: 10),
  Duration(seconds: 30),
];

class CafeWebSocketManager {
  CafeWebSocketManager({
    required this.buildUrl,
    required this.readAccessToken,
    required this.refreshAccessToken,
    required this.onAuthFailure,
    this.onConnected,
    this.onDisconnected,
    this.onMessage,
  });

  final String Function(String accessToken) buildUrl;
  final Future<String?> Function() readAccessToken;
  final RefreshAccessToken refreshAccessToken;
  final OnAuthFailure onAuthFailure;
  final OnConnected? onConnected;
  final OnDisconnected? onDisconnected;
  final OnMessage? onMessage;

  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _reconnectTimer;

  bool _connecting = false;
  bool _connected = false;
  bool _stopped = false;
  int _reconnectAttempt = 0;

  bool get isConnected => _connected;
  bool get isConnecting => _connecting;

  Future<void> connect() async {
    if (_stopped || _connecting || _connected) {
      return;
    }
    _connecting = true;

    await _closeExisting();

    final token = await readAccessToken();
    if (token == null || token.isEmpty) {
      _connecting = false;
      await onAuthFailure('missing_access_token');
      return;
    }

    final url = buildUrl(token);
    try {
      _channel = IOWebSocketChannel.connect(
        Uri.parse(url),
        headers: const {'User-Agent': 'nurcrm-flutter-ws'},
      );
      _subscription = _channel!.stream.listen(
        _handleMessage,
        onDone: _handleDone,
        onError: _handleError,
        cancelOnError: true,
      );
      _connected = true;
      _connecting = false;
      _reconnectAttempt = 0;
      onConnected?.call();
    } on WebSocketException catch (e) {
      _connecting = false;
      await _handleHandshakeFailure(e.message);
    } on SocketException catch (e) {
      _connecting = false;
      _scheduleNetworkReconnect('socket:${e.message}');
    } catch (e) {
      _connecting = false;
      _scheduleNetworkReconnect('unknown:$e');
    }
  }

  Future<void> disconnect({bool permanent = true}) async {
    if (permanent) {
      _stopped = true;
    }
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    await _closeExisting();
  }

  Future<void> reconnectNow() async {
    _reconnectAttempt = 0;
    await connect();
  }

  Future<void> _closeExisting() async {
    _connected = false;
    await _subscription?.cancel();
    _subscription = null;
    await _channel?.sink.close(wsCloseUnauthorized);
    _channel = null;
  }

  void _handleMessage(dynamic raw) {
    if (raw is! String) {
      return;
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) {
        onMessage?.call(decoded);
      }
    } catch (_) {}
  }

  Future<void> _handleDone() async {
    final code = _channel?.closeCode;
    final reason = _channel?.closeReason;
    _connected = false;
    _connecting = false;
    onDisconnected?.call(code, reason);

    if (_stopped) {
      return;
    }

    if (_isAuthClose(code) || code == HttpStatus.forbidden) {
      await _handleAuthFailure('close_code:$code');
      return;
    }

    _scheduleNetworkReconnect('done:$code');
  }

  Future<void> _handleError(Object error) async {
    _connected = false;
    _connecting = false;
    onDisconnected?.call(null, error.toString());

    if (_stopped) {
      return;
    }

    final text = error.toString().toLowerCase();
    if (text.contains('403') || text.contains('401') || text.contains('4401')) {
      await _handleAuthFailure('stream_error:$error');
      return;
    }

    _scheduleNetworkReconnect('error:$error');
  }

  Future<void> _handleHandshakeFailure(String? message) async {
    final text = (message ?? '').toLowerCase();
    if (text.contains('403') || text.contains('401') || text.contains('429')) {
      await _handleAuthFailure('handshake:$message');
      return;
    }
    _scheduleNetworkReconnect('handshake:$message');
  }

  bool _isAuthClose(int? code) {
    return code == wsCloseUnauthorized || code == wsCloseForbidden;
  }

  Future<void> _handleAuthFailure(String reason) async {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;

    final refreshed = await refreshAccessToken();
    if (refreshed != null && refreshed.isNotEmpty) {
      _reconnectAttempt = 0;
      await connect();
      return;
    }

    await onAuthFailure(reason);
  }

  void _scheduleNetworkReconnect(String cause) {
    if (_stopped || _connecting || _connected) {
      return;
    }

    _reconnectTimer?.cancel();
    final idx = min(_reconnectAttempt, _backoffSteps.length - 1);
    final delay = _backoffSteps[idx];
    _reconnectAttempt += 1;

    _reconnectTimer = Timer(delay, () {
      connect();
    });
  }
}

/// Example wiring for orders socket:
/// ```dart
/// final ordersWs = CafeWebSocketManager(
///   buildUrl: (token) =>
///       'wss://app.nurcrm.kg/ws/cafe/orders/?token=$token',
///   readAccessToken: authStorage.readAccessToken,
///   refreshAccessToken: authApi.refreshAccessToken,
///   onAuthFailure: authController.logout,
/// );
/// await ordersWs.connect();
/// ```
