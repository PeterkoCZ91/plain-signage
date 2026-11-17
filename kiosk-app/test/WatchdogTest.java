package app.plainsignage.kiosk;

/**
 * Test of the pure Watchdog logic without Android:
 *   javac -d <tmp> src/app/plainsignage/kiosk/Watchdog.java test/WatchdogTest.java
 *   java -cp <tmp> app.plainsignage.kiosk.WatchdogTest
 */
public class WatchdogTest {
    static int failures = 0;

    static void check(String what, boolean actual, boolean expected) {
        if (actual != expected) {
            failures++;
            System.out.println("ERROR: " + what + " -> " + actual + ", expected " + expected);
        } else {
            System.out.println("ok: " + what);
        }
    }

    public static void main(String[] a) {
        long S = 1000;
        Watchdog w = new Watchdog(20 * S, 600 * S);

        check("never was in the foreground -> do not return", w.shouldReturn(100 * S, false), false);
        w.inForeground(1000 * S);
        check("in the foreground -> do not return", w.shouldReturn(1010 * S, true), false);
        check("out for 5 s -> do not return", w.shouldReturn(1015 * S, false), false);
        check("out for 19 s -> do not return", w.shouldReturn(1029 * S, false), false);
        check("out for exactly 20 s -> return", w.shouldReturn(1030 * S, false), true);
        w.attemptMade(1030 * S);
        check("right after an attempt -> do not return", w.shouldReturn(1035 * S, false), false);
        check("20 s after an attempt -> return again", w.shouldReturn(1050 * S, false), true);
        check("back in the foreground -> do not return", w.shouldReturn(1060 * S, true), false);

        w.deliberateExit(2000 * S);
        check("1 min after exit -> do not return", w.shouldReturn(2060 * S, false), false);
        check("9:59 after exit -> do not return", w.shouldReturn(2599 * S, false), false);
        check("10:00 after exit -> return", w.shouldReturn(2600 * S, false), true);

        Watchdog h = new Watchdog(20 * S, 600 * S);
        h.inForeground(5000 * S);
        check("clock backwards -> do not return", h.shouldReturn(4000 * S, false), false);

        Watchdog p = new Watchdog(20 * S, 600 * S);
        p.inForeground(100 * S);
        p.deliberateExit(100000 * S);
        check("exit, then the clock jumped back -> the pause is shortened, do not return at once",
                p.shouldReturn(200 * S, false), false);
        check("... and after 10 min return", p.shouldReturn(801 * S, false), true);

        Watchdog v = new Watchdog();
        v.inForeground(0 + 1);
        check("default threshold 20 s", v.shouldReturn(1 + Watchdog.THRESHOLD_MS, false), true);

        // pause because of the update dialog (5 min), cap = pause after a deliberate exit
        Watchdog pz = new Watchdog(20 * S, 600 * S);
        pz.inForeground(5000 * S);
        pz.pause(5000 * S, 300 * S);
        check("paused 4 min -> do not return", pz.shouldReturn(5240 * S, false), false);
        check("after 5 min -> return", pz.shouldReturn(5301 * S, false), true);
        Watchdog q = new Watchdog(20 * S, 600 * S);
        q.inForeground(9000 * S);
        q.pause(9000 * S, 99999 * S);
        check("pause shortened to the cap", q.shouldReturn(9601 * S, false), true);

        System.out.println(failures == 0 ? "ALL OK" : "FAILURES: " + failures);
        if (failures != 0) {
            System.exit(1);
        }
    }
}
