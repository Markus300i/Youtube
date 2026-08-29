from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import Scene, ShotPlan


SHOT_SEQUENCE = (
    "wide",
    "detail",
    "medium",
    "close_up",
    "pov",
    "reveal",
    "wide",
    "twist",
)

CAMERA_SEQUENCE = (
    "slow_push",
    "static",
    "pan_right",
    "slow_pull",
    "pan_left",
    "slow_push",
    "static",
    "static",
)

PURPOSE_SEQUENCE = (
    "establish",
    "evidence",
    "character",
    "tension",
    "evidence",
    "reveal",
    "orientation_reset",
    "twist",
)


@dataclass(slots=True)
class ShotAudit:
    score: int
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


class ShotDirector:
    """Assigns a lightweight shot plan and checks visual variety.

    It deliberately does not generate prompts or images. The current YAML remains
    compatible with the renderer; this layer only gives CSP Studio structured
    production intent that can later drive prompt generation and the timeline UI.
    """

    def plan(self, scenes: Iterable[Scene]) -> list[Scene]:
        planned = list(scenes)
        total = len(planned)
        for index, scene in enumerate(planned):
            template_index = min(index, len(SHOT_SEQUENCE) - 1)
            shot_type = SHOT_SEQUENCE[template_index]
            camera = CAMERA_SEQUENCE[template_index]
            purpose = PURPOSE_SEQUENCE[template_index]

            if total != 8:
                if index == 0:
                    shot_type, purpose = "wide", "establish"
                elif index == total - 1:
                    shot_type, camera, purpose = "twist", "static", "twist"
                elif index == total - 2:
                    shot_type, purpose = "reveal", "reveal"

            scene.shot = ShotPlan(
                shot_type=shot_type,
                camera=camera,
                purpose=purpose,
                visual_anchor=(scene.continuity_refs[0] if scene.continuity_refs else None),
                motion_intensity="none" if camera == "static" else "low",
            )
        return planned

    def audit(self, scenes: Iterable[Scene]) -> ShotAudit:
        items = list(scenes)
        warnings: list[str] = []
        score = 100

        if not items:
            return ShotAudit(score=0, warnings=["Brak scen do analizy."])

        for left, right in zip(items, items[1:]):
            if left.shot.shot_type == right.shot.shot_type:
                warnings.append(
                    f"Sceny {left.scene_id} i {right.scene_id}: powtórzony shot_type "
                    f"'{left.shot.shot_type}'."
                )
                score -= 10
            if left.shot.camera == right.shot.camera and left.shot.camera != "static":
                warnings.append(
                    f"Sceny {left.scene_id} i {right.scene_id}: powtórzony ruch kamery "
                    f"'{left.shot.camera}'."
                )
                score -= 6

        first = items[0]
        if first.shot.purpose != "establish":
            warnings.append("Pierwsza scena nie pełni roli establish.")
            score -= 8

        last = items[-1]
        if last.shot.purpose != "twist":
            warnings.append("Ostatnia scena nie jest oznaczona jako twist.")
            score -= 12
        if last.shot.camera != "static":
            warnings.append("Twist powinien domyślnie dostać statyczny hold przed/po cięciu.")
            score -= 5

        if len(items) >= 6:
            recent = items[-3:]
            if len({scene.shot.purpose for scene in recent}) == 1:
                warnings.append("Ostatnie trzy sceny mają ten sam cel wizualny.")
                score -= 10

        return ShotAudit(score=max(0, score), warnings=warnings)
