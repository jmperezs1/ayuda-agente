"""Score a prompt version against real posts whose correct reading we already know."""

import json
from argparse import ArgumentParser
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ayudagente.radar.models import Observation
from ayudagente.radar.services.extraction import PROMPT_VERSION, Extractor

CASES_PATH = Path(settings.BASE_DIR) / "data" / "eval" / "extraction_cases.json"


class Command(BaseCommand):
    """
    Turn "the prompt seems better" into a number.

    Note:
        Reading a handful of outputs cannot tell a fix from a coincidence, and the failures
        worth catching are the quiet ones — a resource key drifting, an actor losing its name.
        Each case pins one behaviour that was seen to break, so a regression fails loudly.

        It calls the real model, so it is not part of `make check`.
    """

    help = "Run the extractor over the eval cases and report what passes."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the flags, all of which narrow a run rather than change what is asserted."""
        parser.add_argument("--model", help="Override the extraction model, to compare tiers.")
        parser.add_argument("--only", nargs="+", metavar="CASE_ID", help="Run these cases only.")
        parser.add_argument("--verbose-items", action="store_true", help="Print every item.")

    def handle(self, *args, **options) -> None:
        """
        Score every case and report the failures.

        Raises:
            CommandError: If the case file or a referenced observation is missing, since a
                silently skipped case would inflate the score.
        """
        if not CASES_PATH.exists():
            raise CommandError(f"no eval cases at {CASES_PATH}")
        spec = json.loads(CASES_PATH.read_text())
        cases = spec["cases"]
        if options["only"]:
            cases = [case for case in cases if case["id"] in set(options["only"])]

        extractor = Extractor(model=options["model"]) if options["model"] else Extractor()
        passed, failures, tokens = 0, [], {"input": 0, "output": 0}

        for case in cases:
            observation = Observation.objects.filter(
                platform=case["platform"], platform_id=case["platform_id"]
            ).first()
            if observation is None:
                raise CommandError(f"{case['id']}: no observation {case['platform_id']}, run seed")

            extraction = extractor.run(observation, force=True)
            tokens["input"] += extraction.input_tokens
            tokens["output"] += extraction.output_tokens
            problems = self._check(case["expect"], extraction.payload, extraction.classification)

            if problems:
                failures.append((case, problems))
                self.stdout.write(self.style.ERROR(f"  FAIL  {case['id']}"))
                for problem in problems:
                    self.stdout.write(f"          {problem}")
            else:
                passed += 1
                self.stdout.write(self.style.SUCCESS(f"  pass  {case['id']}"))

            if options["verbose_items"]:
                for item in extraction.payload["items"]:
                    self.stdout.write(
                        f"          [{item['direction']}] {item['resource_key']} "
                        f"q={item['quantity']} {item['unit']} actor={item['actor']['name']!r}"
                    )

        self._report(len(cases), passed, failures, tokens, extractor.model)

    def _check(self, expect: dict, payload: dict, classification: str) -> list[str]:
        """
        Compare one reading against what the case says must be true.

        Args:
            expect (dict): The case's assertions.
            payload (dict): The stored extraction payload.
            classification (str): The persisted class, which may differ from the raw payload
                when the reading was forced to `discard` for belonging to another event.

        Returns:
            list[str]: One line per broken assertion, empty when the case passes.
        """
        problems: list[str] = []
        items = payload.get("items", [])

        if "classification" in expect and classification != expect["classification"]:
            problems.append(
                f"classification: want {expect['classification']}, got {classification}"
            )
        if "classification_in" in expect and classification not in expect["classification_in"]:
            problems.append(
                f"classification: want one of {expect['classification_in']}, got {classification}"
            )
        if (
            "belongs_to_event" in expect
            and payload["belongs_to_event"] != expect["belongs_to_event"]
        ):
            problems.append(f"belongs_to_event: want {expect['belongs_to_event']}")
        if "min_items" in expect and len(items) < expect["min_items"]:
            problems.append(f"items: want at least {expect['min_items']}, got {len(items)}")
        if "max_items" in expect and len(items) > expect["max_items"]:
            problems.append(f"items: want at most {expect['max_items']}, got {len(items)}")
        if "geocode_contains" in expect:
            needle = expect["geocode_contains"].casefold()
            if needle not in payload.get("geocode_query", "").casefold():
                problems.append(
                    f"geocode_query: want {expect['geocode_contains']!r}, "
                    f"got {payload.get('geocode_query', '')!r}"
                )
        for wanted in expect.get("items_any", []):
            if not any(self._matches(item, wanted) for item in items):
                problems.append(f"no item matching {wanted}")
        return problems

    def _matches(self, item: dict, wanted: dict) -> bool:
        """Report whether one extracted item satisfies a partial description."""
        if "direction" in wanted and item["direction"] != wanted["direction"]:
            return False
        if "resource_key_in" in wanted and item["resource_key"] not in wanted["resource_key_in"]:
            return False
        if wanted.get("has_quantity") and item.get("quantity") is None:
            return False
        if "has_contact_kind" in wanted:
            kinds = {contact["kind"] for contact in item.get("contacts", [])}
            if wanted["has_contact_kind"] not in kinds:
                return False
        return True

    def _report(self, total: int, passed: int, failures: list, tokens: dict, model: str) -> None:
        """Print the score, the token spend and a reminder of what each failure was for."""
        score = f"{passed}/{total}"
        style = self.style.SUCCESS if passed == total else self.style.WARNING
        self.stdout.write(style(f"\n{score} cases pass  ·  prompt {PROMPT_VERSION}  ·  {model}"))
        self.stdout.write(
            f"tokens: {tokens['input']:,} in, {tokens['output']:,} out "
            f"({Decimal(tokens['input'] + tokens['output']) / max(total, 1):.0f} per case)"
        )
        if failures:
            self.stdout.write("\nwhat each failure was protecting:")
            for case, _ in failures:
                self.stdout.write(f"  {case['id']}: {case['why']}")
