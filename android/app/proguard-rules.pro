# MediaCodec — async callback classes get stripped by R8 if not kept explicitly
-keep class android.media.MediaCodec { *; }
-keepclassmembers class * extends android.media.MediaCodec$Callback { *; }
-keep class * implements android.media.MediaCodec$Callback { *; }

# Your app code — StreamDecoder handles NAL parsing / SPS/PPS detection,
# keep it intact rather than risk R8 stripping reflective access
-keep class com.tethrlink.StreamDecoder { *; }
-keep class com.tethrlink.** { *; }
-dontwarn com.tethrlink.**

# Jetpack Compose
-keep class androidx.compose.runtime.** { *; }
-keepclassmembers class androidx.compose.** { *; }
-dontwarn androidx.compose.**

# Coroutines
-keepclassmembers class kotlinx.coroutines.** { *; }
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
-dontwarn kotlinx.coroutines.**

# Kotlin metadata, needed if anything uses reflection (serialization, etc)
-keepattributes *Annotation*, InnerClasses, Signature, Exceptions
-keep class kotlin.Metadata { *; }

# AndroidManifest-declared activities/services are kept automatically by R8,
# no explicit rule needed for MainActivityV2 / SettingsActivity