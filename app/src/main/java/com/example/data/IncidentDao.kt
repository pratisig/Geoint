package com.example.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface IncidentDao {
    @Query("SELECT * FROM incidents ORDER BY id DESC")
    suspend fun getAll(): List<IncidentEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(incidents: List<IncidentEntity>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(incident: IncidentEntity): Long

    @Query("DELETE FROM incidents")
    suspend fun clearAll()

    @Query("SELECT COUNT(*) FROM incidents")
    suspend fun getCount(): Int
}
