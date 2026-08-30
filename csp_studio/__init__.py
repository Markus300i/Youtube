"""CSP Studio domain layer built on top of the existing CSP Automation renderer."""

from .agent_one import AgentOne, AgentOneReport, ReadinessCheck
from .asset_manager import AssetManager
from .models import Asset, Project, Scene, SceneRevision, ShotPlan
from .opencut_adapter import build_manifest as build_opencut_manifest
from .opencut_adapter import export_manifest as export_opencut_manifest
from .scene_ops import SceneOperations
from .shot_director import ShotAudit, ShotDirector
from .store import StudioStore
from .task_engine import StudioTask, TaskEngine

__all__ = [
    "AgentOne",
    "AgentOneReport",
    "ReadinessCheck",
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
    "MemoryItem",
    "MemoryMatch",
    "UniverseMemory",
    "VisualQA",
    "VisualQAReport",
    "VisualSceneNote",
    "build_opencut_manifest",
    "export_opencut_manifest",
]


def __getattr__(name: str):
    # Modules that also expose a `python -m csp_studio.<module>` CLI are imported
    # lazily. Eagerly importing them here makes runpy find the target module in
    # sys.modules before executing it and produces a misleading RuntimeWarning.
    if name in {"MemoryItem", "MemoryMatch", "UniverseMemory"}:
        from .universe_memory import MemoryItem, MemoryMatch, UniverseMemory

        return {
            "MemoryItem": MemoryItem,
            "MemoryMatch": MemoryMatch,
            "UniverseMemory": UniverseMemory,
        }[name]
    if name in {"VisualQA", "VisualQAReport", "VisualSceneNote"}:
        from .visual_qa import VisualQA, VisualQAReport, VisualSceneNote

        return {
            "VisualQA": VisualQA,
            "VisualQAReport": VisualQAReport,
            "VisualSceneNote": VisualSceneNote,
        }[name]
    raise AttributeError(name)
