"""Context file loader — scans context/ directory for project specification files.

Handles text files (specs, architecture, style, tech-stack, references),
image files (mockups), and binary assets (sprites, sounds, fonts) in
context/assets/.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adam.types import ContextType

# Filename patterns → context type inference
_FILENAME_TYPE_MAP: list[tuple[list[str], ContextType]] = [
    (["spec", "requirements", "features", "stories", "acceptance"], ContextType.SPEC),
    (["architecture", "design", "system", "structure"], ContextType.ARCHITECTURE),
    (["style", "conventions", "coding", "lint", "format"], ContextType.STYLE),
    (["tech", "stack", "dependencies", "framework", "language"], ContextType.TECH_STACK),
    (["reference", "api", "docs", "documentation", "notes"], ContextType.REFERENCE),
]

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac"}
_FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
_ASSET_EXTENSIONS = _IMAGE_EXTENSIONS | _AUDIO_EXTENSIONS | _FONT_EXTENSIONS
_TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml"}


@dataclass
class ContextFile:
    """A loaded context file."""
    path: Path
    context_type: ContextType
    content: str = ""  # Text content; empty for images/assets
    is_image: bool = False
    frontmatter: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem


@dataclass
class AssetEntry:
    """A binary asset discovered in context/assets/."""
    source_path: Path  # Absolute path in context/assets/
    relative_path: str  # Relative to assets/ dir (e.g. "towers/tower-base.png")
    filename: str  # Just the filename (e.g. "tower-base.png")
    category: str  # Inferred: "image", "audio", "font"
    size_bytes: int = 0


@dataclass
class AssetManifest:
    """Inventory of all binary assets available for the project."""
    assets: list[AssetEntry] = field(default_factory=list)

    @property
    def image_assets(self) -> list[AssetEntry]:
        return [a for a in self.assets if a.category == "image"]

    @property
    def audio_assets(self) -> list[AssetEntry]:
        return [a for a in self.assets if a.category == "audio"]

    @property
    def filenames(self) -> list[str]:
        """All asset filenames — useful for passing to agents."""
        return [a.filename for a in self.assets]

    @property
    def by_directory(self) -> dict[str, list[str]]:
        """Assets grouped by subdirectory."""
        groups: dict[str, list[str]] = {}
        for a in self.assets:
            parts = a.relative_path.rsplit("/", 1)
            dirname = parts[0] if len(parts) > 1 else ""
            groups.setdefault(dirname, []).append(a.filename)
        return groups

    def summary(self) -> str:
        """Human-readable summary for including in agent prompts."""
        if not self.assets:
            return "No assets available."

        images = self.image_assets
        audio = self.audio_assets

        lines = [
            f"{len(self.assets)} asset(s) available in public/assets/.",
            "These are INDIVIDUAL files (not a sprite atlas or sprite sheet).",
            "Load each file separately by its filename.",
        ]

        if images:
            extensions = {a.filename.rsplit(".", 1)[-1] for a in images}
            lines.append(
                f"  Images: {len(images)} files ({', '.join(sorted(extensions))} format)"
            )
        if audio:
            lines.append(f"  Audio: {len(audio)} files")

        for dirname, files in sorted(self.by_directory.items()):
            label = dirname or "(root)"
            lines.append(f"  {label}/ ({len(files)} files)")
            for f in sorted(files)[:10]:
                lines.append(f"    - {f}")
            if len(files) > 10:
                lines.append(f"    ... and {len(files) - 10} more")
        return "\n".join(lines)


@dataclass
class ContextManifest:
    """Summary of loaded context files and assets."""
    files: list[ContextFile]
    types_present: set[ContextType] = field(default_factory=set)
    assets: AssetManifest = field(default_factory=AssetManifest)

    def files_of_type(self, ct: ContextType) -> list[ContextFile]:
        return [f for f in self.files if f.context_type == ct]

    def has_type(self, ct: ContextType) -> bool:
        return ct in self.types_present

    @property
    def has_assets(self) -> bool:
        return len(self.assets.assets) > 0


class ContextLoader:
    def __init__(self, context_dir: Path) -> None:
        self._context_dir = context_dir
        self._files: list[ContextFile] = []
        self._assets = AssetManifest()
        self._manifest: ContextManifest | None = None

    @property
    def context_dir(self) -> Path:
        return self._context_dir

    @property
    def files(self) -> list[ContextFile]:
        return self._files

    @property
    def assets(self) -> AssetManifest:
        return self._assets

    @property
    def manifest(self) -> ContextManifest:
        if self._manifest is None:
            self._manifest = ContextManifest(
                files=self._files,
                types_present={f.context_type for f in self._files},
                assets=self._assets,
            )
        return self._manifest

    def load(self) -> list[ContextFile]:
        """Scan context directory and load all recognized files."""
        self._files = []
        self._assets = AssetManifest()
        self._manifest = None

        if not self._context_dir.is_dir():
            return self._files

        for p in sorted(self._context_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue

            cf = self._load_file(p)
            if cf is not None:
                self._files.append(cf)

        # Scan assets/ subdirectory
        self._assets = self._scan_assets()

        self._manifest = ContextManifest(
            files=self._files,
            types_present={f.context_type for f in self._files},
            assets=self._assets,
        )
        return self._files

    def _scan_assets(self) -> AssetManifest:
        """Scan context/assets/ for binary project assets."""
        assets_dir = self._context_dir / "assets"
        if not assets_dir.is_dir():
            return AssetManifest()

        entries: list[AssetEntry] = []
        for p in sorted(assets_dir.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue

            suffix = p.suffix.lower()
            if suffix not in _ASSET_EXTENSIONS:
                continue

            if suffix in _IMAGE_EXTENSIONS:
                category = "image"
            elif suffix in _AUDIO_EXTENSIONS:
                category = "audio"
            else:
                category = "font"

            relative = str(p.relative_to(assets_dir))
            entries.append(AssetEntry(
                source_path=p,
                relative_path=relative,
                filename=p.name,
                category=category,
                size_bytes=p.stat().st_size,
            ))

        return AssetManifest(assets=entries)

    def refresh(self) -> list[ContextFile]:
        """Detect new files since last load."""
        known_paths = {f.path for f in self._files}
        new_files = []

        if not self._context_dir.is_dir():
            return new_files

        for p in sorted(self._context_dir.rglob("*")):
            if not p.is_file() or p in known_paths or p.name.startswith("."):
                continue
            cf = self._load_file(p)
            if cf is not None:
                self._files.append(cf)
                new_files.append(cf)

        if new_files:
            self._manifest = None
        return new_files

    def _load_file(self, path: Path) -> ContextFile | None:
        suffix = path.suffix.lower()

        # Skip files in assets/ — handled separately
        try:
            path.relative_to(self._context_dir / "assets")
            return None  # In assets dir, skip
        except ValueError:
            pass  # Not in assets dir, proceed

        # Image files (mockups, not assets)
        if suffix in _IMAGE_EXTENSIONS:
            return ContextFile(
                path=path,
                context_type=ContextType.MOCKUP,
                is_image=True,
                content_hash=self._hash_file(path),
            )

        # Text files
        if suffix not in _TEXT_EXTENSIONS:
            return None

        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        content, frontmatter = self._parse_frontmatter(raw)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        ct = ContextType.UNKNOWN
        if "type" in frontmatter:
            with contextlib.suppress(ValueError):
                ct = ContextType(frontmatter["type"])

        if ct == ContextType.UNKNOWN:
            ct = self._infer_type(path)

        return ContextFile(
            path=path,
            context_type=ct,
            content=content,
            frontmatter=frontmatter,
            content_hash=content_hash,
        )

    @staticmethod
    def _infer_type(path: Path) -> ContextType:
        stem_lower = path.stem.lower().replace("-", "_").replace(" ", "_")
        if "reference" in [p.name.lower() for p in path.parents]:
            return ContextType.REFERENCE

        for keywords, ct in _FILENAME_TYPE_MAP:
            for kw in keywords:
                if kw in stem_lower:
                    return ct

        return ContextType.UNKNOWN

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[str, dict[str, Any]]:
        """Parse optional YAML frontmatter delimited by ---."""
        if not raw.startswith("---"):
            return raw, {}

        parts = raw.split("---", 2)
        if len(parts) < 3:
            return raw, {}

        fm_text = parts[1].strip()
        content = parts[2].strip()

        frontmatter: dict[str, Any] = {}
        current_key: str | None = None
        current_list: list[str] | None = None

        for line in fm_text.split("\n"):
            stripped = line.strip()

            # YAML list item (e.g., "  - foo")
            if stripped.startswith("- ") and current_key is not None:
                if current_list is None:
                    current_list = []
                current_list.append(stripped[2:].strip())
                continue

            # Flush any accumulated list
            if current_list is not None and current_key is not None:
                frontmatter[current_key] = current_list
                current_list = None
                current_key = None

            # Key: value line
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if val:
                    frontmatter[key] = val
                    current_key = None
                else:
                    # Value is empty — next lines may be a YAML list
                    current_key = key

        # Flush final list
        if current_list is not None and current_key is not None:
            frontmatter[current_key] = current_list

        return content, frontmatter

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
