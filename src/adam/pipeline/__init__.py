"""Pipeline — explicit stage runner for Adam's lifecycle.

Stages: plan → scaffold → construct → refine → done.

The pipeline replaces ad-hoc phase management with explicit stage
definitions and a runner that coordinates transitions between them.
"""

from adam.pipeline.stages import Pipeline, Stage, StageResult

__all__ = ["Pipeline", "Stage", "StageResult"]
