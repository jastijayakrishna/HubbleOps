from __future__ import annotations

import csv
import io
import re

from hubbleops.packs._protocol import (
    TelemetryIssue,
    TelemetryResult,
    WireObservation,
)

METHOD = re.compile(
    r"^google\.ads\.googleads\.(v[0-9]+)\.services\."
    r"([A-Za-z][A-Za-z0-9]*Service)\.([A-Za-z][A-Za-z0-9]*)$"
)


class GoogleAdsTelemetry:
    def parse(self, payload: str) -> TelemetryResult:
        reader = csv.reader(io.StringIO(payload), strict=True)
        try:
            rows = list(reader)
        except csv.Error as error:
            return TelemetryResult(
                observations=(),
                issues=(
                    TelemetryIssue(
                        row=max(reader.line_num, 1),
                        value="",
                        reason=f"malformed CSV: {error}",
                    ),
                ),
            )
        headers = rows[0] if rows else []
        candidates = [
            header
            for header in headers
            if header.lower() == "method" or header.lower().endswith(".method")
        ]
        normalized_headers = [header.casefold() for header in headers]
        if (
            len(candidates) != 1
            or any(not header for header in headers)
            or len(normalized_headers) != len(set(normalized_headers))
        ):
            return TelemetryResult(
                observations=(),
                issues=(
                    TelemetryIssue(
                        row=1,
                        value=",".join(headers),
                        reason="CSV must contain exactly one Method or qualified .method column",
                    ),
                ),
            )
        column = candidates[0]
        column_index = headers.index(column)
        observations: set[WireObservation] = set()
        issues: list[TelemetryIssue] = []
        for row_number, row in enumerate(rows[1:], start=2):
            if len(row) != len(headers):
                issues.append(
                    TelemetryIssue(
                        row=row_number,
                        value=",".join(row),
                        reason="CSV row has a different number of columns than the header",
                    )
                )
                continue
            value = row[column_index]
            match = METHOD.fullmatch(value.strip()) if value else None
            if match is None:
                issues.append(
                    TelemetryIssue(
                        row=row_number,
                        value=value,
                        reason="method is not a fully-qualified Google Ads API method",
                    )
                )
                continue
            observations.add(WireObservation(service=match[2], method=match[3], version=match[1]))
        return TelemetryResult(
            observations=tuple(
                sorted(observations, key=lambda item: (item.version, item.service, item.method))
            ),
            issues=tuple(issues),
        )


TELEMETRY = GoogleAdsTelemetry()
