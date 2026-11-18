package app.plainsignage.kiosk;

/**
 * Test of the pure Diag logic without Android:
 *   javac -d <tmp> src/app/plainsignage/kiosk/Diag.java test/DiagTest.java
 *   java -cp <tmp> app.plainsignage.kiosk.DiagTest
 */
public class DiagTest {
    static int failures = 0;

    static void check(String what, Object actual, Object expected) {
        boolean match = actual == null ? expected == null : actual.equals(expected);
        if (!match) {
            failures++;
            System.out.println("ERROR: " + what + " -> " + actual + ", expected " + expected);
        } else {
            System.out.println("ok: " + what);
        }
    }

    public static void main(String[] a) {
        check("host", Diag.hostOf("https://example.com/tv/lobby?t=secret"), "example.com");
        check("host with port", Diag.hostOf("https://example.com:8443/connect"), "example.com:8443");
        check("host without path", Diag.hostOf("https://example.com"), "example.com");
        check("host strips user-info", Diag.hostOf("https://user:pw@example.com/x"), "example.com");
        check("host query directly", Diag.hostOf("https://example.com?x=1"), "example.com");
        check("host null", Diag.hostOf(null), "-");
        check("host no scheme", Diag.hostOf("example.com/x"), "-");

        check("screen path", Diag.screenPath("https://example.com/tv/lobby"), "/tv/lobby");
        check("screen path drops query", Diag.screenPath("https://example.com/tv/lobby?token=abc#x"), "/tv/lobby");
        check("pairing is no screen", Diag.screenPath("https://example.com/connect"), null);
        check("empty tv path", Diag.screenPath("https://example.com/tv/"), null);
        check("no path", Diag.screenPath("https://example.com"), null);
        check("screen null", Diag.screenPath(null), null);

        check("start line saved", Diag.startLine("1.15", "https://example.com",
                "https://example.com/tv/lobby?token=abc"),
                "start: version 1.15, server example.com, screen saved /tv/lobby");
        check("start line pairing", Diag.startLine("1.15", "https://example.com",
                "https://example.com/connect"),
                "start: version 1.15, server example.com, screen not saved");
        check("start line has no token", Diag.startLine("1.15", "https://example.com",
                "https://example.com/tv/lobby?token=abc").contains("token"), false);

        check("age never", Diag.age(0), "-");
        check("age seconds", Diag.age(12000), "12 s");
        check("age minutes", Diag.age(5 * 60000L + 5), "5 min");
        check("age hours", Diag.age((3 * 60 + 4) * 60000L), "3 h 04 min");

        System.out.println(failures == 0 ? "ALL OK" : "FAILURES: " + failures);
        if (failures != 0) {
            System.exit(1);
        }
    }
}
