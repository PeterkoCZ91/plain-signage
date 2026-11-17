package app.plainsignage.kiosk;

/**
 * Test of the pure Allowlist function without Android:
 *   javac -d /tmp/t src/app/plainsignage/kiosk/Allowlist.java test/AllowlistTest.java
 *   java -cp /tmp/t app.plainsignage.kiosk.AllowlistTest
 * It does not get into the APK (build.sh compiles only src/ and build/gen/).
 */
public class AllowlistTest {
    static int failures = 0;
    static final String BASE = "https://example.com";

    static void ok(String u) { check(u, BASE, true); }
    static void no(String u) { check(u, BASE, false); }

    static void check(String u, String base, boolean expected) {
        boolean actual = Allowlist.isAllowed(u, base);
        if (actual != expected) {
            failures++;
            System.out.println("ERROR: " + u + " -> " + actual + ", expected " + expected);
        } else {
            System.out.println("ok: " + (expected ? "allowed " : "rejected ") + u
                    + (expected ? "" : "  [" + Allowlist.rejectionReason(u, base) + "]"));
        }
    }

    public static void main(String[] a) {
        ok("https://example.com");
        ok("https://example.com/");
        ok("https://EXAMPLE.com/connect");
        ok("https://example.com/connect?code=1");
        ok("https://example.com/tv/screen-1");
        ok("https://example.com/tv/a/b?x=1#f");
        ok("https://example.com:443/tv/x");
        no("http://example.com/tv/x");
        no("https://evil.example.com/tv/x");
        no("https://example.com.evil.com/tv/x");
        no("https://example.com@example.net/tv/x");
        no("https://user:pw@example.com/tv/x");
        no("https://example.com:8443/tv/x");
        no("https://example.com/admin");
        no("https://example.com/media/x.mp4");
        no("https://example.com/api/screens");
        no("https://example.com/tv");
        no("https://example.com/tv/");
        no("https://example.com/connectx");
        no("https://example.com/tv/../admin");
        no("https://example.com/tv/%2e%2e/admin");
        no("https://example.com/tv\\..\\admin");
        no("https://example.com//evil.com");
        no("javascript:alert(1)");
        no("file:///etc/passwd");
        no("");
        no("neni-url");
        no("https:///tv/x");
        no(null);
        // base with a path
        check("https://d.example.com/app/tv/x", "https://d.example.com/app", true);
        check("https://d.example.com/app/connect", "https://d.example.com/app", true);
        check("https://d.example.com/tv/x", "https://d.example.com/app", false);
        // the reason contains neither the query nor user-info
        String d = Allowlist.rejectionReason("https://leakuser@example.com/x?param=abc", BASE);
        if (d.contains("leakuser") || d.contains("param")) { failures++; System.out.println("ERROR: secret leak: " + d); }
        System.out.println(failures == 0 ? "ALL OK" : "FAILURES: " + failures);
        if (failures != 0) System.exit(1);
    }
}
