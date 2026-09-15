package com.example.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface GeozoneDao {
    @Query("SELECT * FROM geozones ORDER BY id DESC")
    suspend fun getAll(): List<GeozoneEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(geozone: GeozoneEntity): Long

    @Query("DELETE FROM geozones WHERE id = :id")
    suspend fun deleteById(id: Long): Int

    @Query("DELETE FROM geozones")
    suspend fun clearAll()
}
