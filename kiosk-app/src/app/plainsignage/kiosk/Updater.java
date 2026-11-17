package app.plainsignage.kiosk;

import android.app.ActivityManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.security.MessageDigest;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Controlled kiosk update from the server (for when — see UpdatePolicy).
 *
 * Every minute it asks GET /api/kiosk/release?device=&have=&state= ; this
 * also shows the server the version and update state in the admin. The same query also carries the box health (RAM, uptime, network/Wi-Fi, renderer
 * crashes — see Health). When an update is due it downloads the APK from
 * /kiosk/…, verifies size, SHA-256, package name and a higher versionCode
 * (the signature is checked by Android itself — a foreign signature makes it
 * refuse the install) and hands it to the PackageInstaller. On Android 9/10
 * that shows a dialog; it waits for OK at most CONFIRMATION_WAIT_MS, then the
 * kiosk returns to the content.
 *
 * Installing from "unknown sources" must be allowed once on the box via ADB:
 *   adb shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow
 * Without it nothing is downloaded and the admin shows the state no_permission.
 */
final class Updater {

    private static final String TAG = MainActivity.TAG;
    /** Broadcast action of the install result. */
    private static final String ACTION_INSTALL_STATE = "app.plainsignage.kiosk.INSTALL_STATE";
    /** Server endpoint polled for releases. */
    private static final String RELEASE_PATH = "/api/kiosk/release";

    /** States reported to the server (wire values, English; the server ignores unknown ones). */
    private static final String STATE_IDLE = "idle";
    private static final String STATE_DOWNLOADING = "downloading";
    private static final String STATE_WAITING = "waiting";
    private static final String STATE_INSTALLED = "installed";
    private static final String STATE_DECLINED = "declined";
    private static final String STATE_FAILED = "failed";
    private static final String STATE_NO_PERMISSION = "no_permission";

    /** Interface to the activity: where to get the address and how to return to the content. */
    interface Host {
        String currentUrl();
        void pauseWatchdog(long ms);
        void returnToContent();
        /** Own screen "a new version is ready, press OK" (for up to ms). */
        void showPrompt(String version, long ms);
        void hidePrompt();
        /** WebView renderer crashes since the process started (for the box health). */
        int crashCount();
    }

    private final Context ctx;
    private final Handler handler;
    private final Host host;
    private final boolean processAfterPowerOn;

    /** State for the server: idle|downloading|waiting|installed|declined|failed|no_permission. */
    private volatile String state = STATE_IDLE;
    private volatile boolean inProgress;
    /** The after-power-on check is done only once per process lifetime. */
    private boolean afterPowerOnHandled;
    private volatile int session = -1;
    /** Only one query (including the download) runs; the next minute is skipped. */
    private final AtomicBoolean queryRunning = new AtomicBoolean();
    private Runnable check;
    private Runnable waitEnd;
    /** Version from the server's offer (for the on-screen prompt). */
    private volatile String offeredVersion = "";
    /** System install dialog that opens only after OK on our prompt. */
    private Intent pendingConfirmation;
    private BroadcastReceiver stateReceiver;

    Updater(Context ctx, Handler handler, Host host) {
        this.ctx = ctx.getApplicationContext();
        this.handler = handler;
        this.host = host;
        this.processAfterPowerOn = SystemClock.elapsedRealtime() < UpdatePolicy.AFTER_POWER_ON_WINDOW_MS;
    }

    void start() {
        stateReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent it) {
                onInstallState(it);
            }
        };
        ctx.registerReceiver(stateReceiver, new IntentFilter(ACTION_INSTALL_STATE));
        check = new Runnable() {
            @Override
            public void run() {
                checkInBackground();
                handler.postDelayed(this, UpdatePolicy.CHECK_INTERVAL_MS);
            }
        };
        // the first query only after a while: after boot the network comes later than we do
        handler.postDelayed(check, UpdatePolicy.CHECK_INTERVAL_MS);
    }

    void stop() {
        if (check != null) {
            handler.removeCallbacks(check);
        }
        if (waitEnd != null) {
            handler.removeCallbacks(waitEnd);
        }
        if (stateReceiver != null) {
            try {
                ctx.unregisterReceiver(stateReceiver);
            } catch (Exception ignored) {
                // already unregistered
            }
        }
    }

    String state() {
        return state;
    }

    // --- query to the server ------------------------------------------------

    private void checkInBackground() {
        if (!queryRunning.compareAndSet(false, true)) {
            return;
        }
        final String url = host.currentUrl();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    checkServer(url);
                } catch (Exception e) {
                    // network or server: it is retried in a minute, nothing is reported
                    Log.i(TAG, "update: query failed: " + e.getClass().getSimpleName());
                } finally {
                    queryRunning.set(false);
                }
            }
        }, "kiosk-update").start();
    }

    private void checkServer(String url) throws Exception {
        int have = myVersion();
        final String reported = state;
        StringBuilder q = new StringBuilder(BaseUrl.URL).append(RELEASE_PATH).append("?have=")
                .append(have).append("&state=").append(reported).append(health());
        String name = UpdatePolicy.screen(url);
        if (name != null) {
            q.append("&device=").append(URLEncoder.encode(name, "UTF-8"));
        }
        HttpURLConnection c = open(q.toString());
        int code = c.getResponseCode();
        if (code != 200) {
            c.disconnect();
            return;                              // 404 = no version published
        }
        JSONObject r;
        try (InputStream in = c.getInputStream()) {
            r = new JSONObject(readAll(in, 64 * 1024));
        } finally {
            c.disconnect();
        }
        int offered = r.optInt("versionCode", 0);
        boolean requested = r.optBoolean("requested", false);
        // "after power-on" = within the window from boot, not from process start: a
        // version published an hour later must not raise the dialog just because the
        // process has been running since boot
        boolean afterPowerOn = processAfterPowerOn && !afterPowerOnHandled
                && SystemClock.elapsedRealtime() < UpdatePolicy.AFTER_POWER_ON_WINDOW_MS;
        afterPowerOnHandled = true;
        // The result (declined/failed/no_permission/installed) is reported once;
        // otherwise the box would keep sending it and cancel a new admin request at once.
        if (!inProgress && state.equals(reported) && !reported.equals(STATE_IDLE)) {
            state = STATE_IDLE;
        }
        if (!UpdatePolicy.shouldStart(have, offered, requested, afterPowerOn, inProgress)) {
            return;
        }
        if (Build.VERSION.SDK_INT >= 26
                && !ctx.getPackageManager().canRequestPackageInstalls()) {
            Log.w(TAG, "update: install not allowed (appops REQUEST_INSTALL_PACKAGES)");
            state = STATE_NO_PERMISSION;
            return;
        }
        inProgress = true;
        try {
            downloadAndInstall(r, offered, have);
        } catch (Exception e) {
            Log.w(TAG, "update failed", e);
            state = STATE_FAILED;
            inProgress = false;
        }
    }

    // --- download, verification, installation ---------------------------------

    private void downloadAndInstall(JSONObject r, int offered, int have) throws Exception {
        offeredVersion = r.optString("versionName", "");
        String path = r.optString("url", "");
        String sha = r.optString("sha256", "");
        long size = r.optLong("size", -1);
        if (!UpdatePolicy.isValidApkPath(path) || !UpdatePolicy.isValidSha256(sha)
                || size <= 0 || size > UpdatePolicy.MAX_SIZE) {
            throw new IllegalStateException("invalid version offer");
        }
        state = STATE_DOWNLOADING;
        Log.i(TAG, "update: downloading version " + offered + " (have " + have + ")");
        File file = new File(ctx.getCacheDir(), "update.apk");
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        HttpURLConnection c = open(BaseUrl.URL + path);
        long received = 0;
        try (InputStream in = c.getInputStream();
             OutputStream out = new FileOutputStream(file)) {
            if (c.getResponseCode() != 200) {
                throw new IllegalStateException("HTTP " + c.getResponseCode());
            }
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) {
                received += n;
                if (received > size) {
                    throw new IllegalStateException("APK larger than the server reports");
                }
                md.update(buf, 0, n);
                out.write(buf, 0, n);
            }
        } finally {
            c.disconnect();
        }
        if (received != size || !UpdatePolicy.hex(md.digest()).equals(sha)) {
            file.delete();
            throw new IllegalStateException("APK does not match the checksum");
        }
        PackageInfo pi = ctx.getPackageManager().getPackageArchiveInfo(file.getPath(), 0);
        if (pi == null || !ctx.getPackageName().equals(pi.packageName)
                || pi.versionCode <= have) {
            file.delete();
            throw new IllegalStateException("APK is not a newer kiosk version");
        }
        install(file);
    }

    private void install(File file) throws Exception {
        PackageInstaller pi = ctx.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams p =
                new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        p.setAppPackageName(ctx.getPackageName());
        int id = pi.createSession(p);
        try (PackageInstaller.Session s = pi.openSession(id)) {
            try (OutputStream out = s.openWrite("kiosk", 0, file.length());
                 InputStream in = new FileInputStream(file)) {
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
                s.fsync(out);
            }
            Intent st = new Intent(ACTION_INSTALL_STATE).setPackage(ctx.getPackageName());
            PendingIntent pend = PendingIntent.getBroadcast(ctx, id, st,
                    PendingIntent.FLAG_UPDATE_CURRENT);
            session = id;
            s.commit(pend.getIntentSender());
        }
        file.delete();
    }

    /** Result from the PackageInstaller (main thread). */
    private void onInstallState(Intent it) {
        int s = it.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (s == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirmation = it.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirmation == null) {
                finish(STATE_FAILED);
                return;
            }
            // First our own prompt in the kiosk (the system dialog is bare and
            // does not explain what is going on); the system dialog opens only
            // after OK (confirm()).
            state = STATE_WAITING;
            confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            pendingConfirmation = confirmation;
            host.returnToContent();
            host.showPrompt(offeredVersion, UpdatePolicy.CONFIRMATION_WAIT_MS);
            waitEnd = new Runnable() {
                @Override
                public void run() {
                    // nobody confirmed: back to the ad content, the next try only on a
                    // new admin request or after the next power-on
                    Log.i(TAG, "update: not confirmed, returning to the content");
                    abandonSession();
                    finish(STATE_DECLINED);
                    host.hidePrompt();
                    host.returnToContent();
                }
            };
            handler.postDelayed(waitEnd, UpdatePolicy.CONFIRMATION_WAIT_MS);
            return;
        }
        if (waitEnd != null) {
            handler.removeCallbacks(waitEnd);
        }
        host.hidePrompt();
        if (s == PackageInstaller.STATUS_SUCCESS) {
            finish(STATE_INSTALLED);            // the process ends shortly anyway
        } else if (s == PackageInstaller.STATUS_FAILURE_ABORTED) {
            finish(STATE_DECLINED);             // someone pressed Cancel
            host.returnToContent();
        } else {
            Log.w(TAG, "update: install failed, status " + s + ": "
                    + it.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE));
            finish(STATE_FAILED);
            host.returnToContent();
        }
    }

    /** Is the prompt waiting for OK? (keys in MainActivity) */
    boolean awaitingConfirmation() {
        return pendingConfirmation != null;
    }

    /** OK on the prompt: open the system install dialog (main thread). */
    void confirm() {
        Intent confirmation = pendingConfirmation;
        if (confirmation == null) {
            return;
        }
        pendingConfirmation = null;
        host.hidePrompt();
        // the watchdog must not override the dialog; the wait limit (waitEnd) keeps running
        host.pauseWatchdog(UpdatePolicy.CONFIRMATION_WAIT_MS);
        try {
            ctx.startActivity(confirmation);
        } catch (Exception e) {
            Log.w(TAG, "update: the dialog could not be opened", e);
            if (waitEnd != null) {
                handler.removeCallbacks(waitEnd);
            }
            abandonSession();
            finish(STATE_FAILED);
        }
    }

    /** Back on the prompt: not now. The next try on a new request or after power-on. */
    void decline() {
        if (pendingConfirmation == null) {
            return;
        }
        if (waitEnd != null) {
            handler.removeCallbacks(waitEnd);
        }
        host.hidePrompt();
        abandonSession();
        finish(STATE_DECLINED);
    }

    private void finish(String newState) {
        state = newState;
        pendingConfirmation = null;
        inProgress = false;
        session = -1;
    }

    private void abandonSession() {
        if (session < 0) {
            return;
        }
        try {
            ctx.getPackageManager().getPackageInstaller().abandonSession(session);
        } catch (Exception ignored) {
            // the session may have ended already
        }
    }

    // --- box health ------------------------------------------------------------

    /**
     * Query part with the box health (query thread). Whatever cannot be found out
     * is omitted; an error here must never break the update query.
     */
    private String health() {
        int ram = -1;
        int ramTotal = -1;
        boolean ramLow = false;
        String net = null;
        int rssi = Health.INVALID_RSSI;
        try {
            ActivityManager am = (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
            if (am != null) {
                ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
                am.getMemoryInfo(mi);
                ram = Health.toMb(mi.availMem);
                ramTotal = Health.toMb(mi.totalMem);
                ramLow = mi.lowMemory;
            }
        } catch (Exception e) {
            Log.i(TAG, "health: RAM unavailable: " + e.getClass().getSimpleName());
        }
        try {
            ConnectivityManager cm =
                    (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkInfo ni = cm != null ? cm.getActiveNetworkInfo() : null;
            if (ni != null && ni.isConnected()) {
                boolean wifi = ni.getType() == ConnectivityManager.TYPE_WIFI;
                net = Health.networkType(wifi, ni.getType() == ConnectivityManager.TYPE_ETHERNET);
                if (wifi) {
                    // signal strength only; the SSID/BSSID is neither read nor sent
                    WifiManager wm = (WifiManager) ctx.getSystemService(Context.WIFI_SERVICE);
                    WifiInfo wi = wm != null ? wm.getConnectionInfo() : null;
                    if (wi != null) {
                        rssi = wi.getRssi();
                    }
                }
            }
        } catch (Exception e) {
            Log.i(TAG, "health: network unavailable: " + e.getClass().getSimpleName());
        }
        return Health.queryParams(ram, ramLow, ramTotal,
                SystemClock.elapsedRealtime() / 1000, net, rssi, host.crashCount());
    }

    // --- helpers ----------------------------------------------------------------

    private int myVersion() {
        try {
            return ctx.getPackageManager().getPackageInfo(ctx.getPackageName(), 0).versionCode;
        } catch (PackageManager.NameNotFoundException e) {
            return 0;
        }
    }

    private static HttpURLConnection open(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(30000);
        c.setInstanceFollowRedirects(false);    // only our host, no redirects elsewhere
        c.setUseCaches(false);
        return c;
    }

    private static String readAll(InputStream in, int cap) throws Exception {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) {
            if (b.size() + n > cap) {
                throw new IllegalStateException("response too large");
            }
            b.write(buf, 0, n);
        }
        return b.toString("UTF-8");
    }
}
