package com.mumin.aitrader

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Compatibility redirect for older explicit intents.
 * The embedded dashboard was retired; all app surfaces use UnifiedDashboardActivity.
 */
class LiveDashboardActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(Intent(this, UnifiedDashboardActivity::class.java))
        finish()
    }
}
