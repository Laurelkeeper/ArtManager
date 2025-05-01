import sys
import os
import time
import shutil
import sqlite3
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
    QHBoxLayout, QShortcut, QMessageBox, QSplitter,
    QListView, QAbstractItemView, QMenu, QInputDialog,
    QSizePolicy
)
from PyQt5.QtGui import QPixmap, QKeySequence, QIcon
from PyQt5.QtCore import Qt, QSize, QPoint, QThread, pyqtSignal

class SaveArtWorker(QThread):
    finished = pyqtSignal(int, str)   # art_id, filepath
    error    = pyqtSignal(str)

    def __init__(self, image, name, artist, tags, image_dir, db_path, existing):
        super().__init__()
        self.image     = image
        self.name      = name
        self.artist    = artist
        self.tags      = tags
        self.image_dir = image_dir
        self.db_path   = db_path
        self.existing  = existing  # tuple (id, old_path) or None

    def run(self):
        try:
            import time, os, sqlite3
            # 1) save PNG
            fname = f"art_{int(time.time())}.png"
            full  = os.path.join(self.image_dir, fname)
            self.image.save(full)
            # 2) open its own DB connection
            conn = sqlite3.connect(self.db_path)
            c    = conn.cursor()
            if self.existing:
                art_id, old = self.existing
                c.execute(
                    "UPDATE artworks SET filepath=?, artist=?, tags=?, timestamp=CURRENT_TIMESTAMP WHERE id=?",
                    (full, self.artist, ','.join(sorted(self.tags)), art_id)
                )
                try: os.remove(old)
                except: pass
            else:
                c.execute(
                    "INSERT INTO artworks (name, filepath, artist, tags) VALUES (?, ?, ?, ?)",
                    (self.name, full, self.artist, ','.join(sorted(self.tags)))
                )
                art_id = c.lastrowid
            for t in self.tags:
                try: c.execute("INSERT INTO tags (tag) VALUES (?)", (t,))
                except sqlite3.IntegrityError: pass
            conn.commit()
            conn.close()
            self.finished.emit(art_id, full)
        except Exception as e:
            self.error.emit(str(e))

class ArtManager(QMainWindow):
    def __init__(self):
        super().__init__()
        base = os.path.join(os.path.expanduser("~"), "ArtManager")
        self.image_dir = os.path.join(base, "images")
        os.makedirs(self.image_dir, exist_ok=True)
        self.db_path = os.path.join(base, "art.db")
        self.current_tags = set()
        self.current_art_id = None
        self.current_image = None  # QImage
        self.init_db()
        self.init_ui()
        self.search_art()  # initial load

    def init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        c = self.conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS artworks (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            filepath TEXT UNIQUE,
            artist TEXT,
            tags TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            tag TEXT PRIMARY KEY
        )""")
        self.conn.commit()

    def init_ui(self):
        self.setWindowTitle("Art Manager")
        self.resize(1000, 600)
        # shortcuts
        QShortcut(QKeySequence.Paste, self).activated.connect(self.paste_image)
        QShortcut(QKeySequence("Ctrl+Shift+X"), self).activated.connect(self.clear_all)
        QShortcut(QKeySequence.Copy, self).activated.connect(self.copy_current)
        QShortcut(QKeySequence("Ctrl+Shift+V"), self).activated.connect(self.replace_image)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.create_main_panel())
        splitter.addWidget(self.create_side_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(splitter)
        self.setCentralWidget(container)
        self.setStyleSheet("""
            QWidget { background-color: #2b2b2b; color: #ddd; }
            QPushButton {
                background-color: #3c3f41;
                border-radius: 5px;
                padding: 5px;
            }
            QPushButton:hover { background-color: #4c5052; }
            QPushButton:pressed { background-color: #2c2f31; }
            QLineEdit { background-color: #3c3f41; border-radius: 5px; padding: 4px; }
            QListWidget, QListView { background-color: #313335; border-radius: 5px; }
        """)

    def create_main_panel(self):
        widget = QWidget()
        v = QVBoxLayout(widget)

        # Search bar
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search tags, name, artist...")
        self.search_input.returnPressed.connect(self.search_art)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self.search_art)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_btn)
        v.addLayout(search_layout)

        # Results list
        self.results_list = QListWidget()
        self.results_list.setViewMode(QListView.IconMode)
        self.results_list.setIconSize(QSize(64, 64))
        self.results_list.setResizeMode(QListView.Adjust)
        self.results_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.results_list.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.results_list.itemClicked.connect(self.handle_result_click)
        v.addWidget(self.results_list)

        # Image preview
        self.image_label = QLabel("Paste or select an image")
        self.image_label.setAlignment(Qt.AlignCenter)
        # allow the label to shrink below the pixmap’s native size:
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        v.addWidget(self.image_label, stretch=1)

        # Metadata inputs
        form = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Image name")
        self.artist_input = QLineEdit()
        self.artist_input.setPlaceholderText("Artist name")
        form.addWidget(self.name_input)
        form.addWidget(self.artist_input)
        v.addLayout(form)

        # Action buttons
        btns = QHBoxLayout()
        paste_btn = QPushButton("Paste Image"); paste_btn.clicked.connect(self.paste_image)
        copy_btn = QPushButton("Copy Image"); copy_btn.clicked.connect(self.copy_current)
        self.save_btn  = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_art)
        delete_btn = QPushButton("Delete"); delete_btn.clicked.connect(self.delete_current)
        for btn in (paste_btn, copy_btn, self.save_btn, delete_btn):
            btn.setFixedHeight(30)
            btns.addWidget(btn)
        v.addLayout(btns)
        return widget

    def create_side_panel(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel("Tags")
        title.setStyleSheet("font-weight:bold;")
        layout.addWidget(title)

        # Tag list with context menu
        self.tag_list = QListWidget()
        self.tag_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tag_list.customContextMenuRequested.connect(self.tag_context_menu)
        self.tag_list.itemClicked.connect(self.toggle_tag)
        layout.addWidget(self.tag_list)

        # New tag entry
        add_layout = QHBoxLayout()
        self.new_tag_input = QLineEdit()
        self.new_tag_input.setPlaceholderText("New tag...")
        self.new_tag_input.returnPressed.connect(self.add_tag)
        add_btn = QPushButton("Add Tag"); add_btn.clicked.connect(self.add_tag)
        add_layout.addWidget(self.new_tag_input)
        add_layout.addWidget(add_btn)
        layout.addLayout(add_layout)

        self.load_tags()
        return widget

    def handle_result_click(self, item):
        art_id, name, path, artist, tags = item.data(Qt.UserRole)
        if art_id == self.current_art_id:
            # Deselect
            self.clear_selection()
        else:
            self.open_art(item)

    def clear_selection(self):
        self.current_art_id = None
        self.current_tags.clear()
        self.current_image = None
        self.name_input.clear()
        self.artist_input.clear()
        self.image_label.setText("Paste or select an image")
        self.results_list.clearSelection()
        self.load_tags()

    def load_tags(self):
        self.tag_list.clear()
        c = self.conn.cursor()
        all_tags = [row[0] for row in c.execute("SELECT tag FROM tags")]
        selected = sorted([t for t in all_tags if t in self.current_tags])
        unselected = sorted([t for t in all_tags if t not in self.current_tags])
        for tag in selected + unselected:
            item = QListWidgetItem(tag)
            if tag in self.current_tags:
                item.setBackground(self.palette().highlight())
            self.tag_list.addItem(item)

    def add_tag(self):
        tag = self.new_tag_input.text().strip().lower()
        if not tag: return
        c = self.conn.cursor()
        try:
            c.execute("INSERT INTO tags (tag) VALUES (?)", (tag,))
            self.conn.commit(); self.new_tag_input.clear(); self.load_tags()
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "Duplicate Tag", f"'{tag}' already exists.")

    def tag_context_menu(self, pos: QPoint):
        item = self.tag_list.itemAt(pos)
        if not item: return
        tag = item.text()
        menu = QMenu()
        rename_act = menu.addAction("Rename")
        delete_act = menu.addAction("Delete")
        action = menu.exec_(self.tag_list.mapToGlobal(pos))
        if action == delete_act:
            if QMessageBox.question(self, "Delete Tag", f"Remove tag '{tag}' and from all artworks?",
                                     QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
                self.remove_tag(tag)
        elif action == rename_act:
            new, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=tag)
            if ok and new.strip():
                self.rename_tag(tag, new.strip().lower())

    def remove_tag(self, tag):
        c = self.conn.cursor()
        c.execute("DELETE FROM tags WHERE tag=?", (tag,))
        rows = c.execute("SELECT id, tags FROM artworks WHERE tags LIKE ?", (f"%{tag}%",)).fetchall()
        for art_id, tags in rows:
            new_tags = [t for t in tags.split(',') if t != tag]
            c.execute("UPDATE artworks SET tags=? WHERE id=?", (','.join(new_tags), art_id))
        self.conn.commit()
        self.current_tags.discard(tag)
        self.search_art()
        self.load_tags()

    def rename_tag(self, old, new):
        c = self.conn.cursor()
        # First update artworks to replace the tag
        rows = c.execute("SELECT id, tags FROM artworks WHERE tags LIKE ?", (f"%{old}%",)).fetchall()
        for art_id, tags in rows:
            updated = [new if t == old else t for t in tags.split(',')]
            c.execute("UPDATE artworks SET tags=? WHERE id=?", (','.join(updated), art_id))
        # Then update the tags table
        try:
            c.execute("UPDATE tags SET tag=? WHERE tag=?", (new, old))
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "Rename Failed", f"Tag '{new}' already exists.")
            self.conn.rollback()
            return
        self.conn.commit()
        # Update current_tags if needed
        if old in self.current_tags:
            self.current_tags.remove(old)
            self.current_tags.add(new)
        self.search_art()
        self.load_tags()

    def toggle_tag(self, item):
        tag = item.text()
        if tag in self.current_tags:
            self.current_tags.remove(tag)
        else:
            self.current_tags.add(tag)
        self.load_tags()

    def paste_image(self):
        cb = QApplication.clipboard()
        if cb.mimeData().hasImage():
            img = cb.image()
            self.current_image = img
            pix = QPixmap.fromImage(img)
            self.display_image(pix)
            self.current_tags.clear()
            self.current_art_id = None
            self.name_input.clear(); self.artist_input.clear()
            self.load_tags()
        else:
            self.statusBar().showMessage("No image in clipboard", 2000)

    def save_art(self):
        if not self.current_image:
            self.statusBar().showMessage("No image to save", 2000)
            return
        # gather form data
        name     = self.name_input.text().strip()
        artist   = self.artist_input.text().strip()
        tags     = set(self.current_tags)
        # look up existing record
        c = self.conn.cursor()
        existing = None
        if name:
            row = c.execute("SELECT id, filepath FROM artworks WHERE name=?", (name,)).fetchone()
            if row: existing = row

        # disable UI, spawn worker
        self.save_btn.setEnabled(False)
        self._save_thread = SaveArtWorker(
            image     = self.current_image,
            name      = name,
            artist    = artist,
            tags      = tags,
            image_dir = self.image_dir,
            db_path   = self.db_path,
            existing  = existing
        )
        self._save_thread.finished.connect(self.on_save_finished)
        self._save_thread.error.connect(self.on_save_error)
        self._save_thread.start()

    def on_save_finished(self, art_id, path):
        self.current_art_id = art_id
        self.load_tags()
        self.search_art()
        self.statusBar().showMessage("Saved!", 2000)
        self.save_btn.setEnabled(True)

    def on_save_error(self, msg):
        QMessageBox.critical(self, "Save Error", msg)
        self.save_btn.setEnabled(True)

    def delete_current(self):
        if not self.current_art_id:
            self.statusBar().showMessage("No artwork selected", 2000)
            return
        if QMessageBox.question(self, "Delete", "Delete this artwork?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            c = self.conn.cursor()
            row = c.execute("SELECT filepath FROM artworks WHERE id=?", (self.current_art_id,)).fetchone()
            if row:
                try: os.remove(row[0])
                except: pass
            c.execute("DELETE FROM artworks WHERE id=?", (self.current_art_id,))
            self.conn.commit()
            self.current_art_id = None
            self.current_tags.clear()
            self.image_label.setText("Paste or select an image")
            self.name_input.clear()
            self.artist_input.clear()
            self.load_tags()
            self.search_art()

    def clear_all(self):
        if QMessageBox.question(self, "Wipe All", "Permanently delete ALL tags and artworks?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.conn.close()
            shutil.rmtree(self.image_dir, ignore_errors=True)
            os.remove(self.db_path)
            self.current_tags.clear()
            self.current_art_id = None
            os.makedirs(self.image_dir, exist_ok=True)
            self.init_db()
            self.load_tags()
            self.results_list.clear()
            self.image_label.setText("Paste or select an image")
            self.name_input.clear()
            self.artist_input.clear()

    def copy_current(self):
        if self.current_image:
            cb = QApplication.clipboard()
            cb.setImage(self.current_image)
            self.statusBar().showMessage("Image copied to clipboard", 2000)

    def search_art(self):
        terms = [t for t in self.search_input.text().strip().lower().split() if t]
        self.results_list.clear()
        c = self.conn.cursor()
        for row in c.execute("SELECT id, name, filepath, artist, tags FROM artworks"):
            art_id, name, path, artist, tags = row
            name_val = (name or "").lower()
            artist_val = (artist or "").lower()
            tag_vals = [t.lower() for t in tags.split(',')] if tags else []
            # match if all terms present in any field
            if all(any(term in field for field in [name_val, artist_val] + tag_vals) for term in terms):
                icon = QIcon(QPixmap(path).scaled(64,64,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                item = QListWidgetItem(icon, name or os.path.basename(path))
                item.setData(Qt.UserRole, row)
                self.results_list.addItem(item)
        # show all if empty search
        if not terms:
            self.results_list.clear()
            for row in c.execute("SELECT id, name, filepath, artist, tags FROM artworks"):
                art_id, name, path, artist, tags = row
                icon = QIcon(QPixmap(path).scaled(64,64,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                item = QListWidgetItem(icon, name or os.path.basename(path))
                item.setData(Qt.UserRole, row)
                self.results_list.addItem(item)

    def open_art(self, item):
        art_id, name, path, artist, tags = item.data(Qt.UserRole)
        pix = QPixmap(path)
        self.current_image = pix.toImage()
        QApplication.clipboard().setPixmap(pix)
        self.display_image(pix)
        self.current_art_id = art_id
        self.name_input.setText(name)
        self.artist_input.setText(artist)
        self.current_tags = set(tags.split(',')) if tags else set()
        self.load_tags()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_image:
            pix = QPixmap.fromImage(self.current_image)
            self.display_image(pix)

    def display_image(self, pix):
        self.image_label.setPixmap(pix.scaled(
            self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def replace_image(self):
        # Paste into existing art, preserve metadata
        if not self.current_art_id:
            return
        cb = QApplication.clipboard()
        if cb.mimeData().hasImage():
            img = cb.image()
            self.current_image = img
            pix = QPixmap.fromImage(img)
            self.display_image(pix)
            # update DB file for this art
            fname = f"art_{int(time.time())}.png"
            path = os.path.join(self.image_dir, fname)
            img.save(path)
            c = self.conn.cursor()
            # get old filepath
            old = c.execute("SELECT filepath FROM artworks WHERE id=?", (self.current_art_id,)).fetchone()[0]
            c.execute("UPDATE artworks SET filepath=?, timestamp=CURRENT_TIMESTAMP WHERE id=?", (path, self.current_art_id))
            self.conn.commit()
            try: os.remove(old)
            except: pass
        else:
            self.statusBar().showMessage("No image in clipboard to replace", 2000)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon('icon.ico'))
    window = ArtManager()
    window.show()
    sys.exit(app.exec_())