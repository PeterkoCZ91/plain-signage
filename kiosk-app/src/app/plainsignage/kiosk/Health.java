package app.plainsignage.kiosk;

/**
 * Box health telemetry for the server: pure logic without Android (test
 * test/HealthTest.java). Updater collects the values and sends them in the
 * same GET /api/kiosk/release query (no extra connection):
 *
 *   ram, ram_low, ram_total  free / total RAM in MB, the system reports low memory
 *   up                       box uptime in s (the server detects a restart from a drop)
 *   net, rssi                network type wifi|eth|other, Wi-Fi signal in dBm (Wi-Fi only)
 *   crash                    WebView renderer crashes since the process started
 *
 * The SSID is deliberately not sent: on Android 9/10 we cannot get it without
 * the location permission anyway and it is needless personal data. Values
 * outside the server's limits are not sent at all.
 */
final class Health {

    /** Network type values sent to the server. */
    static final String NET_WIFI = "wifi";
    static final String NET_ETH = "eth";
    static final String NET_OTHER = "other";

    static final int MAX_MB = 65536;
    static final long MAX_UPTIME_S = 1000000000L;
    static final int MAX_CRASHES = 1000000;
    /** WifiManager returns -127 (INVALID_RSSI) when it does not know the strength. */
    static final int INVALID_RSSI = -127;

    private Health() {}

    /** Bytes to MB (rounded down), clamped to 0..MAX_MB. */
    static int toMb(long bytes) {
        if (bytes <= 0) {
            return 0;
        }
        long m = bytes / (1024L * 1024L);
        return m > MAX_MB ? MAX_MB : (int) m;
    }

    /** Network type for the server. */
    static String networkType(boolean wifi, boolean eth) {
        return wifi ? NET_WIFI : eth ? NET_ETH : NET_OTHER;
    }

    /** Is it worth sending the RSSI? Only a really measured value (-126..-1 dBm). */
    static boolean isValidRssi(int rssi) {
        return rssi > INVALID_RSSI && rssi < 0;
    }

    /**
     * The query part "&ram=…&ram_low=…&ram_total=…&up=…&net=…[&rssi=…]&crash=…".
     * Negative ram/ramTotal/up = unknown (omitted); net null = unknown network
     * (then no rssi either); rssi is added only for wifi and a valid value.
     */
    static String queryParams(int ramMb, boolean ramLow, int ramTotalMb, long uptimeS,
                              String net, int rssi, int crashes) {
        StringBuilder q = new StringBuilder();
        if (ramMb >= 0 && ramMb <= MAX_MB) {
            q.append("&ram=").append(ramMb).append("&ram_low=").append(ramLow ? 1 : 0);
        }
        if (ramTotalMb > 0 && ramTotalMb <= MAX_MB) {
            q.append("&ram_total=").append(ramTotalMb);
        }
        if (uptimeS >= 0 && uptimeS <= MAX_UPTIME_S) {
            q.append("&up=").append(uptimeS);
        }
        if (NET_WIFI.equals(net) || NET_ETH.equals(net) || NET_OTHER.equals(net)) {
            q.append("&net=").append(net);
            if (NET_WIFI.equals(net) && isValidRssi(rssi)) {
                q.append("&rssi=").append(rssi);
            }
        }
        if (crashes >= 0) {
            q.append("&crash=").append(Math.min(crashes, MAX_CRASHES));
        }
        return q.toString();
    }
}
