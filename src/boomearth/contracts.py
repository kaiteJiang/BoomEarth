from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HandoffContract:
    status: str
    platform: str
    ratio: str
    duration_target_s: float
    word_count: int
    voice: str
    voice_provider: str
    captions: str
    caption_style: str
    visual: str
    illustration_skill: str
    archive_slug: str

    @classmethod
    def required_fields(cls) -> tuple[str, ...]:
        return (
            "status",
            "platform",
            "ratio",
            "duration_target_s",
            "word_count",
            "voice",
            "voice_provider",
            "captions",
            "caption_style",
            "visual",
            "illustration_skill",
            "archive_slug",
        )
