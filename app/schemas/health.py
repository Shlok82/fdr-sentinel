from pydantic import BaseModel
from typing import Optional


class HealthResponse(BaseModel):
    status: str = "ok"
