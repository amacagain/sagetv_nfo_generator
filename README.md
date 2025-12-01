## Description
Exports SageTV metadata to a set of folders organized for compatibility with Jellyfin/Kodi.
Configuration file provided and must be edited for your Sage server.
An example set of instructions for how to add a SageTV library to Jellyfin will be provided as a courtesy but configuring Jellyfin/Kodi is outside the responsibility of this project.

Once this program has run and Jellyfin configured, SageTV movies and shows will be available to watch on any Jellyfin supported device. The Jellyfin server need only create two media libraries for Sage Movies and TV Shows. Of course those Jellyfin clients will have to be configured to point to the Jellyfin server but that is outside the scope of this project.


## 🎯 Features

* **SageTV Integration:** Pulls detailed metadata (Title, Season/Episode, Description, Year) directly from the SageTV API.

* **NFO Generation:** Creates Kodi-compliant `.nfo` files for robust metadata importing. Two principal sub-folders for Movies and TV shows reside under a ROOT folder. Subsequent sub-folders for TV shows and seasons as supported by many media applications.
* **No impact on file/disc organization** No media files have to be moved or relocated.
* **Symbolic Link Creation:** NFO symbolic links to the original media eliminates relocating media files. Unfortunately, creating symbolic links on Windows requires this program runs with admin privileges.
* **Change Detection:** Skips files that have already been processed and whose modification time (`mtime`) has not changed since the last run, ensuring fast execution.

* **Cleanup:** Automatically removes orphaned symlinks and NFOs if the original SageTV file is deleted or no longer found.

* **Sample Jellyfin library metadata update trigger: ** The core generation logic is separate from media server trigger calls. This means if you want to schedule imports into your media playback server, it should be implemented in a separate program. I will provide an example but my understanding is that Jellyfin can automatically detect new, changed, or deleted files already.

## 🛠️ Prerequisites

1.  **Python 3.x:** Installed and accessible via your system's PATH.

2.  **Python `requests` library:** Used for API communication with both SageTV and Jellyfin.

### Installing Libraries

Open your terminal or command prompt and run:

pip install requests


## ⚙️ Configuration

The project uses a configuration file to maintain the separation between media generation and media playback server updates.

### Core Configuration (`config.json`)

This file configures the connection to your SageTV server and defines the output structure.

| Field | Description | Required | Notes |
| :--- | :--- | :--- | :--- |
| `SAGE_HOST` | The IP address or hostname of your SageTV server. | Yes | |
| `SAGE_PORT` | The port SageTV is running on (usually `8080`). | Yes | |
| `SAGE_USER` / `SAGE_PASS` | Username and password for basic authentication. | Yes | |
| `ROOT_PATH` | **The directory where the symlinks and NFOs will be created.** | Yes | This folder must be configured as a media library source in your media server (Jellyfin/Plex/Kodi). |
| `FLAT_MOVIE_STRUCTURE` | If `true`, movies are placed directly in the `Movies` folder. If `false`, they get their own subfolder. | No | Default: `false` |
| `VERBOSITY_LEVEL` | Logging level (0=Critical, 1=Info, 2=Debug/File Logging). | No | Default: `1` |
| 'MAX_FILES_TO_PROCESS' | 0 for all files (default). Set a positive number to limit the processing count for testing purposes | Yes | Default: 0 | 

## 🚀 Running the Program

### **⚠️ CRITICAL: Admin Privileges are Required on Windows**

The core script, `sagetv_nfo_generator.py`, relies on the Python `os.symlink()` function to create **symbolic links**.

* On **Windows**, creating symbolic links typically requires **Administrator privileges** by default.

* If you do not run the script with elevation (Run as Administrator), you will encounter a `PermissionError` when the script attempts to create links.

**To run the program successfully on Windows, you must execute the script from an elevated Command Prompt or PowerShell window.**

### Execution Steps

The recommended way to run the process is outlined below to ensure all files are created.

| Step | Script | Description |
| :--- | :--- | :--- |
| **1. Generate Files** | `python sagetv_nfo_generator.py` | This script connects to SageTV, checks for new/updated files, creates the necessary folders, NFOs, and symbolic links, and performs cleanup. |

**Example Execution (from the script directory):**

Step 1: File Generation and Cleanup

python sagetv_nfo_generator.py

This step is really all you need. It will create a processing file processed_files.json that tracks what has previously been processed. This enables deletion detection.

**FAQ**
This will be added in due time.

## 💻 Compatibility

| Operating System | Status | Notes |
| :--- | :--- | :--- |
| **Windows** | **Tested** | Requires running the script as **Administrator** for symbolic link creation. |
| **Linux/macOS** | Likely Compatible but not tested | Python and symbolic links should work out-of-the-box, no root/admin access should be required unless the target directory has restricted permissions. |

### Experimental Jellyfin Trigger
If using Jellyfin, this separate script sends an API command to your server, telling it to immediately scan its libraries for the newly created files.
Configuration (`jellyfin_config.json`)

| Field | Description | Required | Notes |
| :--- | :--- | :--- | :--- |
| `JELLYFIN_HOST` / `JELLYFIN_PORT` | Address and port of your Jellyfin server. | Yes | |
| `JELLYFIN_API_KEY` | **Crucial:** An API key generated within your Jellyfin Dashboard (under API Keys) with **Library Access** permissions. | Yes | **Never share this key publicly.** |

**Usage: Tell Jellyfin to scan**

python jellyfin_trigger.py

## NFO/Symbolic Link Cleanup and Conflict
This utility includes advanced stability features to handle common issues in SageTV environments:

#### Stale Path Resolution:
If your files are transcoded (e.g., SageTV reports .mpg but the actual file is .mkv), the utility intelligently searches for the correct file on disk and links to the available format, ensuring your links never point to missing files.
#### State-Based Collision Resolution:
Prevents output filename clashes. If two distinct SageTV recordings (different IDs) map to the same target filename (e.g., two recordings of the same show episode), the utility appends the unique SageTV MediaFileID to the filename (e.g., Show - S01E01 - 12345.mkv).
#### Crucially:
This resolution is persistent, meaning the script remembers the unique name in the sagex_state.json file and will not re-log the collision on every subsequent run.
#### Stale File Cleanup:
Automatically removes symlinks and NFOs if the underlying media file they point to is deleted from your disk (e.g., after cleanup by SageTV).
Media Center Compatibility: Generates standard tvshow.nfo and episode/movie NFO files with extracted metadata (Title, Plot, Season/Episode numbers, etc.), suitable for use with Kodi, Jellyfin, and likely Emby.

## Important Notes on State and Cleanup
#### sagex_state.json:
This file is automatically created and maintained by the script. Do not modify this file manually. It is essential for the cleanup process and, more importantly, for remembering collision-resolved filenames. Deleting it will force the script to treat all files as new and re-run collision checks where they were previously resolved.
#### Symlink Permissions:
If you encounter OSError: symbolic link privilege not held, ensure the user running the script has the necessary permissions to create symlinks on your operating system.



