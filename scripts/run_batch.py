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
from app.services.llm_service import LLMRateLimitError  # noqa: E402
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


def _is_resumable_result(result: dict[str, Any]) -> bool:
    """Check if a previous result represents a genuinely completed research evaluation."""
    status = result.get("status")
    if status in ("not_found", "invalid"):
        return True
    if status == "success":
        llm_usage = result.get("llm_usage") or {}
        # If fallback was used (e.g. MockLLM fallback), it was not evaluated by the real LLM
        if llm_usage.get("fallback_used"):
            return False
        # If provider_used is mock when groq was configured, not a real LLM success
        if llm_usage.get("provider_used") == "mock" and llm_usage.get("configured_provider") != "mock":
            return False
        return True
    return False


async def run_batch(
    company_numbers: Iterable[str],
    researcher: ResearcherService,
    max_concurrency: int = 1,
    retries: int = 1,
    retry_backoff_seconds: float = 1.0,
    output_dir: Path | None = None,
    resume: bool = False,
    stop_on_rate_limit: bool = False,
) -> dict[str, Any]:
    """Research inputs with bounded concurrency, optional resume, and incremental saving."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1.")
    if retries < 0:
        raise ValueError("retries cannot be negative.")

    numbers = [str(number).strip() for number in company_numbers]
    started_at = datetime.now(timezone.utc)
    start_time = time.perf_counter()
    semaphore = asyncio.Semaphore(max_concurrency)
    stop_event = asyncio.Event()
    save_lock = asyncio.Lock()

    existing_results: dict[str, dict[str, Any]] = {}
    if resume and output_dir:
        results_file = output_dir / "batch_results.json"
        if results_file.exists():
            try:
                prev_batch = json.loads(results_file.read_text(encoding="utf-8"))
                for r in prev_batch.get("results", []):
                    if _is_resumable_result(r):
                        existing_results[r["company_number"]] = r
            except Exception:
                pass

    interim_results: dict[str, dict[str, Any]] = {}
    # Pre-populate ONLY with genuinely completed results if resuming
    for num in numbers:
        if num in existing_results:
            interim_results[num] = existing_results[num]

    async def save_progress():
        if output_dir:
            async with save_lock:
                completed = [interim_results[n] for n in numbers if n in interim_results]
                successful = sum(r.get("status") == "success" for r in completed)
                failed = sum(r.get("status") not in ("success",) for r in completed)
                interim_batch = {
                    "batch_started_at": started_at.isoformat(),
                    "batch_finished_at": datetime.now(timezone.utc).isoformat(),
                    "total_inputs": len(numbers),
                    "successful": successful,
                    "failed": failed,
                    "results": completed,
                    "total_duration_seconds": round(time.perf_counter() - start_time, 3),
                }
                write_batch_outputs(interim_batch, output_dir)

    async def process_one(raw_number: str) -> dict[str, Any]:
        # Check if already genuinely completed from a previous run.
        if resume and raw_number in existing_results:
            return existing_results[raw_number]

        if stop_event.is_set():
            res = _failure_result(
                raw_number,
                "rate_limited",
                RuntimeError("Batch halted due to Groq rate limit on earlier company."),
                0.0,
            )
            interim_results[raw_number] = res
            await save_progress()
            return res

        try:
            normalized_number = validate_norwegian_org_number(raw_number)
        except InvalidCompanyNumberError as error:
            res = _failure_result(raw_number, "invalid", error, 0.0)
            interim_results[raw_number] = res
            await save_progress()
            return res

        async with semaphore:
            if stop_event.is_set():
                res = _failure_result(
                    raw_number,
                    "rate_limited",
                    RuntimeError("Batch halted due to Groq rate limit on earlier company."),
                    0.0,
                )
                interim_results[raw_number] = res
                await save_progress()
                return res

            company_start = time.perf_counter()

            for attempt in range(retries + 1):
                try:
                    profile = await researcher.research_company(normalized_number)
                    llm_usage = getattr(profile, "_llm_usage", None) or {}
                    fallback_used = bool(llm_usage.get("fallback_used"))
                    fallback_reason = str(llm_usage.get("fallback_reason") or "")

                    # When --stop-on-rate-limit is active and LLMService fell back to
                    # MockLLM because Groq returned 429, the result must NOT be saved as
                    # "success". A "success" result is permanently skipped on --resume,
                    # but this company was never researched by the real LLM.
                    # Save it as "rate_limited" so it is re-researched on next --resume.
                    if stop_on_rate_limit and fallback_used and (
                        "429" in fallback_reason or "rate limit" in fallback_reason.lower()
                    ):
                        stop_event.set()
                        res = _failure_result(
                            raw_number,
                            "rate_limited",
                            RuntimeError(
                                f"Groq 429 — LLM fell back to MockLLM; "
                                f"will be re-researched on next --resume run. "
                                f"Original reason: {fallback_reason}"
                            ),
                            time.perf_counter() - company_start,
                        )
                        # Preserve available company identity for the report
                        res["company_name"] = profile.identity.name
                        res["llm_usage"] = llm_usage
                        interim_results[raw_number] = res
                        await save_progress()
                        return res

                    res = {
                        "company_number": raw_number,
                        "status": "success",
                        "company_name": profile.identity.name,
                        "llm_usage": getattr(profile, "_llm_usage", None),
                        "duration_seconds": round(time.perf_counter() - company_start, 3),
                        "error_message": None,
                    }
                    interim_results[raw_number] = res
                    await save_progress()
                    return res
                except CompanyNotFoundError as error:
                    res = _failure_result(
                        raw_number, "not_found", error, time.perf_counter() - company_start
                    )
                    interim_results[raw_number] = res
                    await save_progress()
                    return res
                except LLMRateLimitError as error:
                    if stop_on_rate_limit:
                        stop_event.set()
                    res = _failure_result(
                        raw_number, "rate_limited", error, time.perf_counter() - company_start
                    )
                    interim_results[raw_number] = res
                    await save_progress()
                    return res
                except Exception as error:
                    err_str = str(error).lower()
                    if stop_on_rate_limit and ("429" in err_str or "rate limit" in err_str):
                        stop_event.set()
                        res = _failure_result(
                            raw_number, "rate_limited", error, time.perf_counter() - company_start
                        )
                        interim_results[raw_number] = res
                        await save_progress()
                        return res

                    if attempt < retries and _is_transient_error(error):
                        await asyncio.sleep(retry_backoff_seconds * (2 ** attempt))
                        continue

                    res = _failure_result(
                        raw_number, "failed", error, time.perf_counter() - company_start
                    )
                    interim_results[raw_number] = res
                    await save_progress()
                    return res

        raise RuntimeError("Batch worker exited without producing a result.")

    results = await asyncio.gather(*(process_one(number) for number in numbers))
    finished_at = datetime.now(timezone.utc)
    successful = sum(result["status"] == "success" for result in results)
    batch_output = {
        "batch_started_at": started_at.isoformat(),
        "batch_finished_at": finished_at.isoformat(),
        "total_inputs": len(numbers),
        "successful": successful,
        "failed": len(numbers) - successful,
        "results": results,
        "total_duration_seconds": round(time.perf_counter() - start_time, 3),
    }
    if output_dir:
        write_batch_outputs(batch_output, output_dir)
    return batch_output


def make_report(batch: dict[str, Any]) -> dict[str, Any]:
    failures: dict[str, int] = {}
    llm_provider_counts: dict[str, int] = {}
    llm_fallback_companies = 0
    groq_successful_companies = 0
    rate_limited_companies = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    genuine_llm_successes_with_token_usage = 0

    for result in batch["results"]:
        status = result.get("status")
        if status == "rate_limited":
            rate_limited_companies += 1
        if status != "success":
            reason = result.get("error_message") or status
            failures[reason] = failures.get(reason, 0) + 1
        llm_usage = result.get("llm_usage") or {}
        provider = llm_usage.get("provider_used")
        if provider and provider != "unknown":
            llm_provider_counts[provider] = llm_provider_counts.get(provider, 0) + 1
        if status == "success" and llm_usage.get("fallback_used"):
            llm_fallback_companies += 1
        elif status == "success" and provider == "groq":
            groq_successful_companies += 1
        if (
            status == "success"
            and not llm_usage.get("fallback_used")
            and provider not in (None, "unknown", "mock")
        ):
            prompt_tokens += _reported_token_count(llm_usage.get("prompt_tokens")) or 0
            completion_tokens += _reported_token_count(llm_usage.get("completion_tokens")) or 0
            reported_total_tokens = _reported_token_count(llm_usage.get("total_tokens"))
            total_tokens += reported_total_tokens or 0
            if reported_total_tokens is not None:
                genuine_llm_successes_with_token_usage += 1

    total = batch["total_inputs"]
    return {
        "total_companies": total,
        "successful_companies": batch["successful"],
        "groq_successful_companies": groq_successful_companies,
        "llm_fallback_companies": llm_fallback_companies,
        "rate_limited_companies": rate_limited_companies,
        "failed_companies": batch["failed"],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "genuine_llm_successes_with_token_usage": genuine_llm_successes_with_token_usage,
        "average_total_tokens_per_genuine_llm_success": (
            round(total_tokens / genuine_llm_successes_with_token_usage, 3)
            if genuine_llm_successes_with_token_usage else None
        ),
        "total_duration_seconds": batch["total_duration_seconds"],
        "average_duration_seconds": (
            round(sum(result["duration_seconds"] for result in batch["results"]) / total, 3)
            if total else 0
        ),
        "failure_reasons": failures,
        "llm_provider_counts": llm_provider_counts,
    }


def _reported_token_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


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
        default_concurrency = int(os.environ.get("MAX_CONCURRENCY", "1"))
    except ValueError:
        parser.error("MAX_CONCURRENCY must be a positive integer.")
    parser.add_argument("--input", required=True, type=Path, help="Input .txt, .csv, or .json file.")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=default_concurrency,
        help="Maximum simultaneous company research jobs (default: MAX_CONCURRENCY or 1).",
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
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted batch using previously completed records from output-dir.",
    )
    parser.add_argument(
        "--stop-on-rate-limit",
        action="store_true",
        help="Halt remaining batch immediately if Groq returns 429 rate limit to avoid wasting quota.",
    )
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
        output_dir=args.output_dir,
        resume=args.resume,
        stop_on_rate_limit=args.stop_on_rate_limit,
    )
    results_path, report_path = write_batch_outputs(batch, args.output_dir)
    report = make_report(batch)
    print(
        f"Batch complete: {batch['successful']} successful "
        f"({report['groq_successful_companies']} via Groq, {report['llm_fallback_companies']} via MockLLM fallback), "
        f"{batch['failed']} failed of {batch['total_inputs']} inputs in {batch['total_duration_seconds']:.2f}s."
    )
    print(f"Results: {results_path}\nReport: {report_path}")
    return 0 if batch["failed"] == 0 else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
