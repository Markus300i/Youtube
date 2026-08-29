"""CSP Studio domain layer built on top of the existing CSP Automation renderer."""

from .asset_manager import AssetManager
from .models import Asset, Project, Scene, SceneRevision, ShotPlan
from .opencut_adapter import build_manifest as build_opencut_manifest
from .opencut_adapter import export_manifest as export_opencut_manifest
from .scene_ops import SceneOperations
from .shot_director import ShotAudit, ShotDirector
from .store import StudioStore
from .task_engine import StudioTask, TaskEngine

__all__ = [
    "Asset",
    "AssetManager",
    "Project",
    "Scene",
    "SceneOperations",
    "SceneRevision",
    "ShotPlan",
    "ShotDirector",
    "ShotAudit",
    "StudioStore",
    "StudioTask",
    "TaskEngine",
    "build_opencut_manifest",
    "export_opencut_manifest",
]
