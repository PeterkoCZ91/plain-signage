package app.plainsignage.kiosk;

/**
 * Test of the pure LoadError logic without Android:
 *   javac -d <tmp> src/app/plainsignage/kiosk/LoadError.java test/LoadErrorTest.java
 *   java -cp <tmp> app.plainsignage.kiosk.LoadErrorTest
 */
public class LoadErrorTest {
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
        check("ssl handshake", LoadError.classify(-11, true), LoadError.Kind.CERTIFICATE);
        check("ssl handshake offline", LoadError.classify(-11, false), LoadError.Kind.CERTIFICATE);
        check("dns", LoadError.classify(-2, true), LoadError.Kind.NETWORK);
        check("timeout online", LoadError.classify(-8, true), LoadError.Kind.SERVER_UNREACHABLE);
        check("connect online", LoadError.classify(-6, true), LoadError.Kind.SERVER_UNREACHABLE);
        check("io online", LoadError.classify(-7, true), LoadError.Kind.SERVER_UNREACHABLE);
        check("timeout offline", LoadError.classify(-8, false), LoadError.Kind.NETWORK);
        check("connect offline", LoadError.classify(-6, false), LoadError.Kind.NETWORK);
        check("unknown online", LoadError.classify(-1, true), LoadError.Kind.OTHER);
        check("redirect loop online", LoadError.classify(-9, true), LoadError.Kind.OTHER);
        check("unknown offline", LoadError.classify(-1, false), LoadError.Kind.NETWORK);
        check("positive code", LoadError.classify(7, true), LoadError.Kind.OTHER);

        check("http 500", LoadError.classifyHttp(500), LoadError.Kind.SERVER_ERROR);
        check("http 503", LoadError.classifyHttp(503), LoadError.Kind.SERVER_ERROR);
        check("http 599", LoadError.classifyHttp(599), LoadError.Kind.SERVER_ERROR);
        check("http 404", LoadError.classifyHttp(404), LoadError.Kind.OTHER);
        check("http 600", LoadError.classifyHttp(600), LoadError.Kind.OTHER);

        check("ssl expired", LoadError.classifySsl(1), LoadError.SslReason.EXPIRED);
        check("ssl not yet valid", LoadError.classifySsl(0), LoadError.SslReason.NOT_YET_VALID);
        check("ssl id mismatch", LoadError.classifySsl(2), LoadError.SslReason.HOST_MISMATCH);
        check("ssl untrusted", LoadError.classifySsl(3), LoadError.SslReason.UNTRUSTED);
        check("ssl date invalid", LoadError.classifySsl(4), LoadError.SslReason.DATE_INVALID);
        check("ssl invalid", LoadError.classifySsl(5), LoadError.SslReason.OTHER);
        check("ssl unknown value", LoadError.classifySsl(42), LoadError.SslReason.OTHER);
        check("ssl negative", LoadError.classifySsl(-1), LoadError.SslReason.OTHER);
        check("clock hint expired", LoadError.suggestsClockCheck(LoadError.SslReason.EXPIRED), true);
        check("clock hint untrusted", LoadError.suggestsClockCheck(LoadError.SslReason.UNTRUSTED), true);
        check("no clock hint mismatch", LoadError.suggestsClockCheck(LoadError.SslReason.HOST_MISMATCH), false);
        check("no clock hint other", LoadError.suggestsClockCheck(LoadError.SslReason.OTHER), false);

        check("countdown 0", LoadError.countdown(0), "0:00");
        check("countdown 5", LoadError.countdown(5), "0:05");
        check("countdown 60", LoadError.countdown(60), "1:00");
        check("countdown 75", LoadError.countdown(75), "1:15");
        check("countdown negative", LoadError.countdown(-3), "0:00");

        check("left rounds up", LoadError.secondsLeft(1000, 5001), 5L);
        check("left exact", LoadError.secondsLeft(1000, 6000), 5L);
        check("left passed", LoadError.secondsLeft(7000, 6000), 0L);

        System.out.println(failures == 0 ? "ALL OK" : "FAILURES: " + failures);
        if (failures != 0) {
            System.exit(1);
        }
    }
}
