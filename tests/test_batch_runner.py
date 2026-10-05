import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts.run_batch import (
    CompanyNotFoundError,
    _run_cli,
    make_report,
    preview_inputs,
    read_company_numbers,
    run_batch,
    write_batch_outputs,
)


VALID_NUMBERS = [
    "923609016",
    "982463718",
    "914778271",
    "984851006",
    "320000009",
]


class FakeResearcher:
    def __init__(self, failures=None, delay=0, llm_usage=None):
        self.failures = failures or {}
        self.delay = delay
        self.llm_usage = llm_usage
        self.active = 0
        self.max_active = 0
        self.calls = []

    async def research_company(self, company_number):
        self.calls.append(company_number)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            failure = self.failures.get(company_number)
            if failure:
                raise failure
            return SimpleNamespace(
                identity=SimpleNamespace(name=f"Company {company_number}"),
                _llm_usage=self.llm_usage,
            )
        finally:
            self.active -= 1


def test_five_valid_companies_produce_five_results():
    researcher = FakeResearcher()

    batch = asyncio.run(run_batch(VALID_NUMBERS, researcher))

    assert len(batch["results"]) == 5
    assert batch["total_inputs"] == 5
    assert batch["successful"] == 5
    assert batch["failed"] == 0
    assert all(result["status"] == "success" for result in batch["results"])


def test_company_failure_does_not_stop_remaining_research():
    failed_number = VALID_NUMBERS[2]
    researcher = FakeResearcher({failed_number: RuntimeError("temporary provider failure")})

    batch = asyncio.run(run_batch(VALID_NUMBERS, researcher, retries=0))

    assert [result["company_number"] for result in batch["results"]] == VALID_NUMBERS
    assert batch["results"][2]["status"] == "failed"
    assert len(researcher.calls) == 5
    assert batch["successful"] == 4
    assert batch["failed"] == 1


def test_invalid_and_not_found_inputs_are_recorded_without_stopping():
    numbers = ["not-a-number", "320000009", "923609016"]
    researcher = FakeResearcher({"320000009": CompanyNotFoundError("not in registry")})

    batch = asyncio.run(run_batch(numbers, researcher, retries=2))

    assert [result["status"] for result in batch["results"]] == ["invalid", "not_found", "success"]
    assert len(batch["results"]) == len(numbers)
    assert researcher.calls == ["320000009", "923609016"]


def test_transient_error_is_retried_but_does_not_duplicate_result():
    class FlakyResearcher(FakeResearcher):
        async def research_company(self, company_number):
            self.calls.append(company_number)
            if len(self.calls) == 1:
                raise RuntimeError("Groq API error (503): temporarily unavailable")
            return SimpleNamespace(identity=SimpleNamespace(name="Recovered company"))

    researcher = FlakyResearcher()
    batch = asyncio.run(
        run_batch([VALID_NUMBERS[0]], researcher, retries=1, retry_backoff_seconds=0)
    )

    assert len(batch["results"]) == 1
    assert batch["results"][0]["status"] == "success"
    assert len(researcher.calls) == 2


def test_concurrency_limit_is_respected():
    researcher = FakeResearcher(delay=0.01)

    asyncio.run(run_batch(VALID_NUMBERS, researcher, max_concurrency=2))

    assert researcher.max_active == 2


def test_output_has_one_record_per_input_and_human_readable_summary(tmp_path):
    researcher = FakeResearcher({VALID_NUMBERS[0]: RuntimeError("provider unavailable")})
    batch = asyncio.run(run_batch(VALID_NUMBERS, researcher, retries=0))

    results_path, report_path = write_batch_outputs(batch, tmp_path)
    saved_batch = json.loads(results_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert len(saved_batch["results"]) == len(VALID_NUMBERS)
    assert report["total_companies"] == 5
    assert report["successful_companies"] == 4
    assert report["failed_companies"] == 1
    assert report["failure_reasons"] == {"provider unavailable": 1}
    assert "average_duration_seconds" in report


def test_batch_reports_real_and_fallback_llm_usage():
    fallback_usage = {
        "configured_provider": "groq",
        "provider_used": "mock",
        "fallback_used": True,
        "fallback_reason": "RuntimeError: LLM API error (429)",
    }
    researcher = FakeResearcher(llm_usage=fallback_usage)

    batch = asyncio.run(run_batch([VALID_NUMBERS[0]], researcher))
    report = make_report(batch)

    assert batch["results"][0]["status"] == "success"
    assert batch["results"][0]["llm_usage"] == fallback_usage
    assert report["llm_provider_counts"] == {"mock": 1}
    assert report["llm_fallback_companies"] == 1


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("companies.txt", "923609016\n982463718\n", ["923609016", "982463718"]),
        (
            "companies.csv",
            "company_number,name\n923609016,Equinor\n982463718,Telenor\n",
            ["923609016", "982463718"],
        ),
        (
            "companies.json",
            '[{"company_number":"923609016"},{"company_number":"982463718"}]',
            ["923609016", "982463718"],
        ),
    ],
)
def test_input_formats_are_read(tmp_path, filename, content, expected):
    input_path = tmp_path / filename
    input_path.write_text(content, encoding="utf-8")

    assert read_company_numbers(input_path) == expected


def test_dry_run_does_not_construct_researcher_or_write_output(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "companies.txt"
    input_path.write_text("923609016\nnot-valid\n", encoding="utf-8")
    output_path = tmp_path / "artifacts"

    class UnexpectedResearcher:
        def __init__(self):
            pytest.fail("Dry run must not construct the research service.")

    monkeypatch.setattr("scripts.run_batch.ResearcherService", UnexpectedResearcher)
    args = SimpleNamespace(
        max_concurrency=3,
        input=input_path,
        dry_run=True,
        output_dir=output_path,
        retries=1,
    )

    assert asyncio.run(_run_cli(args)) == 0
    assert "1 valid and 1 invalid" in capsys.readouterr().out
    assert not output_path.exists()
    assert preview_inputs(["923609016", "invalid"]) == {"total": 2, "valid": 1, "invalid": 1}


def test_groq_success_is_skipped_on_resume(tmp_path):
    """A genuine Groq success is recorded in batch_results.json and skipped on --resume."""
    num1, num2 = VALID_NUMBERS[0], VALID_NUMBERS[1]
    groq_usage = {
        "configured_provider": "groq",
        "provider_used": "groq",
        "fallback_used": False,
        "fallback_reason": None,
    }
    # Initial run: company 1 succeeds with real Groq
    initial_batch = {
        "batch_started_at": "2026-10-05T12:00:00Z",
        "batch_finished_at": "2026-10-05T12:01:00Z",
        "total_inputs": 1,
        "successful": 1,
        "failed": 0,
        "results": [
            {
                "company_number": num1,
                "status": "success",
                "company_name": f"Company {num1}",
                "llm_usage": groq_usage,
                "duration_seconds": 1.2,
                "error_message": None,
            }
        ],
        "total_duration_seconds": 1.2,
    }
    write_batch_outputs(initial_batch, tmp_path)

    # Resume run with both companies
    researcher = FakeResearcher(llm_usage=groq_usage)
    batch = asyncio.run(
        run_batch([num1, num2], researcher, resume=True, output_dir=tmp_path)
    )

    # num1 was skipped, only num2 was researched
    assert researcher.calls == [num2]
    assert batch["successful"] == 2
    assert batch["results"][0]["company_number"] == num1
    assert batch["results"][1]["company_number"] == num2


def test_mock_fallback_on_rate_limit_stops_batch_and_marks_rate_limited(tmp_path):
    """When stop_on_rate_limit is enabled, a 429 MockLLM fallback marks company rate_limited and halts."""
    num1, num2, num3 = VALID_NUMBERS[0], VALID_NUMBERS[1], VALID_NUMBERS[2]
    fallback_usage = {
        "configured_provider": "groq",
        "provider_used": "mock",
        "fallback_used": True,
        "fallback_reason": "LLMProviderError: LLM API error (429): rate limit exceeded",
    }
    researcher = FakeResearcher(llm_usage=fallback_usage)

    batch = asyncio.run(
        run_batch(
            [num1, num2, num3],
            researcher,
            stop_on_rate_limit=True,
            output_dir=tmp_path,
        )
    )

    # First company was researched and hit rate limit, subsequent halted
    assert researcher.calls == [num1]
    assert batch["results"][0]["status"] == "rate_limited"
    assert batch["results"][1]["status"] == "rate_limited"
    assert batch["results"][2]["status"] == "rate_limited"
    assert batch["successful"] == 0
    assert batch["failed"] == 3

    report = make_report(batch)
    assert report["rate_limited_companies"] == 3
    assert report["groq_successful_companies"] == 0


def test_rate_limited_companies_are_retried_on_resume(tmp_path):
    """Rate-limited companies from a previous batch run are NOT skipped when resuming."""
    num1, num2 = VALID_NUMBERS[0], VALID_NUMBERS[1]
    prev_batch = {
        "batch_started_at": "2026-10-05T12:00:00Z",
        "batch_finished_at": "2026-10-05T12:01:00Z",
        "total_inputs": 2,
        "successful": 0,
        "failed": 2,
        "results": [
            {
                "company_number": num1,
                "status": "rate_limited",
                "company_name": f"Company {num1}",
                "llm_usage": None,
                "duration_seconds": 0.5,
                "error_message": "Groq rate limited",
            },
            {
                "company_number": num2,
                "status": "rate_limited",
                "company_name": None,
                "llm_usage": None,
                "duration_seconds": 0.0,
                "error_message": "Halted due to rate limit",
            },
        ],
        "total_duration_seconds": 0.5,
    }
    write_batch_outputs(prev_batch, tmp_path)

    # Resume run now succeeds
    groq_usage = {
        "configured_provider": "groq",
        "provider_used": "groq",
        "fallback_used": False,
        "fallback_reason": None,
    }
    researcher = FakeResearcher(llm_usage=groq_usage)
    batch = asyncio.run(
        run_batch([num1, num2], researcher, resume=True, output_dir=tmp_path)
    )

    # Both previously rate-limited companies were retried
    assert researcher.calls == [num1, num2]
    assert batch["successful"] == 2
    assert all(r["status"] == "success" for r in batch["results"])


def test_mock_fallback_without_stop_on_rate_limit_is_retried_on_resume(tmp_path):
    """A company that previously completed via MockLLM fallback is NOT skipped on --resume."""
    num1, num2 = VALID_NUMBERS[0], VALID_NUMBERS[1]
    prev_batch = {
        "batch_started_at": "2026-10-05T12:00:00Z",
        "batch_finished_at": "2026-10-05T12:01:00Z",
        "total_inputs": 2,
        "successful": 2,
        "failed": 0,
        "results": [
            {
                "company_number": num1,
                "status": "success",
                "company_name": f"Company {num1}",
                "llm_usage": {
                    "configured_provider": "groq",
                    "provider_used": "mock",
                    "fallback_used": True,
                    "fallback_reason": "429 rate limit",
                },
                "duration_seconds": 1.0,
                "error_message": None,
            },
            {
                "company_number": num2,
                "status": "success",
                "company_name": f"Company {num2}",
                "llm_usage": {
                    "configured_provider": "groq",
                    "provider_used": "groq",
                    "fallback_used": False,
                    "fallback_reason": None,
                },
                "duration_seconds": 1.0,
                "error_message": None,
            },
        ],
        "total_duration_seconds": 2.0,
    }
    write_batch_outputs(prev_batch, tmp_path)

    researcher = FakeResearcher(llm_usage={
        "configured_provider": "groq",
        "provider_used": "groq",
        "fallback_used": False,
    })
    batch = asyncio.run(
        run_batch([num1, num2], researcher, resume=True, output_dir=tmp_path)
    )

    # num1 (mock fallback) must be retried; num2 (real groq) must be skipped
    assert researcher.calls == [num1]
    assert batch["successful"] == 2


def test_company_duration_measures_active_time_not_queue_wait(tmp_path):
    """Company duration_seconds must reflect active execution time, not cumulative waiting time behind semaphore."""
    num1, num2 = VALID_NUMBERS[0], VALID_NUMBERS[1]

    class DelayResearcher(FakeResearcher):
        async def research_company(self, company_number: str):
            await asyncio.sleep(0.05)
            return await super().research_company(company_number)

    researcher = DelayResearcher()
    batch = asyncio.run(
        run_batch([num1, num2], researcher, max_concurrency=1, output_dir=tmp_path)
    )

    r1 = batch["results"][0]
    r2 = batch["results"][1]

    # Both companies should record active execution duration (~0.05s), neither should be cumulative (~0.10s)
    assert 0.03 <= r1["duration_seconds"] <= 0.09
    assert 0.03 <= r2["duration_seconds"] <= 0.09

    report = make_report(batch)
    # Average duration should be around 0.05s, not skewed by queue accumulation
    assert 0.03 <= report["average_duration_seconds"] <= 0.09


