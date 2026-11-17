package com.mumin.aitrader

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Compatibility redirect for legacy launcher references.
 * The unified remote operations center is the only dashboard implementation.
 */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(Intent(this, UnifiedDashboardActivity::class.java))
        finish()
    }
}
