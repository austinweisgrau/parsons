from pydantic import BaseModel, validator
import datetime
from typing import Literal


class MatchStatusProcess(BaseModel):
    processName: Literal["PREPROCESS", "PREPARE", "EXPORT", "PUBLISH", "APICALL"]
    processState: Literal[
        "Unprocessed",
        "Waiting",
        "Pending",
        "Queued",
        "Processing",
        "Finished",
        "Error",
        "Stopped",
        "Exception",
    ]
    processId: str
    percentCompleted: float


class MatchStatusValidation(BaseModel):
    batchFileId: str
    statusMessage: str
    valid: bool
    linesPassed: int
    linesFailed: int
    ruleResults: list[dict]


class MatchStatusColumn(BaseModel):
    header: str
    index: int
    dataType: Literal["CHAR", "INT", "NNINT", "FLOAT", "PERCENT", "DATE", "BOOLEAN"]
    synthetic: bool
    coverage: float | None = None


class MatchStatus(BaseModel):
    id: str
    fileName: str
    created: datetime.datetime
    columns: list[MatchStatusColumn]
    process: MatchStatusProcess
    validation: MatchStatusValidation | None = None

    @validator("created", pre=True)
    def parse_created(cls, value: str) -> datetime.datetime:
        """Parse datetime format return by API."""
        result = datetime.datetime.strptime(value, "%b %d, %Y %I:%M:%S %p")
        return result

    @property
    def is_completed(self) -> bool:
        result = False
        if self.process.processState in ("Finished", "Error", "Stopped", "Exception"):
            result = True
        return result

    @property
    def completed_successfully(self) -> bool:
        result = False
        if self.process.processState == "Finished":
            result = True
        return result
