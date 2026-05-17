package com.androidagent

import android.app.Application
import android.util.Log

class App : Application() {
    companion object {
        const val TAG = "AndroidAgent"
        lateinit var instance: App
            private set
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        Log.i(TAG, "Android Agent application started")
    }
}
