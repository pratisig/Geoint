package com.example.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "incidents")
data class IncidentEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val title: String,
    val link: String,
    val source: String,
    val sourceType: String = "PRESSE",
    val category: String,
    val region: String = "Global",
    val country: String = "International",
    val latitude: Double,
    val longitude: Double,
    val publishedAt: String,
    val summary: String = "",
    val severity: String = "medium",
    val actors: String = "[]",
    val needs: String = "[]",
    val riskLevel: Int = 2,
    val language: String = "fr",
    val verified: Boolean = false,
    val createdAt: Long = System.currentTimeMillis()
)
