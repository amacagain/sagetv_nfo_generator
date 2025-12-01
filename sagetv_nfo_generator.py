import requests
import os
import xml.etree.ElementTree as ET
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional, Set, Any, Tuple
import json
import re
import time
import shutil
import sys 

# -----------------------------------------------------------------------------
# Configuration File Constant
# -----------------------------------------------------------------------------
CONFIG_FILE_NAME = "config.json"
# -----------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    """Loads configuration settings from the external JSON file."""
    config_file_path = Path(__file__).resolve().parent / CONFIG_FILE_NAME
    try:
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            logging.info(f"Configuration loaded from {CONFIG_FILE_NAME}.")
            return config
    except FileNotFoundError:
        print(f"FATAL ERROR: Configuration file '{CONFIG_FILE_NAME}' not found. Cannot proceed.")
        raise
    except json.JSONDecodeError as e:
        print(f"FATAL ERROR: Failed to parse configuration file. Check JSON formatting: {e}")
        raise

def setup_logging(config: Dict[str, Any]):
    """Configures logging for the utility."""
    level = config.get('VERBOSITY_LEVEL', 1)
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    root_logger = logging.getLogger()
    
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Set logging level based on config (2 or higher enables DEBUG)
    if level == 0:
        root_logger.setLevel(logging.CRITICAL)
    elif level == 1:
        root_logger.setLevel(logging.INFO)
    elif level >= 2:
        root_logger.setLevel(logging.DEBUG)
        
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    if level >= 2:
        # File Handler
        log_file_name = config.get('LOG_FILE_NAME', 'nfo_generator.log')
        max_size_mb = config.get('MAX_LOG_SIZE_MB', 1)
        max_count = config.get('MAX_LOG_COUNT', 5)
        
        log_file_path = Path(__file__).resolve().parent / log_file_name
        file_handler = RotatingFileHandler(
            log_file_path, 
            maxBytes=max_size_mb * 1024 * 1024, 
            backupCount=max_count
        )
        file_handler.setFormatter(log_formatter)
        root_logger.addHandler(file_handler)

# ---------------------

class NFOGeneratorUtility:
    """
    Generates symbolic links and NFO files for SageX media files, including 
    handling for stale paths caused by transcoding and resolving output filename collisions.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # SageTV Configuration
        self.auth = (config['SAGE_USER'], config['SAGE_PASS'])
        self.host = config['SAGE_HOST']
        self.port = config['SAGE_PORT']
        self.page_size = config.get('PAGE_SIZE', 100)
        
        # New: Limit the number of files to process
        self.max_files_to_process = config.get('MAX_FILES_TO_PROCESS', 0) # 0 means no limit
        
        # File/Folder Configuration
        self.root_path = Path(config['ROOT_PATH'])
        self.flat_movie_structure = config.get('FLAT_MOVIE_STRUCTURE', False)
        self.tv_shows_root = self.root_path / "TV Shows"
        self.movies_root = self.root_path / "Movies"
        
        # State Management
        self.state_file = Path(__file__).resolve().parent / "sagex_state.json"
        # current_state is populated during run and saved at the end
        self.current_state: Dict[str, Any] = {}
        # previous_state is loaded at init and used for cleanup and collision recall
        self.previous_state: Dict[str, Any] = self._load_state() 
        self.processed_tv_shows: Set[str] = set()

        self._ensure_root_directories()
        
    def _load_state(self) -> Dict[str, Any]:
        """Loads the previous state from the JSON file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    logging.info("Loaded previous state for cleanup and collision recall.")
                    return json.load(f)
            except json.JSONDecodeError:
                logging.warning("sagex_state.json corrupted, starting with fresh state.")
        return {}

    def _save_state(self):
        """Saves the current state to the JSON file."""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.current_state, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save state file: {e}")

    def _ensure_root_directories(self):
        """Creates the main root and sub-directories."""
        logging.debug(f"Ensuring root directories exist: {self.root_path}")
        try:
            self.tv_shows_root.mkdir(parents=True, exist_ok=True)
            self.movies_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logging.error(f"Failed to create directories: {e}")
            raise

    def _clean_directory_name(self, name: str) -> str:
        """Removes illegal characters and cleans up a directory/file name."""
        illegal_chars = set(['<', '>', ':', '"', '/', '\\', '|', '?', '*'])
        cleaned_name = ''.join(['-' if c in illegal_chars else c for c in name])
        cleaned_name = cleaned_name.strip()
        while cleaned_name and (cleaned_name.endswith('.') or cleaned_name.endswith(' ')):
            cleaned_name = cleaned_name.rstrip('. ').strip()
        return cleaned_name if cleaned_name else "UnknownMedia"
    
    def _parse_sxxeyy(self, filename: str) -> Optional[Tuple[int, int]]:
        """Parses the SXXEYY pattern from a filename for the Fallback step."""
        match = re.search(r'[sS][\.\-]?(\d+)\s*[eE][\.\-]?(\d+)', filename)
        if match:
            try:
                return int(match.group(1)), int(match.group(2))
            except ValueError:
                return None
        return None
        
    def _get_media_files_page(self, start: int) -> Optional[ET.Element]:
        """Calls the SageX API for a specific page of media files."""
        url = f"http://{self.host}:{self.port}/sagex/api"
        params = {
            "command": "GetMediaFiles",
            "format": "xml",
            "size": self.page_size,
            "start": start
        }
        
        try:
            response = requests.get(url, params=params, auth=self.auth, timeout=30)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            return root
        except requests.exceptions.RequestException as e:
            logging.error(f"API Request failed at start={start}. Check SAGE_HOST, SAGE_PORT, SAGE_USER/PASS. Error: {e}")
            return None
        except ET.ParseError as e:
            logging.error(f"Failed to parse XML response at start={start}: {e}")
            return None
            
    def _extract_data(self, element: ET.Element) -> Dict[str, str]:
        """Extracts essential metadata from the XML element."""
        def get_text(xpath: str, default: str = '') -> str:
            val = element.findtext(xpath)
            return val.strip() if val else default

        is_movie_str = get_text('./Airing/Show/IsMovie', 'false').lower()
        title = get_text('./Airing/Show/ShowTitle') or get_text('./MediaTitle')

        metadata = {
            'MediaFileID': get_text('./MediaFileID'),
            'IsMovie': is_movie_str == 'true',
            'Title': title,
            'Year': get_text('./Airing/Show/ShowYear'),
            'Description': get_text('./Airing/Show/ShowDescription') or get_text('./MediaFileMetadataProperties/Description'),
            'RuntimeMs': get_text('./FileDuration'),
            'EpisodeName': get_text('./Airing/Show/ShowEpisode'),
            'EpisodeNumber': get_text('./Airing/Show/ShowEpisodeNumber'),
            'SeasonNumber': get_text('./Airing/Show/ShowSeasonNumber'),
            'Rated': get_text('./Airing/Show/ShowRated'),
            'Genre': get_text('./MediaFileMetadataProperties/Genre'),
            'Writers': get_text('./MediaFileMetadataProperties/Writer'),
            'Directors': get_text('./MediaFileMetadataProperties/Director'),
            'FilePath': get_text('./SegmentFiles/File'),
        }
        return metadata

    def _resolve_actual_file_path(self, original_path: str) -> Optional[Path]:
        """
        Attempts to find the media file on disk, checking common alternative
        extensions if the original path from SageTV (e.g., .mpg) is stale.
        """
        original_file_path = Path(original_path)
        
        # 1. Check the original path first
        if original_file_path.is_file():
            return original_file_path
            
        if not original_file_path.is_absolute() or not original_file_path.stem:
            return None

        # Check common alternative extensions in the same directory
        common_extensions = ['.mkv', '.mp4', '.avi', '.ts', '.mpg'] 
        file_dir = original_file_path.parent
        file_base_name = original_file_path.stem
        
        # 2. Check alternatives
        for ext in common_extensions:
            alternative_path = file_dir / (file_base_name + ext)
            if alternative_path.is_file():
                logging.info(f"Resolved stale path: Found '{alternative_path.name}' instead of missing '{original_file_path.name}'.")
                return alternative_path
        
        # 3. Final failure
        logging.debug(f"File not found: {original_path} (and no alternatives found)")
        return None

    def _get_comparable_path(self, path_str: str) -> str:
        """
        Normalizes the path, resolves it to its canonical form, and converts 
        to lowercase on Windows for robust, precise comparison.
        """
        if not path_str:
            return ""

        path_obj = Path(path_str)
        
        try:
            # 1. Resolve path to canonical form, handle \\?\ and forward slashes
            path_normalized_str = path_obj.resolve().as_posix()
            
            # Strip the extended path prefix if present (Windows only prefix)
            if path_normalized_str.startswith('//?/'):
                path_normalized_str = path_normalized_str[4:]
        except:
            # Fallback if resolve() fails (e.g., target file is missing)
            path_normalized_str = path_str 

        # 2. Apply case conversion ONLY IF on Windows
        if sys.platform == 'win32' or sys.platform.startswith('cygwin') or sys.platform.startswith('msys'):
            return path_normalized_str.lower()
            
        return path_normalized_str
    
    def _get_resolved_filename_base(self, media_file_id: str) -> Optional[str]:
        """
        Checks the previous state for a pre-calculated, collision-resolved filename base.
        """
        entry = self.previous_state.get(media_file_id)
        if entry and 'resolved_filename_base' in entry:
            return entry['resolved_filename_base']
        return None


    def _create_media_files(self, data: Dict[str, str], resolved_path: Path, target_dir: Path, filename_base: str):
        """Creates the symlink and NFO files."""
        
        media_file_id = data['MediaFileID']
        file_extension = resolved_path.suffix
        
        # The symlink file name should use the extension of the *resolved* file
        symlink_path = target_dir / f"{filename_base}{file_extension}"
        # The NFO file name should match the symlink file name stem
        nfo_path = target_dir / f"{filename_base}.nfo"
        
        # 1. Ensure directory exists
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # 2. Handle Symlink (Creation or Replacement)
        if symlink_path.exists():
            if symlink_path.is_symlink():
                current_target_str = os.readlink(symlink_path)
                
                # Use precise path comparison
                current_target_comparable = self._get_comparable_path(current_target_str)
                resolved_path_comparable = self._get_comparable_path(str(resolved_path))

                # Check if the existing link points to a different physical file.
                if current_target_comparable != resolved_path_comparable:
                    
                    # This replacement is necessary for the original stale path fix (file moved/transcoded).
                    os.remove(symlink_path)
                    logging.info(f"🔄 REPLACED SYMLINK: Deleted old link (Target was {current_target_str}) to update target to {resolved_path.as_posix()}")
                    
                    # Recreate
                    try:
                        os.symlink(resolved_path, symlink_path)
                        logging.info(f"🔗 CREATED SYMLINK: {symlink_path.name} -> {resolved_path.as_posix()}")
                    except Exception as e:
                        logging.error(f"Failed to create symlink {symlink_path}: {e}")
                        return
                else:
                    # Link is correct, do nothing
                    logging.debug(f"Symlink already exists and is correct: {symlink_path.name}")
            else:
                logging.warning(f"File exists at {symlink_path.name} but is not a symlink. Skipping.")
                return # Skip this item
        else:
            # Create new symlink
            try:
                os.symlink(resolved_path, symlink_path)
                logging.info(f"🔗 CREATED SYMLINK: {symlink_path.name} -> {resolved_path.as_posix()}")
            except Exception as e:
                logging.error(f"Failed to create symlink {symlink_path}: {e}")
                return # Don't create NFO if link creation failed

        # 3. Handle NFO (Creation or Update)
        is_movie = data['IsMovie']
        nfo_content = self._generate_nfo_content(data, is_movie)

        # Only overwrite NFO if it is missing. We prefer not to touch existing NFOs.
        if not nfo_path.exists():
            try:
                nfo_path.write_text(nfo_content, encoding='utf-8')
                logging.info(f"📝 CREATED NFO: {nfo_path.name}")
            except Exception as e:
                logging.error(f"Failed to write NFO {nfo_path}: {e}")

        # Update current state map with the final output path and the resolved base name
        self.current_state[media_file_id] = {
            'link_path': str(symlink_path),
            'nfo_path': str(nfo_path),
            'original_path': str(resolved_path), # Store the path of the actual file
            'resolved_filename_base': filename_base # NEW: Store the final, collision-resolved name
        }

    
    def _generate_nfo_content(self, data: Dict[str, str], is_movie: bool) -> str:
        """Helper to generate XML content for NFO."""
        try:
            runtime_min = round(int(data['RuntimeMs']) / 60000)
        except (ValueError, TypeError):
            runtime_min = '0'
            
        directors_xml = "".join(f"<director>{d.strip()}</director>" for d in data.get('Directors', '').split(';') if d.strip())
        writers_xml = "".join(f"<writer>{w.strip()}</writer>" for w in data.get('Writers', '').split(';') if w.strip())

        if is_movie:
            return f"""<movie>
    <title>{data['Title'].strip()}</title>
    <originaltitle>{data['Title'].strip()}</originaltitle>
    <year>{data['Year']}</year>
    <plot>{data['Description']}</plot>
    <rating>{data['Rated']}</rating>
    <runtime>{runtime_min} min</runtime>
    <genre>{data['Genre']}</genre>
    {directors_xml}
    {writers_xml}
</movie>"""
        else:
            return f"""<episodedetails>
    <title>{data['EpisodeName'].strip() or data['Title'].strip()}</title>
    <showtitle>{data['Title']}</showtitle>
    <season>{data['SeasonNumber']}</season>
    <episode>{data['EpisodeNumber']}</episode>
    <plot>{data['Description']}</plot>
    <year>{data['Year']}</year>
    <genre>{data['Genre']}</genre>
    <runtime>{runtime_min} min</runtime>
</episodedetails>"""


    def _create_series_nfo(self, data: Dict[str, str], show_path: Path):
        """Generates the tvshow.nfo file in the main series folder."""
        
        nfo_file = show_path / "tvshow.nfo"
        if nfo_file.exists():
            return
            
        directors_xml = "".join(f"<director>{d.strip()}</director>" for d in data.get('Directors', '').split(';') if d.strip())
        writers_xml = "".join(f"<writer>{w.strip()}</writer>" for w in data.get('Writers', '').split(';') if w.strip())
        
        nfo_content = f"""<tvshow>
    <title>{data['Title']}</title>
    <plot>{data['Description']}</plot>
    <premiered>{data['Year']}-01-01</premiered>
    <year>{data['Year']}</year>
    <genre>{data['Genre']}</genre>
    {directors_xml}
    {writers_xml}
</tvshow>"""
        
        try:
            show_path.mkdir(parents=True, exist_ok=True)
            nfo_file.write_text(nfo_content, encoding='utf-8')
            logging.info(f"Created Series NFO: {nfo_file.name}")
        except Exception as e:
            logging.error(f"Failed to write series NFO {nfo_file}: {e}")


    def _process_tv_show(self, data: Dict[str, str], resolved_path: Path):
        """Processes a TV show episode, using state-based collision recall."""
        if not data['Title']: return
        
        media_file_id = data['MediaFileID']

        # Determine S/E numbers using the 3-Tier Fallback logic
        try:
            sage_season_num = int(data['SeasonNumber'] or 0)
            sage_episode_num = int(data['EpisodeNumber'] or 0)
        except ValueError:
            sage_season_num = 0
            sage_episode_num = 0

        season_num = sage_season_num
        episode_num = sage_episode_num
        
        if (sage_season_num == 0 and sage_episode_num == 0):
            sxe_result = self._parse_sxxeyy(resolved_path.name) 
            if sxe_result:
                season_num, episode_num = sxe_result
            else:
                season_num = 0
                episode_num = 1
        
        # Define output paths
        cleaned_show_name = self._clean_directory_name(data['Title'].strip())
        show_path = self.tv_shows_root / cleaned_show_name
        season_path = show_path / f"Season {season_num:02d}"
        
        # Ensure Series NFO is present
        if cleaned_show_name not in self.processed_tv_shows:
            self._create_series_nfo(data, show_path)
            self.processed_tv_shows.add(cleaned_show_name)

        raw_episode_name = data['EpisodeName'].strip() if data['EpisodeName'] else 'Episode'
        episode_name_clean = self._clean_directory_name(raw_episode_name)
        
        # Base filename before collision check
        default_filename_base = f"{cleaned_show_name} - S{season_num:02d}E{episode_num:02d} - {episode_name_clean}"
        filename_base = default_filename_base
        
        # 1. CHECK STATE FOR PRE-RESOLVED NAME
        resolved_name = self._get_resolved_filename_base(media_file_id)
        
        if resolved_name:
            logging.debug(f"Collision Recall: Using previously resolved name for ID {media_file_id}: {resolved_name}")
            filename_base = resolved_name
        else:
            # 2. IF NEW FILE, RUN COLLISION RESOLUTION (on disk)
            new_target_comparable = self._get_comparable_path(str(resolved_path))
            collision_detected = False
            
            # Check common extensions
            for suffix in ['.mpg', '.mkv', '.mp4', '.ts', '.avi']: 
                existing_symlink_path = season_path / f"{filename_base}{suffix}"
                
                if existing_symlink_path.exists() and existing_symlink_path.is_symlink():
                    try:
                        existing_target_comparable = self._get_comparable_path(os.readlink(existing_symlink_path))
                        
                        if existing_target_comparable != new_target_comparable:
                            collision_detected = True
                            break
                    except Exception as e:
                        logging.warning(f"Error during collision check for {filename_base}: {e}. Skipping uniqueness check.")
                        
            if collision_detected:
                logging.warning(f"⚠️ COLLISION RESOLVED: Found conflicting symlink for base '{default_filename_base}'. Appending MediaFileID {media_file_id} for uniqueness.")
                # Collision detected! Append the unique ID to the filename base
                filename_base = f"{default_filename_base} - {media_file_id}"

        # Create media files (symlink and NFO) using the final filename_base
        self._create_media_files(data, resolved_path, season_path, filename_base)


    def _process_movie(self, data: Dict[str, str], resolved_path: Path):
        """Processes a Movie, using state-based collision recall."""
        if not data['Title']: return
        
        media_file_id = data['MediaFileID']

        raw_movie_name = data['Title'].strip()
        movie_year = data['Year']
        movie_name_with_year = f"{raw_movie_name} ({movie_year})" if movie_year else raw_movie_name
        
        cleaned_dir_name = self._clean_directory_name(movie_name_with_year)

        # Structure logic
        if self.flat_movie_structure:
            target_dir = self.movies_root
            default_filename_base = cleaned_dir_name
        else:
            target_dir = self.movies_root / cleaned_dir_name
            default_filename_base = cleaned_dir_name 
            
        filename_base = default_filename_base

        # 1. CHECK STATE FOR PRE-RESOLVED NAME
        resolved_name = self._get_resolved_filename_base(media_file_id)
        
        if resolved_name:
            logging.debug(f"Collision Recall: Using previously resolved name for ID {media_file_id}: {resolved_name}")
            filename_base = resolved_name
        else:
            # 2. IF NEW FILE, RUN COLLISION RESOLUTION (on disk)
            new_target_comparable = self._get_comparable_path(str(resolved_path))
            collision_detected = False
            
            # Check common extensions
            for suffix in ['.mpg', '.mkv', '.mp4', '.ts', '.avi']: 
                existing_symlink_path = target_dir / f"{filename_base}{suffix}"
                if existing_symlink_path.exists() and existing_symlink_path.is_symlink():
                    try:
                        existing_target_comparable = self._get_comparable_path(os.readlink(existing_symlink_path))
                        
                        if existing_target_comparable != new_target_comparable:
                            collision_detected = True
                            break
                    except Exception as e:
                        logging.warning(f"Error during movie collision check for {filename_base}: {e}. Skipping uniqueness check.")
                        
            if collision_detected:
                logging.warning(f"⚠️ COLLISION RESOLVED: Found conflicting symlink for movie '{default_filename_base}'. Appending MediaFileID {media_file_id} for uniqueness.")
                # Collision detected! Append the unique ID to the filename base
                filename_base = f"{default_filename_base} - {media_file_id}"
            
        # Create media files (symlink and NFO)
        self._create_media_files(data, resolved_path, target_dir, filename_base)
    
    # -------------------------------------------------------------------------
    # CLEANUP FUNCTION (Debug Logging Included)
    # -------------------------------------------------------------------------

    def _cleanup_stale_files(self):
        """
        Performs a cleanup pass to remove symlinks and NFOs that refer to 
        files no longer present on the filesystem.
        """
        logging.info("--- Starting Stale File Cleanup (Debug Mode) ---")
        stale_count = 0
        
        for media_id, paths in self.previous_state.items():
            try:
                # Use Path to correctly handle platform paths
                link_path = Path(paths.get('link_path', ''))
                nfo_path = Path(paths.get('nfo_path', ''))
                
                if not link_path or not nfo_path:
                    logging.debug(f"Cleanup: Skipping ID {media_id} due to missing path data in state.")
                    continue

                logging.debug(f"Cleanup Check ID {media_id}: Checking link {link_path.name}")
                
                # 1. Check if the symlink file exists at the expected link_path
                if not link_path.exists():
                    logging.debug(f"  -> Status: Link file not found on disk at {link_path}. Skipping.")
                    continue

                # 2. Check if it's actually a symlink
                if not link_path.is_symlink():
                    logging.warning(f"  -> Status: File exists at {link_path.name} but is NOT a symlink. Skipping cleanup.")
                    continue

                # 3. Read the target path (where the symlink points)
                target_path_str = os.readlink(link_path)
                target_path = Path(target_path_str)
                
                logging.debug(f"  -> Link target: {target_path_str}")

                # 4. Critical check: Does the target file exist?
                if not target_path.exists():
                    # STALE CONDITION MET: Link exists, but its target is gone. DELETE.
                    
                    # Remove symlink
                    os.remove(link_path)
                    logging.info(f"🗑️ CLEANUP DELETION: Removed stale symlink {link_path.name}")
                    logging.info(f"  -> Reason: Target file '{target_path.name}' is missing.")
                    
                    # Remove associated NFO file
                    if nfo_path.exists():
                        os.remove(nfo_path)
                        logging.info(f"🗑️ CLEANUP DELETION: Removed associated NFO {nfo_path.name}.")
                        
                    stale_count += 1
                else:
                    logging.debug(f"  -> Status: Symlink is VALID (Target file exists).")

            except KeyError:
                logging.warning(f"Cleanup: State entry for ID {media_id} is malformed. Skipping.")
            except FileNotFoundError:
                logging.warning(f"Cleanup: File system error or link target read failure for ID {media_id}. Skipping.")
            except Exception as e:
                logging.error(f"Cleanup: Unexpected error during cleanup for ID {media_id}: {e}", exc_info=False)


        logging.info(f"--- Stale File Cleanup Complete. {stale_count} items removed. ---")
    # -------------------------------------------------------------------------
    
    def run_generator(self):
        """Iterates through all media files from the API and generates links/NFOs."""
        
        # 1. Run Cleanup before processing new data
        self._cleanup_stale_files()

        # 2. Start Processing and Generating
        start = 0
        total_generated = 0
        
        logging.info("Starting generation process by fetching all media files from SageX...")
        
        while True:
            # Check the limit *before* fetching the next page
            if self.max_files_to_process > 0 and total_generated >= self.max_files_to_process:
                logging.info(f"Processing limit of {self.max_files_to_process} files reached. Stopping API fetching.")
                break
                
            root_element = self._get_media_files_page(start)
            
            if root_element is None:
                break

            media_files = root_element.findall('./MediaFile')
            
            if not media_files:
                logging.info(f"No more media files found. Finished API processing.")
                break

            for file_element in media_files:
                
                # Check the limit *before* processing this single file
                if self.max_files_to_process > 0 and total_generated >= self.max_files_to_process:
                    logging.info(f"Processing limit of {self.max_files_to_process} files reached. Stopping file processing.")
                    break
                    
                metadata = self._extract_data(file_element)
                original_sage_path = metadata['FilePath']
                
                if not original_sage_path: 
                    logging.warning(f"Skipping media file ID {metadata['MediaFileID']}: No file path reported by SageTV.")
                    continue
                
                # CRITICAL STEP: Find the actual file, solving the MPG/MKV stale path issue
                resolved_path = self._resolve_actual_file_path(original_sage_path)
                
                if resolved_path is None:
                    # If the file is not on disk, we can't create a link or NFO. 
                    logging.warning(f"Skipping '{metadata['Title']}': Actual media file not found on disk at {original_sage_path} or alternatives.")
                    continue
                
                is_movie = metadata['IsMovie']

                try:
                    if is_movie:
                        self._process_movie(metadata, resolved_path)
                    else:
                        self._process_tv_show(metadata, resolved_path)

                    total_generated += 1
                except Exception as e:
                    logging.error(f"Error processing {metadata['Title']} (ID: {metadata['MediaFileID']}): {e}", exc_info=True)
            
            # If we broke out of the inner loop (file limit reached)
            if self.max_files_to_process > 0 and total_generated >= self.max_files_to_process:
                break
            
            if len(media_files) < self.page_size:
                break 
                
            start += self.page_size
            time.sleep(0.1) 
            
        logging.info("Generation process complete.")
        logging.info(f"Total media files successfully processed: {total_generated}")
        
        # 3. Save the new state for the next run's cleanup
        self._save_state()
        logging.info("State saved successfully.")


# --- Main Execution Block ---
if __name__ == "__main__":
    
    try:
        config_data = load_config()
        setup_logging(config_data)

        logging.info("NFO Generator Utility (Final Collision Fix - State Based) initialized.")
        
        utility = NFOGeneratorUtility(config_data)
        utility.run_generator()
        
    except Exception as main_e:
        critical_logger = logging.getLogger()
        critical_logger.critical(f"A fatal error occurred during program execution: {main_e}", exc_info=True)
