package app.plainsignage.kiosk;

/**
 * Pure logic of the return-to-foreground watchdog (no Android, testable with javac+java).
 *
 * Decides by time only: the kiosk is the HOME app, so when another activity
 * takes over and nobody left on purpose, it should come back after the
 * threshold. A deliberate exit (double BACK) switches the watchdog off for a
 * fixed time so a technician can work.
 */
final class Watchdog {

    /** How long the kiosk may stay out of the foreground before it returns. */
    static final long THRESHOLD_MS = 20000;
    /** How long nothing is brought back after a deliberate exit. */
    static final long PAUSE_AFTER_EXIT_MS = 10 * 60 * 1000;

    private final long thresholdMs;
    private final long pauseMs;
    /** When the kiosk was last in the foreground (resumed); 0 = never yet. */
    private long lastInForeground;
    /** Until when the watchdog is off because of a deliberate exit. */
    private long disabledUntil;

    Watchdog() {
        this(THRESHOLD_MS, PAUSE_AFTER_EXIT_MS);
    }

    Watchdog(long thresholdMs, long pauseMs) {
        this.thresholdMs = thresholdMs;
        this.pauseMs = pauseMs;
    }

    /** The kiosk is in the foreground (right now). */
    void inForeground(long now) {
        lastInForeground = now;
    }

    /** The user left on purpose (double BACK). */
    void deliberateExit(long now) {
        disabledUntil = now + pauseMs;
    }

    /** Temporarily do not bring back (e.g. the system update dialog is waiting for OK). */
    void pause(long now, long ms) {
        disabledUntil = now + Math.min(ms, pauseMs);
    }

    /**
     * Should the kiosk come back? Yes, when it has been out of the foreground
     * longer than the threshold and no pause after a deliberate exit is
     * running. After a clock jump backwards (now before the last record) it
     * does not return; a pause longer than configured is shortened too.
     */
    boolean shouldReturn(long now, boolean inForeground) {
        if (inForeground) {
            lastInForeground = now;
            return false;
        }
        if (disabledUntil - now > pauseMs) {
            disabledUntil = now + pauseMs;          // the clock went backwards
        }
        if (now < disabledUntil) {
            return false;
        }
        if (lastInForeground == 0 || now < lastInForeground) {
            return false;
        }
        return now - lastInForeground >= thresholdMs;
    }

    /** After a return attempt the countdown restarts so starts do not pile up. */
    void attemptMade(long now) {
        lastInForeground = now;
    }
}
