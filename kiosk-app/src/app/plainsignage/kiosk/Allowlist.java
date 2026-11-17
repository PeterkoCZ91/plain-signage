package app.plainsignage.kiosk;

import java.util.Locale;

/**
 * Pure (no Android) check of the main-frame address: https, exactly our host
 * and port, no user:pass@, path only /, /connect or /tv/... (under the
 * base path, if any). It parses the address by hand so it can be tested with
 * plain javac+java.
 *
 * Returns the rejection reason, or null when the address is allowed. The
 * reason never contains the query, fragment or user-info (they may carry
 * secrets) — only the scheme and host, or the category of the error.
 */
final class Allowlist {
    private Allowlist() {}

    /** null = allowed, otherwise a short rejection reason (without secrets). */
    static String rejectionReason(String url, String base) {
        Parts a = parse(url);
        if (a == null) {
            return "address cannot be parsed";
        }
        Parts b = parse(base);
        if (b == null) {
            return "invalid service base";
        }
        String desc = "scheme=" + a.scheme + " host=" + (a.host.isEmpty() ? "?" : a.host);
        if (!"https".equals(a.scheme)) {
            return "scheme is not https (" + desc + ")";
        }
        if (a.hasUser) {
            return "address contains user-info (" + desc + ")";
        }
        if (!a.host.equals(b.host)) {
            return "host outside the allowlist (" + desc + ")";
        }
        if (a.port != b.port) {
            return "port outside the allowlist (" + desc + ")";
        }
        String path = a.path;
        String prefix = b.path.endsWith("/") ? b.path.substring(0, b.path.length() - 1) : b.path;
        if (!path.startsWith(prefix)) {
            return "path outside the allowlist (" + desc + ")";
        }
        String rel = path.substring(prefix.length());
        if (rel.isEmpty()) {
            rel = "/";
        }
        if (!rel.startsWith("/")) {
            return "path outside the allowlist (" + desc + ")";
        }
        String lower = rel.toLowerCase(Locale.ROOT);
        if (lower.indexOf('\\') >= 0 || lower.contains("%2e") || lower.contains("%2f")
                || lower.contains("%5c") || lower.contains("//")) {
            return "suspicious path (" + desc + ")";
        }
        for (String segment : rel.split("/", -1)) {
            if (segment.equals("..") || segment.equals(".")) {
                return "path with a dot segment (" + desc + ")";
            }
        }
        if (rel.equals("/") || rel.equals("/connect") || rel.equals("/connect/")
                || (rel.startsWith("/tv/") && rel.length() > 4)) {
            return null;
        }
        return "path outside the allowlist (" + desc + ")";
    }

    static boolean isAllowed(String url, String base) {
        return rejectionReason(url, base) == null;
    }

    private static final class Parts {
        String scheme;
        String host;
        int port;
        boolean hasUser;
        String path;
    }

    /** Manual parse of scheme://[user@]host[:port][/path][?query][#fragment]. */
    private static Parts parse(String s) {
        if (s == null) {
            return null;
        }
        int sep = s.indexOf("://");
        if (sep <= 0) {
            return null;
        }
        Parts c = new Parts();
        c.scheme = s.substring(0, sep).toLowerCase(Locale.ROOT);
        String rest = s.substring(sep + 3);
        int end = rest.length();
        for (int i = 0; i < rest.length(); i++) {
            char ch = rest.charAt(i);
            if (ch == '/' || ch == '?' || ch == '#') {
                end = i;
                break;
            }
        }
        String authority = rest.substring(0, end);
        String remaining = rest.substring(end);
        int q = remaining.length();
        for (int i = 0; i < remaining.length(); i++) {
            char ch = remaining.charAt(i);
            if (ch == '?' || ch == '#') {
                q = i;
                break;
            }
        }
        c.path = remaining.substring(0, q);
        int at = authority.lastIndexOf('@');
        c.hasUser = at >= 0;
        String hp = at >= 0 ? authority.substring(at + 1) : authority;
        c.port = c.scheme.equals("https") ? 443 : c.scheme.equals("http") ? 80 : -1;
        int dp = hp.lastIndexOf(':');
        if (dp >= 0 && hp.indexOf(']') < dp) {
            try {
                c.port = Integer.parseInt(hp.substring(dp + 1));
            } catch (NumberFormatException e) {
                return null;
            }
            hp = hp.substring(0, dp);
        }
        c.host = hp.toLowerCase(Locale.ROOT);
        if (c.host.isEmpty()) {
            return null;
        }
        return c;
    }
}
