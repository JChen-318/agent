package com.androidagent.util

import android.util.Log
import com.androidagent.App

object Logger {
    fun d(tag: String, msg: String) = Log.d("${App.TAG}:$tag", msg)
    fun i(tag: String, msg: String) = Log.i("${App.TAG}:$tag", msg)
    fun w(tag: String, msg: String) = Log.w("${App.TAG}:$tag", msg)
    fun e(tag: String, msg: String, t: Throwable? = null) = Log.e("${App.TAG}:$tag", msg, t)
}
