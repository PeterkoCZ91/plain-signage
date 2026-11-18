package app.plainsignage.kiosk;

/**
 * Classification of a failed main-page load for the status screen: pure logic
 * without Android (test test/LoadErrorTest.java). The codes are the
 * WebViewClient.ERROR_* constants, copied here as numbers so the class can be
 * tested without the Android SDK. The texts live in res/values (en) and
 * res/values-cs; MainActivity maps a Kind to its string resource.
 */
final class LoadError {

    /** What the status screen tells the person standing at the TV. */
    enum Kind {
        /** Certificate or SSL handshake problem. */
        CERTIFICATE,
        /** The server did not answer in time, or refused or dropped the connection. */
        SERVER_UNREACHABLE,
        /** The name cannot be resolved, or the box has no network. */
        NETWORK,
        /** The server answered with HTTP 5xx. */
        SERVER_ERROR,
        /** Anything else (bad URL, redirect loop, unknown). */
        OTHER
    }

    /** Why the certificate was rejected (android.net.http.SslError primary error). */
    enum SslReason { NOT_YET_VALID, EXPIRED, HOST_MISMATCH, UNTRUSTED, DATE_INVALID, OTHER }

    /** SslError.SSL_* values, copied as numbers (testable without the Android SDK). */
    static final int SSL_NOTYETVALID = 0;
    static final int SSL_EXPIRED = 1;
    static final int SSL_IDMISMATCH = 2;
    static final int SSL_UNTRUSTED = 3;
    static final int SSL_DATE_INVALID = 4;
    static final int SSL_INVALID = 5;

    static final int ERROR_UNKNOWN = -1;
    static final int ERROR_HOST_LOOKUP = -2;
    static final int ERROR_CONNECT = -6;
    static final int ERROR_IO = -7;
    static final int ERROR_TIMEOUT = -8;
    static final int ERROR_FAILED_SSL_HANDSHAKE = -11;

    private LoadError() {}

    /**
     * Kind for a WebViewClient error code. When the box reports no network at
     * all, every connection-type failure is a network problem, not the server's.
     */
    static Kind classify(int code, boolean online) {
        switch (code) {
            case ERROR_FAILED_SSL_HANDSHAKE:
                return Kind.CERTIFICATE;
            case ERROR_HOST_LOOKUP:
                return Kind.NETWORK;
            case ERROR_CONNECT:
            case ERROR_IO:
            case ERROR_TIMEOUT:
                return online ? Kind.SERVER_UNREACHABLE : Kind.NETWORK;
            default:
                return online ? Kind.OTHER : Kind.NETWORK;
        }
    }

    /** Reason for SslError.getPrimaryError(). */
    static SslReason classifySsl(int primaryError) {
        switch (primaryError) {
            case SSL_NOTYETVALID: return SslReason.NOT_YET_VALID;
            case SSL_EXPIRED: return SslReason.EXPIRED;
            case SSL_IDMISMATCH: return SslReason.HOST_MISMATCH;
            case SSL_UNTRUSTED: return SslReason.UNTRUSTED;
            case SSL_DATE_INVALID: return SslReason.DATE_INVALID;
            default: return SslReason.OTHER;
        }
    }

    /** Wrong date on the box can cause (or look like) these, so the text tells to check it. */
    static boolean suggestsClockCheck(SslReason r) {
        return r == SslReason.NOT_YET_VALID || r == SslReason.EXPIRED
                || r == SslReason.DATE_INVALID || r == SslReason.UNTRUSTED;
    }

    /** Kind for the HTTP status of the main page: 5xx = SERVER_ERROR, else OTHER. */
    static Kind classifyHttp(int status) {
        return status >= 500 && status <= 599 ? Kind.SERVER_ERROR : Kind.OTHER;
    }

    /** Seconds as m:ss for the countdown, negative counts as 0. */
    static String countdown(long seconds) {
        long s = Math.max(0, seconds);
        return (s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
    }

    /** Whole seconds left until the retry, rounded up, never negative. */
    static long secondsLeft(long nowMs, long retryAtMs) {
        long ms = retryAtMs - nowMs;
        return ms <= 0 ? 0 : (ms + 999) / 1000;
    }
}
