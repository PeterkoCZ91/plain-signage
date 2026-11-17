package app.plainsignage.kiosk;

/**
 * Test of the pure UpdatePolicy logic without Android:
 *   javac -d <tmp> src/app/plainsignage/kiosk/UpdatePolicy.java test/UpdatePolicyTest.java
 *   java -cp <tmp> app.plainsignage.kiosk.UpdatePolicyTest
 */
public class UpdatePolicyTest {
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
        check("same version, requested -> no", UpdatePolicy.shouldStart(9, 9, true, true, false), false);
        check("older on the server -> no", UpdatePolicy.shouldStart(9, 8, true, true, false), false);
        check("newer, nothing -> no (no random updates)", UpdatePolicy.shouldStart(9, 10, false, false, false), false);
        check("newer, requested -> yes", UpdatePolicy.shouldStart(9, 10, true, false, false), true);
        check("newer, after power-on -> yes", UpdatePolicy.shouldStart(9, 10, false, true, false), true);
        check("already in progress -> no", UpdatePolicy.shouldStart(9, 10, true, true, true), false);

        check("apk path ok", UpdatePolicy.isValidApkPath("/kiosk/signage-kiosk.apk"), true);
        check("apk path foreign host", UpdatePolicy.isValidApkPath("https://zly.cz/kiosk/a.apk"), false);
        check("apk path traversal", UpdatePolicy.isValidApkPath("/kiosk/../x.apk"), false);
        check("apk path outside /kiosk", UpdatePolicy.isValidApkPath("/media/a.apk"), false);
        check("apk path null", UpdatePolicy.isValidApkPath(null), false);

        check("sha ok", UpdatePolicy.isValidSha256(
                "ab8c324f5c1720065f44a7b3a4eba890a89c1aad3b3d7081ff89bd0f38681d3e"), true);
        check("sha uppercase", UpdatePolicy.isValidSha256(
                "AB8C324F5C1720065F44A7B3A4EBA890A89C1AAD3B3D7081FF89BD0F38681D3E"), false);
        check("sha too short", UpdatePolicy.isValidSha256("ab"), false);
        check("hex", UpdatePolicy.hex(new byte[]{0, 15, (byte) 255}), "000fff");

        check("screen", UpdatePolicy.screen("https://x.cz/tv/pracovna-1a2b"), "pracovna-1a2b");
        check("screen with a slash", UpdatePolicy.screen("https://x.cz/tv/abc/"), "abc");
        check("screen with a query", UpdatePolicy.screen("https://x.cz/tv/abc?lang=en"), "abc");
        check("pairing -> null", UpdatePolicy.screen("https://x.cz/connect"), null);
        check("tv without a name -> null", UpdatePolicy.screen("https://x.cz/tv/"), null);
        check("odd characters -> null", UpdatePolicy.screen("https://x.cz/tv/a%2fb"), null);
        check("null", UpdatePolicy.screen(null), null);

        System.out.println(failures == 0 ? "ALL OK" : "FAILURES: " + failures);
        if (failures != 0) {
            System.exit(1);
        }
    }
}
