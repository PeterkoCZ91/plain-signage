package app.plainsignage.kiosk;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * After the kiosk is updated the system kills the old process. This receiver
 * starts it again right away, so the screen does not stay on the system dialog
 * or in the background. (A start from the background on Android 10 works thanks
 * to the granted SYSTEM_ALERT_WINDOW; otherwise the kiosk returns to HOME on
 * the next press of Home or on restart.)
 */
public class UpdateReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent it) {
        if (!Intent.ACTION_MY_PACKAGE_REPLACED.equals(it.getAction())) {
            return;
        }
        try {
            Intent start = new Intent(ctx, MainActivity.class);
            start.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(start);
        } catch (Exception e) {
            Log.w(MainActivity.TAG, "start after update failed", e);
        }
    }
}
