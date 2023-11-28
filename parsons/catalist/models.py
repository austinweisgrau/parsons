from dataclasses import dataclass


@dataclass
class MatchStatus:
    id: str
    fileName: str
    created: str
    columns: list
    process: dict
    validation: dict

    def is_completed(self) -> bool:
        if self.status in ("Finished", "Error", "Stopped", "Exception"):
            result = True
        else:
            result = False

        return result

    @property
    def status(self) -> str:
        result = self.process["processState"]
        return result
