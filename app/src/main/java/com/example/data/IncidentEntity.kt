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
    val sourceType: String = "PRESSE", // "OFFICIEL", "OSINT_DEPÊCHE", "RENSEIGNEMENT", "CYBER_FUITE", "PRESSE", "ALERTE_CATASTROPHE"
    val category: String, // "conflit", "cyber", "energie", "epidemie", "protest", "catastrophe"
    val region: String = "Global", // "Moyen-Orient", "Europe", "Afrique", "Asie-Pacifique", "Amériques", "Global"
    val country: String = "International", // e.g. "Ukraine", "Israël/Gaza", "Iran", "Soudan", etc.
    val latitude: Double,
    val longitude: Double,
    val publishedAt: String,
    val summary: String = "",
    val createdAt: Long = System.currentTimeMillis()
)
