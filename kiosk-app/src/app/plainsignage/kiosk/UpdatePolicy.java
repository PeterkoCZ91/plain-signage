package app.plainsignage.kiosk;

import java.net.URI;
import java.util.Locale;

/**
 * Pure logic of the controlled kiosk update (no Android, testable with javac+java).
 *
 * An update is not started at random: only when the admin requests it for the
 * screen, or once shortly after the box is switched on (someone is usually
 * there then and can press OK on the remote). Android 9/10 without device
 * owner cannot install silently.
 */
final class UpdatePolicy {

    /** How often the kiosk asks the server (an admin request shows up within a minute). */
    static final long CHECK_INTERVAL_MS = 60 * 1000;
    /** Only a process started within this time after boot treats the check as "after power-on". */
    static final long AFTER_POWER_ON_WINDOW_MS = 10 * 60 * 1000;
    /** How long to wait for confirmation on the TV before the kiosk returns to the content. */
    static final long CONFIRMATION_WAIT_MS = 5 * 60 * 1000;
    /** Cap on the size of the downloaded APK (today's one is tens of kB). */
    static final long MAX_SIZE = 30L * 1024 * 1024;

    private UpdatePolicy() {}

    /**
     * Start an update? Only to a newer version, with nothing running, and only
     * if the admin requested it or this is the first check after power-on.
     */
    static boolean shouldStart(int have, int offered, boolean requested,
                               boolean afterPowerOn, boolean inProgress) {
        if (inProgress || offered <= have) {
            return false;
        }
        return requested || afterPowerOn;
    }

    /** The APK link must be a path on our server under /kiosk/ (not a foreign host). */
    static boolean isValidApkPath(String path) {
        return path != null && path.matches("/kiosk/[A-Za-z0-9._-]+\\.apk");
    }

    static boolean isValidSha256(String sha) {
        return sha != null && sha.matches("[0-9a-f]{64}");
    }

    static String hex(byte[] data) {
        StringBuilder sb = new StringBuilder(data.length * 2);
        for (byte b : data) {
            sb.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        }
        return sb.toString();
    }

    /** Screen name from an address …/tv/<name>, otherwise null (pairing, root). */
    static String screen(String url) {
        if (url == null) {
            return null;
        }
        String path;
        try {
            path = new URI(url).getPath();
        } catch (Exception e) {
            return null;
        }
        if (path == null || !path.startsWith("/tv/")) {
            return null;
        }
        String name = path.substring(4);
        if (name.endsWith("/")) {
            name = name.substring(0, name.length() - 1);
        }
        return name.matches("[A-Za-z0-9_-]{1,80}") ? name : null;
    }
}
