"""Run existing Signalpost company research for a list of organization numbers."""

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.company_resolver import (  # noqa: E402
    CompanyNotFoundError,
    CompanyResolverError,
    InvalidCompanyNumberError,
    validate_norwegian_org_number,
)
from app.services.researcher import ResearcherService  # noqa: E402


RETRYABLE_STATUS = re.compile(r"\b(?:429|5\d\d)\b")
NUMBER_COLUMN_NAMES = {
    "company_number",
    "organization_number",
    "organization number",
    "organisation_number",
    "organisation number",
    "org_number",
    "orgnr",
    "org nr",
    "organisasjonsnummer",
}


def _read_json_numbers(data: Any) -> list[str]:
    if isinstance(data, dict):
        for key in ("company_numbers", "organization_numbers", "companies"):
            if key in data:
                data = data[key]
                break
        else:
            raise ValueError("JSON input must be a list or contain a company_numbers/companies list.")

    if not isinstance(data, list):
        raise ValueError("JSON input must contain a list of organization numbers.")

    numbers = []
    for item in data:
        if isinstance(item, dict):
            normalized_keys = {str(key).strip().lower(): key for key in item}
            value = next(
                (item[normalized_keys[key]] for key in NUMBER_COLUMN_NAMES if key in normalized_keys),
                None,
            )
            if value is None:
                raise ValueError("Each JSON company object must include an organization-number field.")
            item = value
        if not isinstance(item, (str, int)):
            raise ValueError("Each JSON organization number must be a string or integer.")
        numbers.append(str(item).strip())
    return numbers


def read_company_numbers(input_path: Path) -> list[str]:
    """Read one organization number per line, CSV row, or JSON array item."""
    content = input_path.read_text(encoding="utf-8-sig")
    if input_path.suffix.lower() == ".json":
        return _read_json_numbers(json.loads(content))

    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        rows = list(csv.reader(content.splitlines(), dialect))
    except csv.Error:
        rows = [[line] for line in content.splitlines()]

    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    number_column = next((i for i, name in enumerate(header) if name in NUMBER_COLUMN_NAMES), None)
    start_row = 1 if number_column is not None else 0
    number_column = 0 if number_column is None else number_column

    numbers = []
    for row in rows[start_row:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        first = row[0].strip()
        if len(row) == 1 and first.startswith("#"):
            continue
        numbers.append(row[number_column].strip() if number_column < len(row) else "")
    return numbers


def preview_inputs(company_numbers: Iterable[str]) -> dict[str, int]:
    valid = invalid = 0
    for number in company_numbers:
        try:
            validate_norwegian_org_number(number)
            valid += 1
        except InvalidCompanyNumberError:
            invalid += 1
    return {"total": valid + invalid, "valid": valid, "invalid": invalid}


def _is_transient_error(error: Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError)):
        return True
    if isinstance(error, (CompanyNotFoundError, InvalidCompanyNumberError)):
        return False
    if isinstance(error, CompanyResolverError) and "Network error" in str(error):
        return True
    return bool(RETRYABLE_STATUS.search(str(error)))


def _failure_result(company_number: str, status: str, error: Exception, duration: float) -> dict[str, Any]:
    return {
        "company_number": company_number,
        "status": status,
        "company_name": None,
        "llm_usage": None,
        "duration_seconds": round(duration, 3),
        "error_message": str(error) or type(error).__name__,
    }


async def run_batch(
    company_numbers: Iterable[str],
    researcher: ResearcherService,
    max_concurrency: int = 3,
    retries: int = 1,
    retry_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    """Research inputs concurrently, returning one ordered record per input."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1.")
    if retries < 0:
        raise ValueError("retries cannot be negative.")

    numbers = [str(number).strip() for number in company_numbers]
    started_at = datetime.now(timezone.utc)
    start_time = time.perf_counter()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def process_one(raw_number: str) -> dict[str, Any]:
        company_start = time.perf_counter()
        try:
            normalized_number = validate_norwegian_org_number(raw_number)
        except InvalidCompanyNumberError as error:
            return _failure_result(raw_number, "invalid", error, time.perf_counter() - company_start)

        async with semaphore:
            for attempt in range(retries + 1):
                try:
                    profile = await researcher.research_company(normalized_number)
                    return {
                        "company_number": raw_number,
                        "status": "success",
                        "company_name": profile.identity.name,
                        "llm_usage": getattr(profile, "_llm_usage", None),
                        "duration_seconds": round(time.perf_counter() - company_start, 3),
                        "error_message": None,
                    }
                except CompanyNotFoundError as error:
                    return _failure_result(
                        raw_number, "not_found", error, time.perf_counter() - company_start
                    )
                except Exception as error:
                    if attempt < retries and _is_transient_error(error):
                        await asyncio.sleep(retry_backoff_seconds * (2 ** attempt))
                        continue
                    return _failure_result(
                        raw_number, "failed", error, time.perf_counter() - company_start
                    )

        raise RuntimeError("Batch worker exited without producing a result.")

    results = await asyncio.gather(*(process_one(number) for number in numbers))
    finished_at = datetime.now(timezone.utc)
    successful = sum(result["status"] == "success" for result in results)
    return {
        "batch_started_at": started_at.isoformat(),
        "batch_finished_at": finished_at.isoformat(),
        "total_inputs": len(numbers),
        "successful": successful,
        "failed": len(numbers) - successful,
        "results": results,
        "total_duration_seconds": round(time.perf_counter() - start_time, 3),
    }


def make_report(batch: dict[str, Any]) -> dict[str, Any]:
    failures: dict[str, int] = {}
    llm_provider_counts: dict[str, int] = {}
    llm_fallback_companies = 0
    for result in batch["results"]:
        if result["status"] != "success":
            reason = result["error_message"] or result["status"]
            failures[reason] = failures.get(reason, 0) + 1
        llm_usage = result.get("llm_usage") or {}
        provider = llm_usage.get("provider_used")
        if provider and provider != "unknown":
            llm_provider_counts[provider] = llm_provider_counts.get(provider, 0) + 1
        if llm_usage.get("fallback_used"):
            llm_fallback_companies += 1
    total = batch["total_inputs"]
    return {
        "total_companies": total,
        "successful_companies": batch["successful"],
        "failed_companies": batch["failed"],
        "total_duration_seconds": batch["total_duration_seconds"],
        "average_duration_seconds": (
            round(sum(result["duration_seconds"] for result in batch["results"]) / total, 3)
            if total else 0
        ),
        "failure_reasons": failures,
        "llm_provider_counts": llm_provider_counts,
        "llm_fallback_companies": llm_fallback_companies,
    }


def write_batch_outputs(batch: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "batch_results.json"
    report_path = output_dir / "batch_report.json"
    results_path.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(
        json.dumps(make_report(batch), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return results_path, report_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research a list of Norwegian organization numbers.")
    try:
        default_concurrency = int(os.environ.get("MAX_CONCURRENCY", "3"))
    except ValueError as error:
        parser.error("MAX_CONCURRENCY must be a positive integer.")
    parser.add_argument("--input", required=True, type=Path, help="Input .txt, .csv, or .json file.")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=default_concurrency,
        help="Maximum simultaneous company research jobs (default: MAX_CONCURRENCY or 3).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        choices=range(4),
        default=1,
        help="Retries for transient failures only (0-3, default: 1).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without researching or writing output.")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"), help="Batch result directory.")
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    if args.max_concurrency < 1:
        print("--max-concurrency must be at least 1.", file=sys.stderr)
        return 2
    try:
        numbers = read_company_numbers(args.input)
    except (OSError, ValueError, json.JSONDecodeError, csv.Error) as error:
        print(f"Could not read input: {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        preview = preview_inputs(numbers)
        print(
            f"Dry run: {preview['total']} inputs; {preview['valid']} valid and "
            f"{preview['invalid']} invalid. No research was performed."
        )
        return 0

    batch = await run_batch(
        numbers,
        ResearcherService(),
        max_concurrency=args.max_concurrency,
        retries=args.retries,
    )
    results_path, report_path = write_batch_outputs(batch, args.output_dir)
    print(
        f"Batch complete: {batch['successful']} successful, {batch['failed']} failed "
        f"of {batch['total_inputs']} inputs in {batch['total_duration_seconds']:.2f}s."
    )
    print(f"Results: {results_path}\nReport: {report_path}")
    return 0 if batch["failed"] == 0 else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
