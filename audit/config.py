from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True, frozen=True)
class Settings:
    canvas_base_url: str
    canvas_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            canvas_base_url=os.environ["CANVAS_BASE_URL"],
            canvas_token=os.environ["ACCESS_TOKEN_EL"],
        )