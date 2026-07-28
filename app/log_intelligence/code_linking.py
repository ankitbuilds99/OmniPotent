"""Log-to-code linking.

Links log entries to the source code that generated them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.log_intelligence.models import LogEntry

log = logging.getLogger("devcopilot.log_intelligence.code_linking")


@dataclass
class CodeLocation:
    """A location in source code."""

    file_path: str
    line_number: int
    function_name: str
    class_name: str = ""
    repository: str = ""
    commit_sha: str = ""
    code_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "function_name": self.function_name,
            "class_name": self.class_name,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "code_snippet": self.code_snippet,
        }


@dataclass
class LogCodeLink:
    """Link between a log entry and source code."""

    log_id: str
    location: CodeLocation
    confidence: float
    link_type: str
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "location": self.location.to_dict(),
            "confidence": round(self.confidence, 2),
            "link_type": self.link_type,
            "evidence": self.evidence,
        }


@dataclass
class StackFrame:
    """A frame in a stack trace."""

    file_path: str
    line_number: int
    function_name: str
    code_line: str = ""
    is_application_code: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "function_name": self.function_name,
            "code_line": self.code_line,
            "is_application_code": self.is_application_code,
        }


@dataclass
class ParsedStackTrace:
    """A parsed stack trace."""

    frames: list[StackFrame]
    exception_type: str
    exception_message: str
    root_cause_frame: StackFrame | None
    language: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": [f.to_dict() for f in self.frames],
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "root_cause_frame": self.root_cause_frame.to_dict() if self.root_cause_frame else None,
            "language": self.language,
        }


class CodeLinker:
    """Links logs to source code."""

    def __init__(self) -> None:
        self._code_index: dict[str, CodeLocation] = {}
        self._service_repos: dict[str, str] = {}
        self._library_patterns = [
            r"/site-packages/",
            r"/node_modules/",
            r"/vendor/",
            r"<frozen ",
            r"java\.lang\.",
            r"java\.util\.",
            r"org\.springframework\.",
        ]

    def register_repository(self, service: str, repository: str) -> None:
        """Register a repository for a service."""
        self._service_repos[service] = repository

    def index_code_location(
        self,
        file_path: str,
        line_number: int,
        function_name: str,
        class_name: str = "",
    ) -> None:
        """Index a code location for linking."""
        key = f"{file_path}:{line_number}"
        self._code_index[key] = CodeLocation(
            file_path=file_path,
            line_number=line_number,
            function_name=function_name,
            class_name=class_name,
        )

    def link_log(self, entry: LogEntry) -> LogCodeLink | None:
        """Try to link a log entry to source code."""
        stack_trace = self._extract_stack_trace(entry.message)
        if stack_trace:
            return self._link_from_stack_trace(entry, stack_trace)

        file_line = self._extract_file_line(entry.message)
        if file_line:
            return self._link_from_file_line(entry, file_line)

        function_hint = entry.attributes.get("function")
        if function_hint:
            return self._link_from_function(entry, function_hint)

        return None

    def _extract_stack_trace(self, message: str) -> ParsedStackTrace | None:
        """Extract and parse a stack trace from a message."""
        python_pattern = r'File "([^"]+)", line (\d+), in (\w+)'
        python_matches = re.findall(python_pattern, message)

        if python_matches:
            frames: list[StackFrame] = []
            for match in python_matches:
                file_path, line_num, func_name = match
                is_app = not any(
                    re.search(pat, file_path)
                    for pat in self._library_patterns
                )
                frames.append(StackFrame(
                    file_path=file_path,
                    line_number=int(line_num),
                    function_name=func_name,
                    is_application_code=is_app,
                ))

            exception_match = re.search(r'(\w+Error|\w+Exception): (.+?)(?:\n|$)', message)
            exc_type = exception_match.group(1) if exception_match else "Unknown"
            exc_msg = exception_match.group(2) if exception_match else ""

            app_frames = [f for f in frames if f.is_application_code]
            root_cause = app_frames[-1] if app_frames else (frames[-1] if frames else None)

            return ParsedStackTrace(
                frames=frames,
                exception_type=exc_type,
                exception_message=exc_msg,
                root_cause_frame=root_cause,
                language="python",
            )

        java_pattern = r'at ([a-zA-Z0-9_.]+)\(([^:]+):(\d+)\)'
        java_matches = re.findall(java_pattern, message)

        if java_matches:
            frames = []
            for match in java_matches:
                method, file_name, line_num = match
                parts = method.rsplit(".", 1)
                func_name = parts[-1]

                is_app = not any(
                    re.search(pat, method)
                    for pat in self._library_patterns
                )

                frames.append(StackFrame(
                    file_path=file_name,
                    line_number=int(line_num),
                    function_name=func_name,
                    is_application_code=is_app,
                ))

            exception_match = re.search(r'(\w+Exception|\w+Error): (.+?)(?:\n|$)', message)
            exc_type = exception_match.group(1) if exception_match else "Unknown"
            exc_msg = exception_match.group(2) if exception_match else ""

            app_frames = [f for f in frames if f.is_application_code]
            root_cause = app_frames[0] if app_frames else (frames[0] if frames else None)

            return ParsedStackTrace(
                frames=frames,
                exception_type=exc_type,
                exception_message=exc_msg,
                root_cause_frame=root_cause,
                language="java",
            )

        return None

    def _extract_file_line(self, message: str) -> tuple[str, int] | None:
        """Extract file:line reference from message."""
        patterns = [
            r'([a-zA-Z0-9_/.-]+\.[a-z]+):(\d+)',
            r'file="([^"]+)", line=(\d+)',
            r'at ([^:]+):(\d+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                return (match.group(1), int(match.group(2)))

        return None

    def _link_from_stack_trace(
        self,
        entry: LogEntry,
        stack_trace: ParsedStackTrace,
    ) -> LogCodeLink | None:
        """Create a link from a stack trace."""
        if not stack_trace.root_cause_frame:
            return None

        frame = stack_trace.root_cause_frame
        repo = self._service_repos.get(entry.service, "")

        location = CodeLocation(
            file_path=frame.file_path,
            line_number=frame.line_number,
            function_name=frame.function_name,
            repository=repo,
        )

        evidence = [
            "Stack trace found in log message",
            f"Exception: {stack_trace.exception_type}",
            f"Root cause: {frame.file_path}:{frame.line_number}",
        ]

        return LogCodeLink(
            log_id=entry.log_id,
            location=location,
            confidence=0.95,
            link_type="stack_trace",
            evidence=evidence,
        )

    def _link_from_file_line(
        self,
        entry: LogEntry,
        file_line: tuple[str, int],
    ) -> LogCodeLink | None:
        """Create a link from a file:line reference."""
        file_path, line_number = file_line
        repo = self._service_repos.get(entry.service, "")

        key = f"{file_path}:{line_number}"
        indexed = self._code_index.get(key)

        location = CodeLocation(
            file_path=file_path,
            line_number=line_number,
            function_name=indexed.function_name if indexed else "",
            class_name=indexed.class_name if indexed else "",
            repository=repo,
        )

        return LogCodeLink(
            log_id=entry.log_id,
            location=location,
            confidence=0.85 if indexed else 0.7,
            link_type="file_reference",
            evidence=[f"File:line reference found: {file_path}:{line_number}"],
        )

    def _link_from_function(
        self,
        entry: LogEntry,
        function_name: str,
    ) -> LogCodeLink | None:
        """Create a link from a function name."""
        for key, location in self._code_index.items():
            if location.function_name == function_name:
                return LogCodeLink(
                    log_id=entry.log_id,
                    location=location,
                    confidence=0.6,
                    link_type="function_match",
                    evidence=[f"Function name match: {function_name}"],
                )

        return None

    def batch_link(self, entries: list[LogEntry]) -> dict[str, LogCodeLink]:
        """Link multiple log entries to code."""
        results: dict[str, LogCodeLink] = {}

        for entry in entries:
            link = self.link_log(entry)
            if link:
                results[entry.log_id] = link

        return results

    def get_code_context(
        self,
        location: CodeLocation,
        context_lines: int = 5,
    ) -> dict[str, Any]:
        """Get code context around a location (placeholder for git integration)."""
        return {
            "file_path": location.file_path,
            "line_number": location.line_number,
            "function_name": location.function_name,
            "context_lines": context_lines,
            "code": f"# Code at {location.file_path}:{location.line_number}",
            "note": "Actual code retrieval requires git integration",
        }


_linker: CodeLinker | None = None


def get_code_linker() -> CodeLinker:
    """Get the default code linker."""
    global _linker
    if _linker is None:
        _linker = CodeLinker()
    return _linker
