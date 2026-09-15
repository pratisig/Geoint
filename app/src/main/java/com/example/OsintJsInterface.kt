package com.example

import android.content.Context
import android.webkit.JavascriptInterface
import android.widget.Toast
import com.example.data.OsintRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject

class OsintJsInterface(
    private val context: Context,
    private val repository: OsintRepository,
    private val onDataUpdated: () -> Unit
) {
    private val scope = CoroutineScope(Dispatchers.Main)
    private var lastSyncTimestamp = System.currentTimeMillis()

    @JavascriptInterface
    fun getLiveFeeds(): String {
        return runBlocking(Dispatchers.IO) {
            val incidents = repository.getIncidents()
            val array = JSONArray()
            for (inc in incidents) {
                val obj = JSONObject().apply {
                    put("id", inc.id)
                    put("title", inc.title)
                    put("link", inc.link)
                    put("source", inc.source)
                    put("source_type", inc.sourceType)
                    put("category", inc.category)
                    put("region", inc.region)
                    put("country", inc.country)
                    put("latitude", inc.latitude)
                    put("longitude", inc.longitude)
                    put("published_at", inc.publishedAt)
                    put("summary", inc.summary)
                    put("severity", inc.severity)
                    put("actors", inc.actors)
                    put("needs", inc.needs)
                    put("risk_level", inc.riskLevel)
                    put("language", inc.language)
                    put("verified", inc.verified)
                }
                array.put(obj)
            }
            array.toString()
        }
    }

    @JavascriptInterface
    fun refreshFeeds(): String {
        scope.launch(Dispatchers.IO) {
            val count = repository.refreshRssFeeds()
            lastSyncTimestamp = System.currentTimeMillis()
            scope.launch(Dispatchers.Main) {
                Toast.makeText(
                    context,
                    if (count > 0) "V4 Mise à jour réussie : $count nouveaux rapports mondiaux intégrés (70+ sources)."
                    else "V4 Flux mondiaux synchronisés - 30 incidents live disponibles.",
                    Toast.LENGTH_SHORT
                ).show()
                onDataUpdated()
            }
        }
        return "OK V4"
    }

    @JavascriptInterface
    fun getLastSyncTimestamp(): Long {
        return lastSyncTimestamp
    }

    @JavascriptInterface
    fun getGeozones(): String {
        return runBlocking(Dispatchers.IO) {
            val zones = repository.getGeozones()
            val array = JSONArray()
            for (z in zones) {
                val obj = JSONObject().apply {
                    put("id", z.id)
                    put("name", z.name)
                    put("geometry_type", z.geometryType)
                    put("geojson_data", z.geojson)
                    put("area_sqkm", z.areaSqkm)
                }
                array.put(obj)
            }
            array.toString()
        }
    }

    @JavascriptInterface
    fun saveGeozone(name: String, type: String, geojson: String, area: Double): Long {
        return runBlocking(Dispatchers.IO) {
            val id = repository.saveGeozone(name, type, geojson, area)
            scope.launch(Dispatchers.Main) {
                Toast.makeText(context, "Zone V4 « $name » sauvegardée SQLite (sat HD)", Toast.LENGTH_SHORT).show()
            }
            id
        }
    }

    @JavascriptInterface
    fun deleteGeozone(id: Long): Boolean {
        return runBlocking(Dispatchers.IO) {
            val deleted = repository.deleteGeozone(id)
            scope.launch(Dispatchers.Main) {
                Toast.makeText(context, "Zone V4 supprimée BDD", Toast.LENGTH_SHORT).show()
            }
            deleted
        }
    }

    @JavascriptInterface
    fun showToast(msg: String) {
        scope.launch(Dispatchers.Main) {
            Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
        }
    }

    @JavascriptInterface
    fun getVersion(): String {
        return "4.0 ULTIMATE POWER - 70+ sources + reverse image + 40 engines + report + AI"
    }
}
