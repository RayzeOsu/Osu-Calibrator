import os
import re
import shutil
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QMessageBox, QFileDialog, QInputDialog
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from config import get_persistent_songs_dir

class AudioManager:
    def __init__(self, window):
        self.window = window
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.3)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setLoops(-1)

        self.current_song_file: Optional[str] = None
        self.current_target_bpm: Optional[int] = None
        self.song_is_paused = False

        self.player.errorOccurred.connect(self._on_player_error)

    def _on_player_error(self, error, error_string):
        if error == QMediaPlayer.NoError:
            return
        friendly = {
            QMediaPlayer.ResourceError: "The audio file could not be opened. It may be missing, locked by another program, or corrupted.",
            QMediaPlayer.FormatError: "The audio format is not supported. Try re-encoding the song to MP3.",
            QMediaPlayer.NetworkError: "A network error occurred loading the song.",
            QMediaPlayer.AccessDeniedError: "Permission denied when reading the song file.",
        }.get(error, f"An audio error occurred: {error_string}")

        self.stop()

        QMessageBox.warning(
            self.window,
            "Audio Playback Error",
            f"{friendly}\n\nPhases will continue to work normally without audio.",
        )

    def extract_bpm_from_filename(self, file_name: str) -> Optional[int]:
        match = re.search(r"\[(\d+)\s*BPM\]", file_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def get_song_display_name(self, file_name: str) -> str:
        if file_name.lower().endswith(".mp3"):
            return file_name[:-4]
        return file_name

    def get_selected_song_file(self) -> Optional[str]:
        data = self.window.song_combo.currentData()
        if isinstance(data, str):
            return data
        return None

    def refresh_song_dropdown(self, preserve_selection: bool = True, preferred_file: Optional[str] = None):
        previous_file = self.get_selected_song_file() if preserve_selection else None

        self.window.song_combo.blockSignals(True)
        self.window.song_combo.clear()
        self.window.song_combo.addItem("None (No Audio)", None)

        songs_dir = get_persistent_songs_dir()
        os.makedirs(songs_dir, exist_ok=True)

        song_files = [f for f in os.listdir(songs_dir) if f.lower().endswith(".mp3")]

        songs_with_bpm = []
        for file_name in song_files:
            bpm = self.extract_bpm_from_filename(file_name)
            sort_bpm = bpm if bpm is not None else 9999
            display_name = self.get_song_display_name(file_name)
            songs_with_bpm.append((sort_bpm, display_name.lower(), display_name, file_name))

        songs_with_bpm.sort(key=lambda item: (item[0], item[1]))

        for _, _, display_name, file_name in songs_with_bpm:
            self.window.song_combo.addItem(display_name, file_name)

        target_file = preferred_file if preferred_file is not None else previous_file
        if target_file is not None:
            index = self.window.song_combo.findData(target_file)
            if index >= 0:
                self.window.song_combo.setCurrentIndex(index)
            else:
                self.window.song_combo.setCurrentIndex(0)
        else:
            self.window.song_combo.setCurrentIndex(0)

        self.window.song_combo.blockSignals(False)

    def import_custom_song(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Import Custom Song",
            "",
            "MP3 Files (*.mp3)",
        )

        if not file_path:
            return

        songs_dir = get_persistent_songs_dir()
        os.makedirs(songs_dir, exist_ok=True)

        file_name = os.path.basename(file_path)
        destination_path = os.path.join(songs_dir, file_name)

        if os.path.abspath(file_path) == os.path.abspath(destination_path):
            self.refresh_song_dropdown(preferred_file=file_name)
            return

        if os.path.exists(destination_path):
            reply = QMessageBox.question(
                self.window,
                "Song Already Exists",
                f"'{file_name}' already exists in your songs folder.\n\nDo you want to replace it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.refresh_song_dropdown(preferred_file=file_name)
                return

        try:
            shutil.copy2(file_path, destination_path)
        except OSError as e:
            QMessageBox.critical(
                self.window,
                "Import Failed",
                f"Could not import the selected song.\n\n{e}",
            )
            return

        if self.extract_bpm_from_filename(file_name) is None:
            bpm_input, ok = QInputDialog.getInt(
                self.window,
                "BPM Missing",
                f"No BPM found in '{file_name}'.\n\nEnter the song's BPM to enable the visual metronome (or cancel to skip):",
                150, 1, 500, 1
            )
            if ok:
                new_file_name = f"{file_name[:-4]} [{bpm_input} BPM].mp3"
                new_destination = os.path.join(songs_dir, new_file_name)
                try:
                    os.rename(destination_path, new_destination)
                    file_name = new_file_name
                except OSError as e:
                    QMessageBox.warning(self.window, "Rename Failed", f"Could not rename file to include BPM:\n{e}\n\nThe track was imported but the BPM tag was not saved.")

        self.refresh_song_dropdown(preferred_file=file_name)

    def play(self, file_name: str):
        song_path = os.path.join(get_persistent_songs_dir(), file_name)
        if os.path.exists(song_path):
            self.player.setSource(QUrl.fromLocalFile(song_path))
            self.player.play()
            self.current_song_file = file_name
            self.current_target_bpm = self.extract_bpm_from_filename(file_name)
            self.song_is_paused = False
            return True
        else:
            QMessageBox.warning(
                self.window,
                "Song Missing",
                f"Selected track not found:\n\n{song_path}\n\nAudio disabled."
            )
            self.stop()
            return False

    def resume(self):
        if self.song_is_paused:
            self.player.play()
            self.song_is_paused = False

    def pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.song_is_paused = True

    def stop(self):
        self.player.stop()
        self.song_is_paused = False
        self.current_song_file = None
        self.current_target_bpm = None
        if hasattr(self.window, "metronome_widget"):
            self.window.metronome_widget.stop()

    def set_volume(self, value: int):
        self.audio_output.setVolume(value / 100.0)