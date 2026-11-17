package app.plainsignage.kiosk;

/**
 * Test of the pure Health logic without Android:
 *   javac -d <tmp> src/app/plainsignage/kiosk/Health.java test/HealthTest.java
 *   java -cp <tmp> app.plainsignage.kiosk.HealthTest
 */
public class HealthTest {
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
        check("mb 0", Health.toMb(0), 0);
        check("mb negative", Health.toMb(-5), 0);
        check("mb rounded down", Health.toMb(412L * 1024 * 1024 + 999), 412);
        check("mb cap", Health.toMb(200L * 1024 * 1024 * 1024), 65536);

        check("network wifi", Health.networkType(true, false), "wifi");
        check("network eth", Health.networkType(false, true), "eth");
        check("network other", Health.networkType(false, false), "other");

        check("rssi -58", Health.isValidRssi(-58), true);
        check("rssi -127 unknown", Health.isValidRssi(-127), false);
        check("rssi 0", Health.isValidRssi(0), false);
        check("rssi positive", Health.isValidRssi(5), false);
        check("rssi -200", Health.isValidRssi(-200), false);

        check("everything wifi", Health.queryParams(412, false, 1838, 3600, "wifi", -58, 2),
                "&ram=412&ram_low=0&ram_total=1838&up=3600&net=wifi&rssi=-58&crash=2");
        check("eth without rssi", Health.queryParams(90, true, 1838, 10, "eth", -58, 0),
                "&ram=90&ram_low=1&ram_total=1838&up=10&net=eth&crash=0");
        check("wifi with unknown rssi", Health.queryParams(90, false, 1838, 10, "wifi", -127, 0),
                "&ram=90&ram_low=0&ram_total=1838&up=10&net=wifi&crash=0");
        check("unknown RAM and network", Health.queryParams(-1, false, -1, 10, null, -50, 0),
                "&up=10&crash=0");
        check("foreign network type", Health.queryParams(-1, false, -1, -1, "lte", -50, -1), "");
        check("crashes cap", Health.queryParams(-1, false, -1, -1, null, 0, 5000000),
                "&crash=1000000");
        check("uptime over the limit", Health.queryParams(-1, false, -1, 2000000000L, null, 0, -1), "");

        System.out.println(failures == 0 ? "ALL OK" : "FAILURES: " + failures);
        if (failures != 0) {
            System.exit(1);
        }
    }
}
