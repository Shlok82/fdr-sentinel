from pydantic import BaseModel
from typing import Optional


class HealthResponse(BaseModel):
    status: str
    database: str
    app_name: str
    environment: str
    timestamp: str
    version: str = "1.0.0"
