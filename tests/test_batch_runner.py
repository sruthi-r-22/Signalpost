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
