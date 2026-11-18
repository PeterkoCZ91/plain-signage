package app.plainsignage.kiosk;

/**
 * Text helpers for the start log line and the ADB diagnostics overview: pure
 * logic without Android (test test/DiagTest.java). Nothing here may let a
 * query string, user-info or token through, because the results go to logcat.
 */
final class Diag {

    private Diag() {}

    /** Host (with a non-default port) of an https/http URL, "-" when it cannot be read. */
    static String hostOf(String url) {
        if (url == null) {
            return "-";
        }
        int s = url.indexOf("://");
        if (s < 0) {
            return "-";
        }
        String rest = url.substring(s + 3);
        int end = rest.length();
        for (int i = 0; i < rest.length(); i++) {
            char c = rest.charAt(i);
            if (c == '/' || c == '?' || c == '#') {
                end = i;
                break;
            }
        }
        String authority = rest.substring(0, end);
        int at = authority.lastIndexOf('@');
        if (at >= 0) {
            authority = authority.substring(at + 1);
        }
        return authority.isEmpty() ? "-" : authority;
    }

    /**
     * The saved screen as the path /tv/<name> only (no host, query or fragment),
     * or null when the address is not a screen address (pairing, empty).
     */
    static String screenPath(String url) {
        if (url == null) {
            return null;
        }
        int s = url.indexOf("://");
        if (s < 0) {
            return null;
        }
        String rest = url.substring(s + 3);
        int slash = rest.indexOf('/');
        if (slash < 0) {
            return null;
        }
        String path = rest.substring(slash);
        int cut = path.length();
        for (int i = 0; i < path.length(); i++) {
            char c = path.charAt(i);
            if (c == '?' || c == '#') {
                cut = i;
                break;
            }
        }
        path = path.substring(0, cut);
        return path.startsWith("/tv/") && path.length() > 4 ? path : null;
    }

    /** One line for the start log: version, server host, whether a screen is saved. */
    static String startLine(String version, String baseUrl, String savedUrl) {
        String path = screenPath(savedUrl);
        return "start: version " + version + ", server " + hostOf(baseUrl)
                + ", screen " + (path == null ? "not saved" : "saved " + path);
    }

    /** Age as "12 s", "5 min", "3 h 04 min"; "-" when it never happened (ms <= 0). */
    static String age(long ms) {
        if (ms <= 0) {
            return "-";
        }
        long s = ms / 1000;
        if (s < 60) {
            return s + " s";
        }
        long m = s / 60;
        if (m < 60) {
            return m + " min";
        }
        return (m / 60) + " h " + (m % 60 < 10 ? "0" : "") + (m % 60) + " min";
    }
}
