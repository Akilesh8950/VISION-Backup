from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(StrictModel):
    pass


class WebSearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)


class GetNewsArgs(StrictModel):
    topic: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=20)


class WeatherUnits(str):
    pass


class GetWeatherArgs(StrictModel):
    city: str = Field(min_length=1, max_length=200)
    units: Literal["metric", "imperial"] = "metric"


class GetStockPriceArgs(StrictModel):
    ticker: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.^=\-]+$")


class GetCryptoPriceArgs(StrictModel):
    symbol: str = Field(min_length=1, max_length=30)
    currency: str = Field(default="USD", min_length=3, max_length=5, pattern=r"^[A-Za-z]+$")


class LookupDocsArgs(StrictModel):
    library: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=500)


class RunPythonArgs(StrictModel):
    code: str = Field(min_length=1, max_length=20_000)


class ReadFileArgs(StrictModel):
    path: str = Field(min_length=1, max_length=1000)


class WriteFileArgs(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=1_000_000)


class ListFilesArgs(StrictModel):
    directory: str = Field(min_length=1, max_length=1000)


class SearchCodeArgs(StrictModel):
    pattern: str = Field(min_length=1, max_length=500)
    path: str = Field(min_length=1, max_length=1000)


class MemoryGetArgs(StrictModel):
    key: str = Field(min_length=1, max_length=200)


class MemorySetArgs(StrictModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=10_000)


Cell = str | int | float | bool | None


class UiTableArgs(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    columns: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[Cell]] = Field(max_length=200)

    @model_validator(mode="after")
    def _row_widths(self) -> UiTableArgs:
        for index, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(f"row {index} has {len(row)} values; expected {len(self.columns)}")
        return self


class UiChartArgs(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    chart_type: Literal["bar", "line", "pie", "scatter"]
    labels: list[str] = Field(min_length=1, max_length=200)
    values: list[float] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _length_and_ranges(self) -> UiChartArgs:
        if len(self.labels) != len(self.values):
            raise ValueError("labels and values must have the same length")
        if self.chart_type == "pie" and any(value < 0 for value in self.values):
            raise ValueError("pie values must be non-negative")
        return self


class UiCard(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    value: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=2000)
    footer: str | None = Field(default=None, max_length=500)


class UiCardsArgs(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    cards: list[UiCard] = Field(min_length=1, max_length=12)


class UiTimelineEvent(StrictModel):
    time: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)


class UiTimelineArgs(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    events: list[UiTimelineEvent] = Field(min_length=1, max_length=50)


class UiCodeDiffArgs(StrictModel):
    filename: str = Field(min_length=1, max_length=500)
    before: str = Field(max_length=200_000)
    after: str = Field(max_length=200_000)


class OpenAppArgs(StrictModel):
    app_name: str = Field(min_length=1, max_length=200)


class FocusWindowArgs(StrictModel):
    title_substring: str = Field(min_length=1, max_length=300)


class OpenPathArgs(StrictModel):
    path: str = Field(min_length=1, max_length=1000)


class OpenUrlArgs(StrictModel):
    url: str = Field(min_length=8, max_length=4000)

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("only http:// and https:// URLs are allowed")
        return value


class ClipboardSetArgs(StrictModel):
    text: str = Field(max_length=100_000)


class SetTimerArgs(StrictModel):
    minutes: float = Field(gt=0, le=1440)
    label: str = Field(min_length=1, max_length=300)


class ReadUrlArgs(StrictModel):
    url: str = Field(min_length=8, max_length=4000)
    max_chars: int = Field(default=12_000, ge=500, le=50_000)

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("only http:// and https:// URLs are allowed")
        return value


class SkillLoadArgs(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


ORIGINAL_27_TOOLS = [
    "web_search", "get_news", "get_weather", "get_stock_price", "get_crypto_price", "lookup_docs",
    "run_python", "read_file", "write_file", "list_files", "search_code",
    "memory_get", "memory_set",
    "ui_table", "ui_chart", "ui_cards", "ui_timeline", "ui_code_diff",
    "list_running_apps", "open_app", "focus_window", "open_path", "open_url",
    "clipboard_get", "clipboard_set", "system_status", "set_timer",
]

TOOL_MODELS: dict[str, type[BaseModel]] = {
    "web_search": WebSearchArgs,
    "get_news": GetNewsArgs,
    "get_weather": GetWeatherArgs,
    "get_stock_price": GetStockPriceArgs,
    "get_crypto_price": GetCryptoPriceArgs,
    "lookup_docs": LookupDocsArgs,
    "run_python": RunPythonArgs,
    "read_file": ReadFileArgs,
    "write_file": WriteFileArgs,
    "list_files": ListFilesArgs,
    "search_code": SearchCodeArgs,
    "memory_get": MemoryGetArgs,
    "memory_set": MemorySetArgs,
    "ui_table": UiTableArgs,
    "ui_chart": UiChartArgs,
    "ui_cards": UiCardsArgs,
    "ui_timeline": UiTimelineArgs,
    "ui_code_diff": UiCodeDiffArgs,
    "list_running_apps": EmptyArgs,
    "open_app": OpenAppArgs,
    "focus_window": FocusWindowArgs,
    "open_path": OpenPathArgs,
    "open_url": OpenUrlArgs,
    "clipboard_get": EmptyArgs,
    "clipboard_set": ClipboardSetArgs,
    "system_status": EmptyArgs,
    "set_timer": SetTimerArgs,
    "read_url": ReadUrlArgs,
    "skill_load": SkillLoadArgs,
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    "web_search": "Search the web for current information.",
    "get_news": "Get recent news headlines for a topic.",
    "get_weather": "Get current weather for a city.",
    "get_stock_price": "Get the latest available stock quote for a ticker.",
    "get_crypto_price": "Get a cryptocurrency price in a fiat currency.",
    "lookup_docs": "Find documentation results for a library and topic.",
    "run_python": "Run short Python code after user approval.",
    "read_file": "Read a UTF-8 text file, subject to the path safety policy.",
    "write_file": "Write a UTF-8 text file, subject to confirmation and sandbox policy.",
    "list_files": "List files in a directory.",
    "search_code": "Search text/code under a path using a regular expression.",
    "memory_get": "Read persistent VISION memory by key.",
    "memory_set": "Store persistent VISION memory by key.",
    "ui_table": "Render a native sortable table in the chat.",
    "ui_chart": "Render a native bar, line, pie, or scatter chart in the chat.",
    "ui_cards": "Render native information cards in the chat.",
    "ui_timeline": "Render a native vertical timeline in the chat.",
    "ui_code_diff": "Render a native side-by-side code diff in the chat.",
    "list_running_apps": "List currently running desktop applications/processes.",
    "open_app": "Open a known or user-named application.",
    "focus_window": "Bring a window matching a title substring to the foreground.",
    "open_path": "Open a file or folder using the operating system, subject to confirmation.",
    "open_url": "Open an HTTP or HTTPS URL in the default browser.",
    "clipboard_get": "Read text from the clipboard.",
    "clipboard_set": "Write text to the clipboard, subject to confirmation for large content.",
    "system_status": "Report CPU, memory, disk, and available GPU status.",
    "set_timer": "Set an in-app timer while VISION is running.",
    "read_url": "Fetch and extract readable text from a public HTTP/HTTPS page for citation.",
    "skill_load": "Load the full instructions for an enabled portable skill by name.",
}


def clean_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def openai_tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": clean_json_schema(TOOL_MODELS[name]),
        },
    }
