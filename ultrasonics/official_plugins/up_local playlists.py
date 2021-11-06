#!/usr/bin/env python3

"""
up_local playlists
Local playlists plugin for ultrasonics.

Designed as both an input and output plugin.
Interacts with physical playlist files, reading the songs present in each one.
Extracts additional tag data from each song discovered.
Upon saving playlists, it will update any existing playlists before creating new ones.

XDGFX, 2020
"""

import io
import os
import re
import shutil
from datetime import datetime

from tqdm import tqdm

from app import _ultrasonics
from ultrasonics import logs
from ultrasonics.tools import local_tags, name_filter

log = logs.create_log(__name__)

handshake = {
    "name": "local playlists",
    "description": "interface with all local .m3u playlists in a directory",
    "type": ["inputs", "outputs"],
    "mode": ["playlists"],
    "version": "0.4",
    "settings": [
        {
            "type": "string",
            "value": "Local playlists (such as .m3u files) include paths directly to the song.",
        },
        {
            "type": "string",
            "value": "If the playlists were generated by a different computer/server to the one you're running ultrasonics 🎧 on currently, the paths saved in the playlist cannot be used by ultrasonics to find the song file.",
        },
        {
            "type": "string",
            "value": "Therefore, you need to provide a prepend for your playlist music files, and the path relative to ultrasonics.",
        },
        {
            "type": "string",
            "value": "This prepend is the longest path 📏 common to all audio files.",
        },
        {
            "type": "text",
            "label": "Local Playlist Prepend",
            "name": "local_prepend",
            "value": "D:/Music",
        },
        {
            "type": "text",
            "label": "ultrasonics Prepend",
            "name": "ultrasonics_prepend",
            "value": "/mnt/music library/music",
        },
    ],
}

supported_playlist_extensions = [".m3u"]

log.info(f"Supported playlist extensions include: {supported_playlist_extensions}")


def run(settings_dict, **kwargs):
    """
    1. Checks for compatibility between unix / nt playlist paths.
    2. Create a list of all playlists which already exist.

    if input:
        3. Read each playlist.
        4. Convert the paths to work with ultrasonics.
        5. Read matadata from each song and use to build the songs_dict.

        @return: settings_dict

    if output:
        3. Create backup of playlists id requested
        4. Either open an existing playlist, or create a new playlist with the provided playlist name.
        5. Convert supplied path back to original playlist path style.
        6. Update each playlist with the new data (overwrites any existing songs)
    """

    database = kwargs["database"]
    global_settings = kwargs["global_settings"]
    component = kwargs["component"]
    applet_id = kwargs["applet_id"]
    songs_dict = kwargs["songs_dict"]

    def remove_prepend(path, invert=False):
        """
        Remove any playlist local music files prepend, so only the path relative to the user's music directory is left.
        Default is local prepend, invert is ultrasonics prepend.
        """

        if not invert:
            if database["local_prepend"]:
                return (
                    path.replace(database["local_prepend"], "").lstrip("/").lstrip("\\")
                )

        else:
            if database["ultrasonics_prepend"]:
                return (
                    path.replace(database["ultrasonics_prepend"], "")
                    .lstrip("/")
                    .lstrip("\\")
                )

        # If no database prepends exist, return the same path
        return path

    def convert_path(path, invert=False):
        """
        Converts a path string into the system format.
        """
        if enable_convert_path:
            unix = os.name != "nt"

            if invert:
                unix = not unix

            if unix:
                return path.replace("\\", "/")
            else:
                return path.replace("/", "\\")
        else:
            return path

    def backup_playlists():
        """
        Create copy of local playlists folder in backups directory.
        Removes oldest backups exceeding retention limit.
        """

        runtime = (
            str(datetime.now().replace(microsecond=0))
            .replace(" ", "-")
            .replace(":", "-")
        )
        backup_dir = os.path.join(
            _ultrasonics["config_dir"], "up_local playlists", "backups", applet_id, ""
        )

        # Create the backups folder if it doesn't already exist
        try:
            os.makedirs(os.path.dirname(backup_dir))
        except FileExistsError:
            # Folder already exists
            pass

        retention_dict = {
            "No Backups": 0,
            "3 Backups": 3,
            "5 Backups": 5,
            "10 Backups": 10,
        }

        retention = retention_dict[settings_dict["retention"]]

        # Backup local playlists
        if retention > 0:
            log.debug("Backing up local playlists...")
            shutil.copytree(path, os.path.join(backup_dir, runtime))
            log.debug(
                f"Backed up local playlists to: {os.path.join(backup_dir, runtime)}."
            )

        backups = os.listdir(backup_dir)
        backup_time = [b.replace("-", "") for b in backups]

        while len(backups) > retention:
            log.info(
                f"INFO: Number of backups ({len(backups)}) exceeds backup retention ({retention})"
            )
            oldest_backup = backup_time.index(min(backup_time))

            # Delete oldest backup
            shutil.rmtree(os.path.join(backup_dir, backups[oldest_backup]))
            del backups[oldest_backup], backup_time[oldest_backup]

            log.info(f"Deleted oldest backup in {backup_dir}.")

        # Calculate backup size
        size = (
            sum(
                os.path.getsize(os.path.join(dirpath, filename))
                for dirpath, dirnames, filenames in os.walk(backup_dir)
                for filename in filenames
            )
            / 1024
            / 1024
        )
        log.info(f"Your backups are currently taking up {round(size, 2)}MB of space")

    # Get path for playlist files
    path = settings_dict["dir"].rstrip("/").rstrip("\\")
    playlists = []

    # Check if file paths are unix or nt (windows)
    enable_convert_path = False
    ultrasonics_unix = database["ultrasonics_prepend"].startswith("/")
    local_unix = database["local_prepend"].startswith("/")

    if ultrasonics_unix != local_unix:
        log.debug(
            "ultrasonics paths and local playlist paths do not use the same separators!"
        )
        enable_convert_path = True

    # Create a dictionary 'playlists' of all playlists in the specified directory
    # name is the playlist name
    # path is the full path to the playlist
    try:
        if settings_dict["recursive"] == "Yes":
            # Recursive mode
            for root, _, files in os.walk(path):
                for item in files:
                    playlists.append(
                        {
                            "name": os.path.splitext(item)[0],
                            "path": os.path.join(root, item),
                        }
                    )

        else:
            # Non recursive mode
            files = os.listdir(path)
            for item in files:
                playlists.append(
                    {
                        "name": os.path.splitext(item)[0],
                        "path": os.path.join(path, item),
                    }
                )

    except Exception as e:
        log.error(e)

    # Remove any files which don't have a supported extension
    playlists = [
        item
        for item in playlists
        if os.path.splitext(item["path"])[1] in supported_playlist_extensions
    ]

    log.info(f"Found {len(playlists)} playlist(s) in supplied directory.")

    if component == "inputs":
        songs_dict = []

        # Apply regex filter to playlists
        filter_titles = [item["name"] for item in playlists]
        filter_titles = name_filter.filter_list(filter_titles, settings_dict["filter"])

        log.info(f"{len(filter_titles)} playlist(s) match supplied filter.")

        playlists = [item for item in playlists if item["name"] in filter_titles]

        for playlist in playlists:

            # Initialise entry for this playlist
            songs_dict_entry = {"name": playlist["name"], "id": {}, "songs": []}

            # Read the playlist file
            songs = io.open(playlist["path"], "r", encoding="utf8").read().splitlines()

            for song in tqdm(songs, desc=f"Processing playlist: {playlist['name']}"):

                # Skip .m3u tags beginning with "#"
                if song.startswith("#"):
                    continue

                # Convert path to be usable by ultrasonics
                song_path = remove_prepend(song)
                song_path = convert_path(song_path)
                song_path = os.path.join(database["ultrasonics_prepend"], song_path)

                # Skip files which don't exist
                if not os.path.isfile(song_path):
                    log.warning(f"{song_path} does not exist! Skipping this song.")
                    continue

                try:
                    temp_song_dict = local_tags.tags(song_path)

                except NotImplementedError:
                    log.warning(f"The file {song_path} is not a supported filetype")
                    continue

                except Exception as e:
                    log.error(f"Could not load tags from song: {song_path}")
                    log.error(e)

                # Add entry to the full songs dict for this playlist
                songs_dict_entry["songs"].append(temp_song_dict)

            # Add previous playlist to full songs_dict
            songs_dict.append(songs_dict_entry)

        return songs_dict

    elif component == "outputs":
        # Backup local dir if requested
        backup_playlists()

        # Sync songs_dict to local playlists
        existing_playlist_titles = [item["name"] for item in playlists]

        for item in songs_dict:
            # Replace invalid characters in playlist title
            item["name"] = re.sub("[\\/:*?|<>]+[ ]*", "", item["name"])

            # Check if playlist already exists
            if item["name"] in existing_playlist_titles:
                # Update existing playlist
                existing_playlist_path = [
                    x["path"] for x in playlists if x["name"] == item["name"]
                ][0]

                f = io.open(existing_playlist_path, "w", encoding="utf8")

            else:
                # Create new playlist
                new_playlist_path = os.path.join(path, item["name"] + ".m3u")

                f = io.open(new_playlist_path, "w", encoding="utf8")

            # Get songs list for this playlist
            songs = item["songs"]

            for song in songs:
                # Skip songs without local file
                if "location" not in song.keys():
                    continue

                # Find location of song, and convert back to local playlists format
                song_path = song["location"]
                song_path = remove_prepend(song_path, invert=True)

                prepend_path_converted = convert_path(database["local_prepend"])

                song_path = os.path.join(prepend_path_converted, song_path)
                song_path = convert_path(song_path, invert=True)

                # Write song to playlist terminated with newline character
                f.write(song_path + "\n")

            f.close()


def builder(**kwargs):
    component = kwargs["component"]

    settings_dict = [
        {
            "type": "string",
            "value": f"⚠️ Only {', '.join(supported_playlist_extensions)} extensions are supported for playlists, and .mp3, m4a extensions are supported for audio files. Unsupported files will be ignored.",
        },
        {
            "type": "text",
            "label": "Directory",
            "name": "dir",
            "value": "/mnt/music library/playlists",
            "required": True,
        },
        {
            "type": "string",
            "value": "Enabling recursive mode will search all subfolders for more playlists.",
        },
        {
            "type": "radio",
            "label": "Recursive",
            "name": "recursive",
            "id": "recursive",
            "options": ["Yes", "No"],
            "required": True,
        },
    ]

    if component == "inputs":
        settings_dict.extend(
            [
                {
                    "type": "string",
                    "value": "You can use regex style filters to only select certain playlists. For example, 'disco' would sync playlists 'Disco 2010' and 'nu_disco', or '2020$' would only sync playlists which ended with the value '2020'.",
                },
                {"type": "string", "value": "Leave it blank to sync everything 🤓."},
                {"type": "text", "label": "Filter", "name": "filter", "value": ""},
            ]
        )
    elif component == "outputs":
        settings_dict.extend(
            [
                {
                    "type": "string",
                    "value": "If you want, your local playlists directory can be backed up inside the `config/up_local playlists` folder. You can set the backup retention here 💾. Everything inside the folder will be backed up, so keep an eye on the space requirements.",
                },
                {
                    "type": "radio",
                    "label": "Backup Retention",
                    "name": "retention",
                    "id": "retention",
                    "options": ["No Backups", "3 Backups", "5 Backups", "10 Backups"],
                    "required": True,
                },
                {
                    "type": "string",
                    "value": "💿 This plugin will update any existing playlist to match the one in the applet. This means any existing tracks will be removed if they are not present in the new playlist!",
                },
            ]
        )

    return settings_dict
