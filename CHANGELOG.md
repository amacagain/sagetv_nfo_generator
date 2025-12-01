Changelog
All notable changes to this project will be documented in this file.
[1.0.1] - TBD
Fixed
Critical Collision Cleanup Bug: Resolved an issue where existing symbolic links and NFO files (created before a collision was detected) were not properly cleaned up when the script transitioned to creating a new, ID-embedded filename. This left orphaned, non-ID-embedded symlinks and NFOs on the disk, requiring manual deletion. The script will now correctly check for and remove the pre-existing target files during the collision resolution phase.

[1.0.0] - 2025-11-30
Initial stable release of the SageTV NFO Generator Utility, featuring critical stability improvements for real-world SageTV media libraries.

Added
State-Based Collision Resolution:
The utility now uses sagex_state.json to persistently store the final, unique output filename for every media file ID.
This eliminates the issue of repeatedly detecting and logging collisions on subsequent runs once a file is processed and named.
Conflicting filenames are automatically resolved by appending the unique SageTV MediaFileID (e.g., ... - 12345.mkv).

Stale Path Resolution:
Implemented logic to automatically check for alternative media file extensions (.mkv, .mp4, etc.) when the SageTV-reported file path (.mpg) is missing. This solves issues related to post-recording transcoding.

Stale File Cleanup:
A pre-processing cleanup step now automatically removes any existing symlinks and their associated NFO files if the actual media file they point to has been deleted from the disk.

Media Center Compatibility:
Explicitly confirmed and compatibility with Kodi, Jellyfin. Emby probably can use this structure too.
Configuration (config.json): Added the MAX_FILES_TO_PROCESS setting for testing and limiting large library scans.

Changed
Logging: Updated logging structure to use Python's built-in RotatingFileHandler for cleaner log management.

Dependency: Updated dependency handling to clearly require the requests library.

Fixed
Resolved the critical bug where collisions were constantly re-logged and resolved on every run, even after symlinks were already created.
