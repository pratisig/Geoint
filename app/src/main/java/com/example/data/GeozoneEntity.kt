package com.example.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "geozones")
data class GeozoneEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val name: String,
    val geometryType: String,
    val geojson: String,
    val areaSqkm: Double,
    val createdAt: Long = System.currentTimeMillis()
)
