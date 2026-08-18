"""Pipeline exception types.

Hard rule 5: OOM fails loud. It never degrades to CPU offload silently.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for everything this pipeline raises deliberately."""


class ConfigError(PipelineError):
    """Malformed or missing configuration."""


class WorkflowError(PipelineError):
    """A ComfyUI workflow could not be loaded or patched."""


class GenerationError(PipelineError):
    """A generation job failed on the ComfyUI side."""


class VRAMError(GenerationError):
    """Out of VRAM.

    Carries the device, model and resolution so the failure is actionable from
    the log line alone -- see hard rule 5.
    """

    def __init__(self, *, device: str, model: str, resolution: tuple[int, int], detail: str):
        self.device = device
        self.model = model
        self.resolution = resolution
        self.detail = detail
        super().__init__(
            f"CUDA OOM on {device} running {model} at {resolution[0]}x{resolution[1]}: {detail}. "
            f"Lower the resolution or clip length in config/pipeline.toml, or route this shot "
            f"to the other queue. Not falling back to CPU offload."
        )


class QCError(PipelineError):
    """QC could not reach a verdict. Never resolves to 'approved'."""


class AssemblyError(PipelineError):
    """Final render failed."""


class DisclosureError(PipelineError):
    """Hard rule 3: a publishable artifact was produced without AI disclosure."""
