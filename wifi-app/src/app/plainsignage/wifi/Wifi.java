package app.plainsignage.wifi;

import android.app.Activity;
import android.content.Context;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.util.Log;

import java.util.List;

/**
 * No UI: does one thing according to the "action" extra and finishes. The result
 * goes to the log (tag SignageWifi), the password is never printed.
 *
 *   am start -n app.plainsignage.wifi/.Wifi --es action add --es ssid "Net" --es password "…"
 *       optionally --ez hidden true; without a password = open network
 *   am start -n app.plainsignage.wifi/.Wifi --es action list
 *   am start -n app.plainsignage.wifi/.Wifi --es action remove --es ssid "Net"
 *   logcat -d -s SignageWifi
 */
public class Wifi extends Activity {

    private static final String TAG = "SignageWifi";

    /** Names of the intent extras and action values. */
    private static final String EXTRA_ACTION = "action";
    private static final String EXTRA_SSID = "ssid";
    private static final String EXTRA_PASS = "password";
    private static final String EXTRA_HIDDEN = "hidden";
    private static final String ACTION_ADD = "add";
    private static final String ACTION_LIST = "list";
    private static final String ACTION_REMOVE = "remove";

    @Override
    protected void onCreate(Bundle savedState) {
        super.onCreate(savedState);
        try {
            perform();
        } catch (Exception e) {
            Log.e(TAG, "ERROR: " + e);
        }
        finish();
    }

    private void perform() {
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wm == null) {
            Log.e(TAG, "ERROR: no WifiManager");
            return;
        }
        String action = getIntent().getStringExtra(EXTRA_ACTION);
        String ssid = getIntent().getStringExtra(EXTRA_SSID);
        if (ACTION_LIST.equals(action)) {
            List<WifiConfiguration> networks = wm.getConfiguredNetworks();
            if (networks == null) {
                Log.e(TAG, "ERROR: list unavailable (location missing? pm grant app.plainsignage.wifi "
                        + "android.permission.ACCESS_FINE_LOCATION)");
                return;
            }
            Log.i(TAG, "SAVED: " + networks.size());
            for (WifiConfiguration c : networks) {
                Log.i(TAG, "  id=" + c.networkId + " ssid=" + c.SSID
                        + (c.hiddenSSID ? " (hidden)" : ""));
            }
            return;
        }
        if (ssid == null || ssid.isEmpty() || ssid.length() > 32) {
            Log.e(TAG, "ERROR: ssid missing or invalid");
            return;
        }
        String quoted = "\"" + ssid + "\"";
        WifiConfiguration existing = find(wm, quoted);
        if (ACTION_REMOVE.equals(action)) {
            if (existing == null) {
                Log.i(TAG, "OK: " + ssid + " is not saved");
                return;
            }
            boolean ok = wm.removeNetwork(existing.networkId);
            wm.saveConfiguration();
            Log.i(TAG, (ok ? "OK: removed " : "ERROR: cannot remove (added by another app?) ") + ssid);
            return;
        }
        if (!ACTION_ADD.equals(action)) {
            Log.e(TAG, "ERROR: unknown action (add|list|remove)");
            return;
        }
        String password = getIntent().getStringExtra(EXTRA_PASS);
        if (password != null && !password.isEmpty() && (password.length() < 8 || password.length() > 63)) {
            Log.e(TAG, "ERROR: a WPA password must have 8–63 characters");
            return;
        }
        WifiConfiguration c = new WifiConfiguration();
        c.SSID = quoted;
        c.hiddenSSID = getIntent().getBooleanExtra(EXTRA_HIDDEN, false);
        if (password == null || password.isEmpty()) {
            c.allowedKeyManagement.set(WifiConfiguration.KeyMgmt.NONE);
        } else {
            c.allowedKeyManagement.set(WifiConfiguration.KeyMgmt.WPA_PSK);
            c.preSharedKey = "\"" + password + "\"";
        }
        int id;
        if (existing != null) {
            c.networkId = existing.networkId;
            id = wm.updateNetwork(c);
            if (id < 0) {
                // the network was saved by someone else (manually in Settings) — we must
                // not overwrite it, but it is saved anyway, which is enough
                Log.i(TAG, "OK: " + ssid + " is already saved (manually), keeping it");
                return;
            }
        } else {
            id = wm.addNetwork(c);
        }
        if (id < 0) {
            Log.e(TAG, "ERROR: Android refused to save the network: " + ssid);
            return;
        }
        // enableNetwork(id, false) = enable, but do not switch away from the current network
        wm.enableNetwork(id, false);
        wm.saveConfiguration();
        Log.i(TAG, "OK: " + (existing != null ? "updated " : "added ") + ssid
                + " (id " + id + ")");
    }

    private static WifiConfiguration find(WifiManager wm, String quoted) {
        List<WifiConfiguration> networks = wm.getConfiguredNetworks();
        if (networks == null) {
            return null;
        }
        for (WifiConfiguration c : networks) {
            if (quoted.equals(c.SSID)) {
                return c;
            }
        }
        return null;
    }
}
